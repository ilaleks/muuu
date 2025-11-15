from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
import gzip
import http.cookiejar
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional


BASE_URL = "https://mu.bless.gs/ru/index.php"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


@dataclass
class MarketItem:
    """Structured representation of a single market entry."""

    name: str
    price: Optional[str] = None
    seller: Optional[str] = None
    details: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return asdict(self)


class _Node:
    __slots__ = {"tag", "attrs", "children", "text_chunks", "parent"}

    def __init__(self, tag: Optional[str], attrs: Optional[Dict[str, str]] = None, parent: Optional["_Node"] = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: List[_Node] = []
        self.text_chunks: List[str] = []
        self.parent = parent

    def append_child(self, node: "_Node") -> None:
        self.children.append(node)

    def append_text(self, data: str) -> None:
        if data:
            self.text_chunks.append(data)

    def iter(self, tag: Optional[str] = None) -> Iterable["_Node"]:
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            yield from child.iter(tag)

    def get_text(self) -> str:
        parts: List[str] = []
        parts.extend(self.text_chunks)
        for child in self.children:
            text = child.get_text()
            if text:
                parts.append(text)
        return " ".join(chunk.strip() for chunk in parts if chunk.strip())


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self._stack: List[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attr_dict = {name: value or "" for name, value in attrs}
        node = _Node(tag, attr_dict, parent=self._stack[-1])
        self._stack[-1].append_child(node)
        self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self._stack[-1].append_text(data)


_TABLE_HEADER_KEYWORDS = {
    "предмет": "name",
    "название": "name",
    "item": "name",
    "вещь": "name",
    "цена": "price",
    "стоимость": "price",
    "price": "price",
    "продавец": "seller",
    "seller": "seller",
    "персонаж": "seller",
    "описание": "details",
    "детали": "details",
    "опции": "details",
}


_CARD_CLASS_KEYWORDS = {
    "market-item",
    "market__item",
    "item-box",
    "item_block",
    "item-box",
    "itemBox",
    "marketItem",
}


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _identify_header(header_text: str) -> Optional[str]:
    normalized = header_text.lower().strip()
    return _TABLE_HEADER_KEYWORDS.get(normalized)


def _parse_table_items(root: _Node) -> List[MarketItem]:
    items: List[MarketItem] = []
    for table in root.iter("table"):
        headers: List[str] = []
        header_keys: List[Optional[str]] = []
        for th in table.iter("th"):
            text = _normalize_text(th.get_text())
            if text:
                headers.append(text)
                header_keys.append(_identify_header(text))
        if not headers:
            continue
        # Collect row data
        for tr in table.iter("tr"):
            cells = [_normalize_text(td.get_text()) for td in tr.iter("td")]
            if len(cells) < 2:
                continue
            if len(cells) != len(headers):
                # Skip rows where structure does not match header definition
                continue
            mapped: Dict[str, str] = {}
            for key, cell in zip(header_keys, cells):
                if key:
                    mapped.setdefault(key, cell)
            name = mapped.get("name")
            if not name:
                continue
            items.append(
                MarketItem(
                    name=name,
                    price=mapped.get("price"),
                    seller=mapped.get("seller"),
                    details=mapped.get("details"),
                )
            )
    return items


def _class_tokens(node: _Node) -> List[str]:
    class_attr = node.attrs.get("class", "")
    return [token for token in class_attr.replace("\t", " ").replace("\n", " ").split(" ") if token]


def _parse_card_items(root: _Node) -> List[MarketItem]:
    items: List[MarketItem] = []
    candidate_nodes: List[_Node] = []
    for node in root.iter("div"):
        class_tokens = set(token.lower() for token in _class_tokens(node))
        if class_tokens & {token.lower() for token in _CARD_CLASS_KEYWORDS}:
            candidate_nodes.append(node)
    for node in candidate_nodes:
        text_by_hint: Dict[str, str] = {}
        for child in node.iter(None):
            if child is node:
                continue
            child_classes = {token.lower() for token in _class_tokens(child)}
            text = _normalize_text(child.get_text())
            if not text:
                continue
            if not text_by_hint.get("name") and any(
                any(keyword in class_name for keyword in {"title", "name"}) for class_name in child_classes
            ):
                text_by_hint["name"] = text
            elif not text_by_hint.get("price") and any(
                any(keyword in class_name for keyword in {"price", "cost"}) for class_name in child_classes
            ):
                text_by_hint["price"] = text
            elif not text_by_hint.get("seller") and any(
                any(keyword in class_name for keyword in {"seller", "author", "owner"}) for class_name in child_classes
            ):
                text_by_hint["seller"] = text
            elif not text_by_hint.get("details") and any(
                any(keyword in class_name for keyword in {"details", "description", "info"}) for class_name in child_classes
            ):
                text_by_hint["details"] = text
        if "name" not in text_by_hint:
            primary_text = _normalize_text(node.get_text())
            if primary_text:
                lines = [line.strip() for line in primary_text.split("\n") if line.strip()]
                if lines:
                    text_by_hint["name"] = lines[0]
                if len(lines) > 1 and "price" not in text_by_hint:
                    text_by_hint["price"] = lines[1]
        if "name" in text_by_hint:
            items.append(
                MarketItem(
                    name=text_by_hint.get("name", ""),
                    price=text_by_hint.get("price"),
                    seller=text_by_hint.get("seller"),
                    details=text_by_hint.get("details"),
                )
            )
    return items


def parse_market_items(html: str) -> List[MarketItem]:
    builder = _TreeBuilder()
    builder.feed(html)
    table_items = _parse_table_items(builder.root)
    card_items = _parse_card_items(builder.root)
    items = table_items + card_items
    unique: List[MarketItem] = []
    seen = set()
    for item in items:
        key = (item.name, item.price, item.seller, item.details)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _build_opener() -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def fetch_market_html(server: str, user_agent: str = DEFAULT_USER_AGENT, cookie: Optional[str] = None) -> str:
    params = urllib.parse.urlencode({"page": "market", "serv": server})
    url = f"{BASE_URL}?{params}"
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://mu.bless.gs/ru/index.php?page=market",
        "Connection": "keep-alive",
    }
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)
    opener = _build_opener()
    with opener.open(request) as response:
        raw = response.read()
        encoding = response.headers.get_content_charset() or "utf-8"
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode(encoding, "ignore")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape MU Bless market listings.")
    parser.add_argument("--server", default="server4", help="Server name, e.g. server4")
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="Override the User-Agent header used for the HTTP request.",
    )
    parser.add_argument(
        "--cookie",
        dest="cookie",
        help="Optional Cookie header value copied from a logged-in browser session.",
    )
    parser.add_argument(
        "--from-file",
        dest="from_file",
        help="Parse market listings from a local HTML file instead of downloading.",
    )
    parser.add_argument(
        "--output",
        choices={"json", "text"},
        default="json",
        help="Output format",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        if args.from_file:
            with open(args.from_file, "r", encoding="utf-8") as handle:
                html = handle.read()
        else:
            html = fetch_market_html(args.server, user_agent=args.user_agent, cookie=args.cookie)
    except Exception as exc:  # pragma: no cover - network errors are reported to the user
        print(f"Failed to load market page: {exc}", file=sys.stderr)
        return 1

    items = parse_market_items(html)
    if args.output == "json":
        print(json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2))
    else:
        for item in items:
            print(f"Название: {item.name}")
            if item.price:
                print(f"  Цена: {item.price}")
            if item.seller:
                print(f"  Продавец: {item.seller}")
            if item.details:
                print(f"  Описание: {item.details}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

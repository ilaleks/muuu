# muuu

Инструмент для парсинга рыночных лотов с сайта [MU Bless](https://mu.bless.gs/ru/index.php?page=market&serv=server4).

## Установка

Зависимостей, кроме стандартной библиотеки Python 3.11+, не требуется.

## Использование

```bash
python market_scraper.py --server server4
```

Скрипт скачает HTML страницы рынка и выведет найденные предметы в формате JSON. Доступные параметры:

- `--server` — имя сервера (по умолчанию `server4`).
- `--from-file` — путь до локального HTML-файла (для оффлайн-тестирования парсера без сети).
- `--output` — формат вывода (`json` или `text`).

Пример оффлайн-запуска на тестовом HTML:

```bash
python market_scraper.py --from-file samples/market_sample.html --output text
```

## Тестовый пример

В каталоге [`samples`](samples) лежит файл [`market_sample.html`](samples/market_sample.html) с упрощённой разметкой, по которой можно проверить работу парсера без доступа к сайту.

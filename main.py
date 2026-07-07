"""
Park Sovinon Analytics Agent — Railway cron worker.

Собирает данные по ЖК «Парк Совиньон» (Одесса): сайт застройщика, DIM.RIA,
рынок/конкуренты/отзывы через веб-поиск Claude, формирует сводку
и отправляет в Telegram.

Env vars (Railway → Variables):
  ANTHROPIC_API_KEY   — ключ API Anthropic (console.anthropic.com)
  TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
  TELEGRAM_CHAT_ID    — ваш chat_id
  CLAUDE_MODEL        — опционально, по умолчанию claude-sonnet-5
"""

import os
import sys
import datetime

import httpx
import anthropic

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

SOURCES = {
    "Сайт ЖК (главная)": "https://park-sovinon.ua/ru/",
    "Сайт ЖК (новости)": "https://park-sovinon.ua/ru/novosti/",
    "DIM.RIA (цены и статус)": "https://dom.ria.com/novostroyka-zhk-park-sovynon-5626/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "ru,uk;q=0.9",
}


def fetch_pages() -> str:
    """Скачивает ключевые страницы, возвращает текст для контекста Claude."""
    chunks = []
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for name, url in SOURCES.items():
            try:
                r = client.get(url)
                r.raise_for_status()
                # Грубая чистка HTML: убираем скрипты/стили, оставляем текст.
                import re
                text = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ",
                              r.text, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                chunks.append(f"===== {name} ({url}) =====\n{text[:20000]}")
            except Exception as e:
                chunks.append(f"===== {name} ({url}) =====\nОШИБКА ЗАГРУЗКИ: {e}")
    return "\n\n".join(chunks)


def build_report(pages_text: str) -> str:
    """Запускает Claude с веб-поиском, возвращает markdown-сводку."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today = datetime.date.today().isoformat()

    prompt = f"""Ты — аналитик рынка недвижимости. Сегодня {today}.
Подготовь еженедельную сводку по ЖК «Парк Совиньон» (Одесса, Таирово, застройщик ЗАРС)
для отдела аналитики и маркетинга.

Ниже — свежескачанный текст ключевых страниц (сайт ЖК и DIM.RIA). Используй его как
первоисточник цен, акций и статуса строительства. Дополнительно сделай веб-поиск:
1) «рынок недвижимости Одесса цены новостройки» за текущий месяц — средняя цена м², динамика, спрос;
2) «новостройки Таирово Одесса бизнес-класс цены акции» — конкуренты (КП Светлый Совиньон,
   КГ Family Home, ЖК Эллада, Aqua Marine и др.);
3) «Парк Совиньон отзывы», «ЗАРС новости» — новые отзывы и репутационные события.

Формат ответа — компактный отчёт в Markdown (без преамбулы, сразу отчёт):
# Парк Совиньон — сводка {today}
## Цены и статус (таблица: 1к/2к/3к, $ за м², статус Дома 8)
## Акции объекта
## Конкуренты (таблица: объект — цена — акция)
## Рынок Одессы
## Отзывы и репутация
## Выводы для маркетинга (3-5 пунктов)
## Источники (список ссылок)

Правила: цены в $ за м²; только факты из источников с датами; не выдумывать данные;
если данных нет — так и написать.

СКАЧАННЫЕ СТРАНИЦЫ:
{pages_text}"""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def send_to_telegram(report: str) -> None:
    """Шлёт отчёт: короткое сообщение + полный файл .md."""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    today = datetime.date.today().isoformat()

    with httpx.Client(timeout=30) as client:
        # Полный отчёт файлом
        client.post(
            f"{api}/sendDocument",
            data={"chat_id": TELEGRAM_CHAT_ID,
                  "caption": f"Парк Совиньон — сводка {today}"},
            files={"document": (f"park_sovinon_{today}.md",
                                report.encode("utf-8"), "text/markdown")},
        ).raise_for_status()

        # Текст сообщением (Telegram лимит 4096 символов)
        for i in range(0, min(len(report), 12000), 4000):
            client.post(
                f"{api}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": report[i:i + 4000]},
            ).raise_for_status()


def main() -> None:
    print("Fetching source pages...")
    pages = fetch_pages()
    print(f"Fetched {len(pages)} chars. Building report via Claude ({MODEL})...")
    report = build_report(pages)
    print(f"Report ready ({len(report)} chars). Sending to Telegram...")
    send_to_telegram(report)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # При ошибке — уведомление в Telegram, чтобы сбой не прошёл незамеченным
        try:
            httpx.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID,
                      "text": f"⚠️ Park Sovinon agent: ошибка запуска — {e}"},
                timeout=15,
            )
        except Exception:
            pass
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

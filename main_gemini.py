"""
Park Sovinon Analytics Agent — Railway cron worker (БЕСПЛАТНАЯ версия на Gemini API).

Собирает данные по ЖК «Парк Совиньон» (Одесса): сайт застройщика, DIM.RIA,
рынок/конкуренты/отзывы через поиск Google (grounding), формирует сводку
и отправляет в Telegram.

Env vars (Railway → Variables):
  GEMINI_API_KEY      — бесплатный ключ из Google AI Studio (aistudio.google.com)
  TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
  TELEGRAM_CHAT_ID    — ваш chat_id
  GEMINI_MODEL        — опционально, по умолчанию gemini-2.5-flash (бесплатный tier)
"""

import os
import re
import sys
import datetime

import httpx
from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

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
    """Скачивает ключевые страницы, возвращает текст для контекста модели."""
    chunks = []
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for name, url in SOURCES.items():
            try:
                r = client.get(url)
                r.raise_for_status()
                text = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ",
                              r.text, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                chunks.append(f"===== {name} ({url}) =====\n{text[:20000]}")
            except Exception as e:
                chunks.append(f"===== {name} ({url}) =====\nОШИБКА ЗАГРУЗКИ: {e}")
    return "\n\n".join(chunks)


def build_report(pages_text: str) -> str:
    """Запускает Gemini с поиском Google, возвращает markdown-сводку."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    today = datetime.date.today().isoformat()

    prompt = f"""Ты — аналитик рынка недвижимости. Сегодня {today}.
Подготовь еженедельную сводку по ЖК «Парк Совиньон» (Одесса, Таирово, застройщик ЗАРС)
для отдела аналитики и маркетинга. Отвечай на русском.

Ниже — свежескачанный текст ключевых страниц (сайт ЖК и DIM.RIA). Используй его как
первоисточник цен, акций и статуса строительства. Дополнительно найди через поиск:
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

    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=4000,
        ),
    )
    return resp.text or "Пустой ответ модели."


def send_to_telegram(report: str) -> None:
    """Шлёт отчёт: полный файл .md + текст сообщениями."""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    today = datetime.date.today().isoformat()

    with httpx.Client(timeout=30) as client:
        client.post(
            f"{api}/sendDocument",
            data={"chat_id": TELEGRAM_CHAT_ID,
                  "caption": f"Парк Совиньон — сводка {today}"},
            files={"document": (f"park_sovinon_{today}.md",
                                report.encode("utf-8"), "text/markdown")},
        ).raise_for_status()

        for i in range(0, min(len(report), 12000), 4000):
            client.post(
                f"{api}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": report[i:i + 4000]},
            ).raise_for_status()


def main() -> None:
    print("Fetching source pages...")
    pages = fetch_pages()
    print(f"Fetched {len(pages)} chars. Building report via Gemini ({MODEL})...")
    report = build_report(pages)
    print(f"Report ready ({len(report)} chars). Sending to Telegram...")
    send_to_telegram(report)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
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

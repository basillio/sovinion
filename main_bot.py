"""
Park Sovinon Analytics Bot — интерактивный Telegram-бот (Gemini, бесплатный tier).

Работает 24/7 на Railway (worker, long polling). Отвечает по запросу:
  /report  — полная сводка (цены, конкуренты, рынок, отзывы)
  /prices  — быстро: актуальные цены и акции
  /news    — новости объекта и рынка за неделю
  любой текст — свободный вопрос по объекту/рынку (Gemini + поиск Google)
Плюс сам присылает полную сводку раз в неделю (понедельник 09:00 Киев).

Env vars (Railway → Variables):
  GEMINI_API_KEY      — бесплатный ключ из Google AI Studio
  TELEGRAM_BOT_TOKEN  — токен бота от @BotFather
  TELEGRAM_CHAT_ID    — ваш chat_id (или несколько через запятую) — только эти
                        чаты получат ответы (защита от чужих)
  GEMINI_MODEL        — опционально, по умолчанию gemini-2.5-flash
  WEEKLY_DAY / WEEKLY_HOUR — опционально: день (0=пн) и час (Киев) авто-сводки,
                        по умолчанию 0 и 9. WEEKLY_DAY=-1 — отключить.
"""

import os
import re
import sys
import json
import time
import datetime
import traceback

import httpx
from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHATS = {c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",")}
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
WEEKLY_DAY = int(os.environ.get("WEEKLY_DAY", "0"))    # 0 = понедельник
WEEKLY_HOUR = int(os.environ.get("WEEKLY_HOUR", "9"))  # час по Киеву
KYIV_UTC_OFFSET = 3  # летом UTC+3, зимой UTC+2 — при желании поменяйте

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

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

BASE_CONTEXT = """Ты — аналитик рынка недвижимости, ассистент по объекту
ЖК «Парк Совиньон» (Одесса, Таирово, ул. Трамвайная 31, застройщик — Холдинг ЗАРС,
бизнес-класс, концепция «город-парк», сайт https://park-sovinon.ua/ru/,
карточка цен: https://dom.ria.com/novostroyka-zhk-park-sovynon-5626/).
Отвечай на русском, кратко и по делу, цены указывай в $ за м², факты — только из
источников с датами, не выдумывай. В конце ответа — список ссылок-источников."""

KEYBOARD = json.dumps({
    "keyboard": [
        [{"text": "📊 Полная сводка"}, {"text": "💰 Цены и акции"}],
        [{"text": "🏘 Конкуренты"}, {"text": "📰 Новости"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
})

BUTTONS = {
    "📊 Полная сводка": "/report",
    "💰 Цены и акции": "/prices",
    "🏘 Конкуренты": "/competitors",
    "📰 Новости": "/news",
}

HELP_TEXT = (
    "Бот-аналитик ЖК «Парк Совиньон» (Одесса).\n\n"
    "Пользуйтесь кнопками внизу:\n"
    "📊 Полная сводка — цены, конкуренты, рынок, отзывы (файл + текст)\n"
    "💰 Цены и акции — актуальные цены за м² и условия\n"
    "🏘 Конкуренты — сравнение с соседними ЖК\n"
    "📰 Новости — события за неделю\n\n"
    "Или просто задайте вопрос текстом, например:\n"
    "«сколько стоит двушка?», «что у конкурентов в Аркадии?», "
    "«какие акции сейчас у застройщика?»"
)

# ---------- Данные ----------

_pages_cache = {"text": "", "ts": 0.0}

def fetch_pages(max_age_sec: int = 3600) -> str:
    """Скачивает ключевые страницы (кэш 1 час, чтобы не дёргать сайты на каждый вопрос)."""
    if _pages_cache["text"] and time.time() - _pages_cache["ts"] < max_age_sec:
        return _pages_cache["text"]
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
    _pages_cache["text"] = "\n\n".join(chunks)
    _pages_cache["ts"] = time.time()
    return _pages_cache["text"]


def ask_gemini(task: str, with_pages: bool = True) -> str:
    """Запрос к Gemini с поиском Google и (опц.) свежескачанными страницами."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    today = datetime.date.today().isoformat()
    prompt = f"{BASE_CONTEXT}\nСегодня {today}.\n\nЗАДАЧА:\n{task}"
    if with_pages:
        prompt += f"\n\nСВЕЖЕСКАЧАННЫЕ СТРАНИЦЫ (первоисточник цен/акций/статуса):\n{fetch_pages()}"
    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=4000,
        ),
    )
    return resp.text or "Пустой ответ модели, попробуйте ещё раз."


def full_report() -> str:
    today = datetime.date.today().isoformat()
    return ask_gemini(f"""Подготовь сводку в Markdown (без преамбулы):
# Парк Совиньон — сводка {today}
## Цены и статус (таблица: 1к/2к/3к, $ за м², статус Дома 8)
## Акции объекта
## Конкуренты (таблица: объект — цена — акция; поиск: новостройки Таирово/Одесса бизнес-класс)
## Рынок Одессы (средняя цена м², динамика, спрос — поиск за текущий месяц)
## Отзывы и репутация (поиск: Парк Совиньон отзывы, ЗАРС новости)
## Выводы для маркетинга (3-5 пунктов)
## Источники""")


# ---------- Telegram ----------

def tg(method: str, **kwargs) -> dict:
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{API}/{method}", data=kwargs)
        r.raise_for_status()
        return r.json()


def send_text(chat_id: str, text: str) -> None:
    for i in range(0, max(len(text), 1), 4000):
        tg("sendMessage", chat_id=chat_id, text=text[i:i + 4000],
           reply_markup=KEYBOARD)


def send_report(chat_id: str, report: str) -> None:
    today = datetime.date.today().isoformat()
    with httpx.Client(timeout=60) as client:
        client.post(f"{API}/sendDocument",
                    data={"chat_id": chat_id,
                          "caption": f"Парк Совиньон — сводка {today}"},
                    files={"document": (f"park_sovinon_{today}.md",
                                        report.encode("utf-8"), "text/markdown")})
    send_text(chat_id, report[:8000])


def handle_message(chat_id: str, text: str) -> None:
    text = BUTTONS.get(text.strip(), text.strip())  # кнопки → команды
    if text in ("/start", "/help"):
        send_text(chat_id, HELP_TEXT)
        return
    send_text(chat_id, "Собираю данные, это займёт ~1 минуту...")
    if text == "/report":
        send_report(chat_id, full_report())
    elif text == "/prices":
        send_text(chat_id, ask_gemini(
            "Кратко: актуальные цены за м² по типам квартир (1к/2к/3к), минимальные "
            "цены квартир, текущие акции и условия рассрочки/єОселя. Таблицей."))
    elif text == "/competitors":
        send_text(chat_id, ask_gemini(
            "Найди актуальные цены и акции конкурентов: КП Светлый Совиньон, "
            "КГ Family Home, ЖК Вільний Совіньон (Таирово) и бизнес-класс у моря "
            "(ЖК Эллада, Aqua Marine, Акрополь, Пространство на Морском). "
            "Сравни с ценами Парк Совиньон. Таблицей: объект — $ за м² — акция."))
    elif text == "/news":
        send_text(chat_id, ask_gemini(
            "Найди новости за последнюю неделю: по ЖК Парк Совиньон, застройщику ЗАРС "
            "и рынку недвижимости Одессы. Кратким списком с датами и ссылками."))
    else:
        send_text(chat_id, ask_gemini(f"Вопрос пользователя: {text}"))


# ---------- Еженедельная авто-сводка ----------

_last_weekly_sent: str = ""

def maybe_send_weekly() -> None:
    global _last_weekly_sent
    if WEEKLY_DAY < 0:
        return
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=KYIV_UTC_OFFSET)
    stamp = now.strftime("%Y-%m-%d")
    if now.weekday() == WEEKLY_DAY and now.hour >= WEEKLY_HOUR and _last_weekly_sent != stamp:
        _last_weekly_sent = stamp
        report = full_report()
        for chat in ALLOWED_CHATS:
            send_report(chat, report)


# ---------- Main loop ----------

def main() -> None:
    print(f"Bot started. Model={MODEL}, allowed chats={ALLOWED_CHATS}")
    offset = 0
    while True:
        try:
            maybe_send_weekly()
            updates = tg("getUpdates", offset=offset, timeout=50)
            for u in updates.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if not text:
                    continue
                if chat_id not in ALLOWED_CHATS:
                    send_text(chat_id, "Извините, это приватный бот.")
                    continue
                try:
                    handle_message(chat_id, text)
                except Exception as e:
                    send_text(chat_id, f"⚠️ Ошибка: {e}")
                    traceback.print_exc()
        except Exception as e:
            print(f"Loop error: {e}", file=sys.stderr)
            time.sleep(10)


if __name__ == "__main__":
    main()

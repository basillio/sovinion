# Деплой агента «Парк Совиньон» на Railway

## ДВЕ ВЕРСИИ АГЕНТА
- **Бесплатная (рекомендуется): Gemini API** — файлы `main_gemini.py` + `requirements_gemini.txt`.
  Ключ бесплатно в Google AI Studio (https://aistudio.google.com → Get API key, карта не нужна).
  Лимиты free tier (модели Flash, до 1 500 запросов/день) для 1 запуска в неделю более чем достаточны.
  Переменные: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
  Для деплоя переименуйте `main_gemini.py` → `main.py` и `requirements_gemini.txt` → `requirements.txt`.
- **Платная: Claude API (Anthropic)** — файлы `main.py` + `requirements.txt`, инструкция ниже.

Остаётся расход только на Railway (~$5/мес Hobby). Совсем бесплатная альтернатива хостингу —
GitHub Actions: тот же скрипт можно запускать по cron в бесплатном воркфлоу (скажите — сделаю yml).

---

Агент раз в неделю собирает данные (сайт ЖК, DIM.RIA, веб-поиск через Claude API),
формирует сводку и шлёт её в Telegram — сообщением и файлом .md.

## Файлы проекта
```
main.py           — код агента
requirements.txt  — зависимости
railway.json      — конфиг Railway (cron: понедельник 06:00 UTC = 09:00 Киев)
```

## Что понадобится
1. **Аккаунт Railway** — railway.com (план Hobby ~$5/мес).
2. **API-ключ Anthropic** — console.anthropic.com → API Keys. Пополните баланс
   (запуск стоит примерно $0.05–0.20: модель + веб-поиск).
3. **Токен Telegram-бота** — у вас уже есть (от @BotFather).
4. **Ваш chat_id** — отправьте боту любое сообщение, затем откройте в браузере:
   `https://api.telegram.org/bot<ТОКЕН>/getUpdates`
   и найдите `"chat":{"id": ЧИСЛО}` — это и есть chat_id.

## Деплой (через GitHub — проще всего)
1. Создайте репозиторий на GitHub и положите туда три файла:
   `main.py`, `requirements.txt`, `railway.json`.
2. На railway.com: **New Project → Deploy from GitHub repo** → выберите репозиторий.
3. В сервисе откройте **Variables** и добавьте:
   - `ANTHROPIC_API_KEY` = ваш ключ
   - `TELEGRAM_BOT_TOKEN` = токен бота
   - `TELEGRAM_CHAT_ID` = ваш chat_id
4. Railway прочитает `railway.json`: cron `0 6 * * 1` (каждый понедельник 06:00 UTC).
   Изменить расписание можно в **Settings → Cron Schedule**.
5. Проверка: в сервисе нажмите **Deploy / Run** (или временно поставьте cron на
   ближайшую минуту) — в Telegram должна прийти сводка. Логи — во вкладке Deployments.

## Альтернатива: Railway CLI
```
npm i -g @railway/cli
railway login
railway init          # в папке с файлами
railway up
railway variables set ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
```

## Примечания
- Cron в Railway задаётся в **UTC**: 06:00 UTC = 09:00 по Киеву (летом).
- `restartPolicyType: NEVER` — обязательно для cron-задач (иначе скрипт будет перезапускаться).
- При ошибке агент сам пришлёт в Telegram сообщение «⚠️ ошибка запуска».
- Модель по умолчанию `claude-sonnet-5`; можно заменить переменной `CLAUDE_MODEL`
  (например, `claude-haiku-4-5-20251001` — дешевле).
- Если DIM.RIA начнёт блокировать запросы с серверных IP, сводка всё равно соберётся
  за счёт веб-поиска Claude (ошибка загрузки страницы передаётся модели, и она
  компенсирует поиском).

# Telegram Bot Design — SIT RBS Room Checker

**Date:** 2026-03-30
**Status:** Approved

---

## Overview

A Telegram bot that lets SIT students check room availability on the RBS system without installing the exe. Runs on the developer's own PC alongside the existing Flask web app. Uses session cookies instead of credentials — no passwords ever touch the bot.

---

## Repository Structure

```
rbs-checker/
├── web-app/
│   ├── app.py              # Flask web app (moved from root)
│   ├── build.bat
│   ├── start.bat
│   └── rbs-checker.spec
├── bot/
│   ├── bot.py              # Telegram bot
│   ├── start-bot.bat       # Double-click to launch bot
│   └── cookies.json        # Persisted user cookies (gitignored)
├── shared/
│   └── scraper.py          # Room-checking logic shared by both
├── bookmarklet.js          # One-click cookie extractor
├── requirements.txt        # Shared dependencies
└── README.md
```

---

## Shared Scraper (`shared/scraper.py`)

Extracts room-checking logic from `app.py` into two public entry points:

```
check_rooms(username, password, date, start, end, log_fn)
    → used by web-app/app.py

check_rooms_with_cookie(cookies, date, start, end, log_fn)
    → used by bot/bot.py

Both call:
    _do_check(page, date, start, end, log_fn)
    → shared room-checking logic (search, collect cards, fetch slots)
```

`app.py` and `bot.py` add the repo root to `sys.path` to import `shared.scraper`.

---

## Cookie Extraction (Bookmarklet)

A JavaScript bookmarklet users drag to their bookmarks bar. When clicked while logged into RBS, it:
1. Reads all cookies for the RBS domain
2. Copies them to clipboard as a JSON string
3. Shows a brief "Cookie copied ✅" alert

No DevTools required. Setup is one-time (drag to bookmarks bar). Use is: log in → click bookmark → paste into bot.

---

## Bot Conversation Flow

### Commands
| Command | Description |
|---|---|
| `/start` | Welcome message + one-time bookmarklet setup guide |
| `/check` | Start a room availability check |
| `/cookie` | Manually refresh saved cookie |

### `/check` flow

1. **Date selection** — inline keyboard:
   `[ Today ] [ Tomorrow ] [ Pick a date ]`
   - "Pick a date" prompts user to type a date (e.g. `2026-04-01`)

2. **Start time selection** — inline keyboard grid (07:00–21:30 in 30-min slots)

3. **End time selection** — inline keyboard grid showing only times after selected start

4. **Cookie check (smart reuse):**
   - Saved cookie exists → try it silently
   - Cookie valid → proceed to scrape
   - Cookie expired/missing → send bookmarklet instructions, ask user to paste cookie

5. **Scraping** — bot sends progress updates as the scraper runs

6. **Results** — formatted message:
   ```
   ✅ Fully Available (N)
   • Room Name

   🟡 Partially Available (N)
   • Room Name — X/Y slots free

   ❌ Fully Booked (N)
   • Room Name
   ```

### Cookie storage
- Stored in `bot/cookies.json` keyed by Telegram user ID
- Persists across bot restarts
- `cookies.json` is gitignored (never committed)

---

## Concurrency

The bot processes one scrape at a time using a `asyncio.Lock`. If a second user sends `/check` while a scrape is running, they receive:
> "⏳ Another check is running, you're next. Please wait..."

Their request is queued and runs immediately after.

---

## `start-bot.bat`

```bat
pip install -r requirements.txt -q
python bot/bot.py
pause
```

Users double-click to launch. Bot token is read from a `BOT_TOKEN` environment variable or a `.env` file in the `bot/` folder.

---

## Dependencies Added

- `python-telegram-bot>=20.0` (async, v20+ API)
- `python-dotenv` (for loading BOT_TOKEN from `.env`)

---

## Security Notes

- `bot/cookies.json` and `bot/.env` are gitignored — never committed
- Cookies are stored per Telegram user ID, not shared between users
- No SIT passwords are ever sent to or stored by the bot

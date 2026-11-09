# SIT RBS Room Checker

Check available Discussion Rooms on SIT's Resource Booking System — no installation required.

## Download & Run (Windows)

1. Go to the [Releases](https://github.com/Qayzan/rbs-checker/releases/latest) page
2. Download `rbs-checker.zip`
3. Unzip and double-click `rbs-checker.exe`
4. Your browser will open automatically at http://localhost:5000

> **First run:** the app will download Chromium (~130 MB) automatically. This only happens once.

## Telegram Bot

An alternative interface via Telegram — no install needed.

### Setup (one-time)

1. Message [@BotFather](https://t.me/BotFather) on Telegram and create a bot to get a token
2. Copy `bot/.env.example` to `bot/.env` and fill in your token
3. Double-click `bot/start-bot.bat`

### Usage

1. Send `/login` and enter your SIT credentials — the bot deletes your password message and only keeps the session
2. Tap **🔍 Check rooms** (or send `/check`), then pick a day, a building, and a time window — or a quick option like *Next 2h* / *Whole day*
3. Rooms appear in the chat as they're found; use the **≥1h / ≥2h** buttons to hide short gaps

Picking one building is ~3× faster than *Any building* (RBS answers one room at a time, ~1.4 s each).
Results are shared for 2 minutes, so a friend asking the same thing gets an instant answer.

### More ways to search

- `/repeat` or **Repeat search** repeats your last date and time window. If it has passed, start a new search.
- `/find 2026-11-10 12:00 18:00 120 E2` finds rooms with a continuous two-hour gap between noon and 6pm, ranked by earliest start. All dates and times use Singapore time.
- Add a final match limit, e.g. `/find 2026-11-10 12:00 18:00 120 E2 3`, to stop after three suitable rooms. This checks fewer rooms; it does not guarantee the earliest match across every room.
- Under a result, **Find a 1h/2h gap** instantly filters the existing results for that duration, retaining their original age. **Check again** fetches fresh availability. **First 3 matching rooms** runs a faster, limited search.
- **Save these preferences** remembers the building and duration. `/find` can omit the duration and building to use them. `/preferences` shows them.
- `/cancel` stops a queued or running search. A new search also stops your previous check.
- `/status` verifies the saved session with SIT before reporting logged in. Confirmed expired sessions are removed; network failures report an unverified session and keep it for a later retry. Verification runs in the background, waits for ongoing checks, then allows up to 35 seconds to contact SIT. `/cancel` also stops a pending status check.
- Failed room lookups appear as **Could not check**, separately from booked rooms. Incomplete or failed checks are not shared through the cache.
- Result buttons apply to their original search. The latest 20 result records remain available until the bot restarts.
- Back buttons let you revise the date, building, and start time. Long result lists use Previous/Next pages in one message.
- Identical full searches already running share one lookup. Cancelling one viewer leaves it running for others; cancelling the last viewer stops the lookup. Limited searches remain independent.
- Temporary room lookup failures are retried once after a short pause. Searches have a three-minute overall limit.
- Authenticated browser sessions stay warm for two minutes between checks, isolated by login session. Every RBS search reloads its form to reset page controls. Idle pages are bounded to eight and are released on logout, error, or bot shutdown.
- Recent room slots can serve overlapping time windows for up to two minutes. Every required slot must be present and fresh; the displayed age reflects the oldest reused data. **Check again** bypasses availability caches.
- `/favourites ROOM-NAME ROOM-NAME` puts up to ten preferred rooms first, especially useful in limited mode. Use exact names from results; `/favourites clear` resets the list.
- Timing logs report queue wait, page setup/reuse, room lookup time, and slot-cache hits without credentials. These measurements can be used to compare real searches; no live speedup has been benchmarked yet.

### Availability alerts

Tap **Watch for a match** under a result to save one alert per person (a new alert replaces the previous one). The bot checks every five minutes, sends one notification when a suitable room is found, then stops. Alerts expire after 24 hours or the latest possible start for the requested duration, whichever comes first.

Use `/alerts` to view your alert and `/unwatch` to cancel it. `/logout` also removes your alert. Alerts survive a restart, but checks only run while the bot is running and your SIT session is valid. Availability may change before you book on RBS.

Stopping or replacing an alert also cancels its running lookup. Starting a manual search interrupts your active alert check so it can use your session first; the saved alert remains scheduled.

Due alerts with overlapping windows in the same building and day fetch a covering window, so subsequent alerts can reuse the individual room slots. Matches are restricted to each person's own requested window.

### Keep it running

- `bot/start-bot.bat` restarts the bot automatically if it crashes
- `bot/autostart-on.bat` makes it start (minimised) whenever you log in to Windows; `bot/autostart-off.bat` undoes that
- The bot only answers while your PC is on and awake

## Usage (Web App)

1. Enter your SIT credentials
2. Pick a date and time range
3. Click **Check Availability**
4. Results show:
   - ✅ Rooms fully available for the whole window
   - 🟡 Rooms partially available (with individual slot breakdown)
   - ❌ Rooms fully booked

## Account format

| Account type | Format |
|---|---|
| Student | `STUDENTID@sit.singaporetech.edu.sg` |
| Staff | `username@singaporetech.edu.sg` |

## Run from source

Requires Python 3.10+.

```bash
pip install -r requirements.txt
playwright install chromium
python web-app/app.py
```

## Build the exe yourself

```bash
web-app/build.bat
```

Output will be in `web-app/dist\rbs-checker\` — zip the folder and share it.

---

> **Privacy:** the web app passes your credentials to SIT through a local automated browser. With the Telegram bot, credentials pass through Telegram and the computer running the bot before reaching SIT. Login is restricted to private chats. The bot attempts to delete credential messages immediately and saves only session cookies locally, not passwords. Message deletion can fail. Saved sessions, preferences, and alerts are local files excluded from Git.

### Development checks

Run `.venv\Scripts\python.exe -m unittest discover -s tests -v` after installing the requirements in a local environment. The tests simulate Telegram and SIT responses; they do not send real messages or log into SIT.

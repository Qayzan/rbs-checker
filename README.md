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

1. Send `/check` to the bot
2. Select a date and time range using the buttons
3. Paste your RBS session cookie when prompted (see below)
4. Results appear in the chat

### Getting your session cookie

**Option A — Bookmarklet:**
1. Add the bookmarklet from `bookmarklet.js` to your browser
2. Log into RBS, click the bookmarklet → cookie is copied to clipboard
3. Paste into the bot

**Option B — DevTools (always works):**
1. Log into RBS and press `F12`
2. Click **Network** tab → press `F5` to reload
3. Click any request → scroll to **Request Headers** → find `Cookie:`
4. Copy the full value and paste into the bot

Your cookie is saved locally — you only need to do this once per session.

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

> **Privacy:** your credentials are typed directly into SIT's login page via a local automated browser. They are never sent to any external server. The Telegram bot stores only your session cookie locally — never your password.

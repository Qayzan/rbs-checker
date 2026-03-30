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

1. Send `/login` and enter your SIT credentials — the bot handles authentication automatically
2. Send `/check`, select a date and time range using the buttons
3. Results appear in the chat

Your session is saved locally — you only need to `/login` again when it expires.

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

> **Privacy:** your credentials are typed directly into SIT's login page via a local automated browser. They are never sent to any external server. The Telegram bot stores only your session cookie locally — never your password. Password messages are deleted from chat immediately after login.

# SIT RBS Room Checker

Check available Discussion Rooms on SIT's Resource Booking System — no installation required.

## Download & Run (Windows)

1. Go to the [Releases](../../releases/latest) page
2. Download `rbs-checker.zip`
3. Unzip and double-click `rbs-checker.exe`
4. Your browser will open automatically at http://localhost:5000

> **First run:** the app will download Chromium (~130 MB) automatically. This only happens once.

## Usage

1. Enter your SIT credentials
2. Pick a date and time range
3. Click **Check Availability**
4. Results show:
   - ✅ Rooms fully available for the whole window
   - 🟡 Rooms partially available (with individual slot breakdown)
   - ❌ Rooms fully booked

## Account format
- Students: `STUDENTID@sit.singaporetech.edu.sg`
- Staff: `username@singaporetech.edu.sg`

## Run from source

1. Install Python from https://python.org
2. Install dependencies:
   ```
   pip install flask playwright
   playwright install chromium
   ```
3. Run:
   ```
   python app.py
   ```

## Build the exe yourself

```
build.bat
```

Output will be in `dist\rbs-checker\` — zip and share.

---

> Your credentials are entered directly into SIT's login page via an automated browser running locally on your machine. They are never sent to any external server.

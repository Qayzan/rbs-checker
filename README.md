# SIT RBS Room Checker

Check available Discussion Rooms on SIT's Resource Booking System — no installation required.

## Download & Run (Windows)

1. Go to the [Releases](https://github.com/Qayzan/rbs-checker/releases/latest) page
2. Download `rbs-checker.zip`
3. Unzip and double-click `rbs-checker.exe`
4. Your browser will open automatically at http://localhost:5000

> **First run:** the app will download Chromium (~130 MB) automatically. This only happens once.

## Usage

1. Enter your SIT credentials
2. Pick a date and time range
3. Click **Check Availability**
4. Results show:
   - ✅ Fully available — free for the entire time window
   - 🟡 Partially available — some slots free, some taken
   - ❌ Fully booked

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
python app.py
```

## Build the exe yourself

```bash
build.bat
```

Output will be in `dist\rbs-checker\` — zip the folder and share it.

---

> **Privacy:** your credentials are typed directly into SIT's login page via a local automated browser. They are never sent to any external server.

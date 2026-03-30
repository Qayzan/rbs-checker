# Telegram Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Telegram bot that checks SIT RBS room availability using session cookies, while keeping the existing Flask web app intact.

**Architecture:** Extract shared scraping logic into `shared/scraper.py`, move the web app into `web-app/`, and build `bot/bot.py` using python-telegram-bot v20+. Both tools import from `shared.scraper` — one uses username/password login, the other uses browser session cookies.

**Tech Stack:** python-telegram-bot>=20.0, python-dotenv, Playwright (sync API), Flask (unchanged)

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Move | `app.py` → `web-app/app.py` | Flask web app |
| Move | `build.bat` → `web-app/build.bat` | PyInstaller build script |
| Move | `start.bat` → `web-app/start.bat` | Run-from-source launcher |
| Move | `rbs-checker.spec` → `web-app/rbs-checker.spec` | PyInstaller spec |
| Create | `shared/__init__.py` | Makes shared a package |
| Create | `shared/scraper.py` | Room-checking logic shared by both tools |
| Create | `bot/bot.py` | Telegram bot |
| Create | `bot/start-bot.bat` | Bot launcher |
| Create | `bot/.env.example` | Token config template |
| Create | `bookmarklet.js` | One-click cookie extractor |
| Modify | `web-app/app.py` | Remove check_rooms, import from shared.scraper |
| Modify | `web-app/rbs-checker.spec` | Add root to pathex for shared module |
| Modify | `web-app/start.bat` | cd to own directory, use root requirements.txt |
| Modify | `web-app/build.bat` | cd to own directory |
| Modify | `.gitignore` | Add bot/cookies.json, bot/.env |
| Modify | `requirements.txt` | Add python-telegram-bot, python-dotenv |
| Modify | `README.md` | Update structure and bot setup instructions |

---

## Task 1: Restructure repo folders

**Files:**
- Move: `app.py` → `web-app/app.py`
- Move: `build.bat` → `web-app/build.bat`
- Move: `start.bat` → `web-app/start.bat`
- Move: `rbs-checker.spec` → `web-app/rbs-checker.spec`
- Create: `shared/__init__.py`
- Create: `bot/` directory

- [ ] **Step 1: Create directories and move files**

```bash
cd "C:\Users\fauza\OneDrive\Desktop\rbs-checker"
mkdir web-app bot shared
move app.py web-app\app.py
move build.bat web-app\build.bat
move start.bat web-app\start.bat
move rbs-checker.spec web-app\rbs-checker.spec
type nul > shared\__init__.py
```

- [ ] **Step 2: Verify structure**

```bash
ls web-app/ bot/ shared/
```

Expected:
```
web-app/: app.py  build.bat  start.bat  rbs-checker.spec
bot/:     (empty)
shared/:  __init__.py
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: restructure repo into web-app/, bot/, shared/ folders"
```

---

## Task 2: Create `shared/scraper.py`

**Files:**
- Create: `shared/scraper.py`

Extract the room-checking logic from `web-app/app.py` into `shared/scraper.py` with three functions: `_do_check` (shared core), `check_rooms` (password login, used by web app), `check_rooms_with_cookie` (cookie auth, used by bot).

- [ ] **Step 1: Create `shared/scraper.py`**

```python
# shared/scraper.py
import os
from playwright.sync_api import sync_playwright


def _do_check(page, date, start_time, end_time, log_fn):
    """Shared room-checking logic. Called after auth is set up."""
    log_fn('step', 'Loading booking search page...')
    page.goto("https://rbs.singaporetech.edu.sg/SRB001/SRB001Page", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=15000)
    log_fn('progress', done=2, total=5)

    log_fn('step', 'Selecting Discussion Room type...')
    page.get_by_role('combobox', name='Resource Type').click()
    page.wait_for_timeout(500)
    page.get_by_role('option', name='Discussion Room').click()
    page.wait_for_timeout(500)

    log_fn('step', f'Setting date to {date}...')
    page.evaluate(f"""
        var input = document.getElementById('searchSlotDate');
        var months = {{'Jan':0,'Feb':1,'Mar':2,'Apr':3,'May':4,'Jun':5,
                       'Jul':6,'Aug':7,'Sep':8,'Oct':9,'Nov':10,'Dec':11}};
        var parts = '{date}'.split(' ');
        var d = new Date(parseInt(parts[2]), months[parts[1]], parseInt(parts[0]));
        input.removeAttribute('readonly');
        input.value = '{date}';
        input.setAttribute('readonly', 'readonly');
        input.setAttribute('day', d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate());
    """)
    page.wait_for_timeout(300)

    log_fn('step', f'Setting time {start_time} \u2192 {end_time}...')
    page.select_option('#SearchHoursFrom', label=start_time)
    page.select_option('#SearchHoursTo', label=end_time)
    log_fn('progress', done=3, total=5)

    log_fn('step', 'Searching for available rooms...')
    page.get_by_role('button', name='Search', exact=True).click()
    page.wait_for_selector('.cardwimg', timeout=20000)
    page.wait_for_timeout(1500)
    log_fn('progress', done=4, total=5)

    cards = page.evaluate("""
    () => {
        const token      = document.querySelector('input[name=__RequestVerificationToken]')?.value || '';
        const searchDate = document.querySelector('#searchSlotDate')?.value || '';
        const startTime  = document.querySelector('#SearchHoursFrom option:checked')?.text || '';
        const endTime    = document.querySelector('#SearchHoursTo option:checked')?.text || '';
        const rsrcTypeID = document.querySelector('.cardwimg')?.getAttribute('data-rsrctypid') || '';
        const bkgStatus  = document.querySelector('#bookingstatus')?.value || 'All';
        return Array.from(document.querySelectorAll('.cardwimg')).map(card => ({
            rsrcID:     card.getAttribute('data-rsrcid'),
            rsrcName:   card.getAttribute('data-rsrcname'),
            rsrcTypeID, bkgStatus, searchDate, startTime, endTime, token,
            bkgRul: card.getAttribute('data-isbkgrul'),
            isSld:  card.getAttribute('data-issld')
        }));
    }
    """)

    total_rooms = len(cards)
    log_fn('info', f'Found {total_rooms} rooms. Checking each one...')

    fully, partial, none_list = [], [], []

    for i, card in enumerate(cards):
        room_name = card['rsrcName']
        log_fn('step', f'[{i+1}/{total_rooms}] {room_name}')

        try:
            data = page.evaluate("""
            async (c) => {
                const params = new URLSearchParams({
                    __RequestVerificationToken: c.token,
                    rsrcID:           c.rsrcID,
                    rsrctypID:        c.rsrcTypeID,
                    bookingstatus:    c.bkgStatus,
                    SearchDate:       c.searchDate,
                    SearchStartTime:  c.startTime,
                    SearchEndTime:    c.endTime,
                    BKG_RUL:          c.bkgRul,
                    IS_SLD_Resource:  c.isSld
                });
                const resp = await fetch('/SRB001/GetTimeSlotListByresidNdatetime', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: params.toString()
                });
                return await resp.json();
            }
            """, card)

            if isinstance(data, str):
                none_list.append(room_name)
                log_fn('warn', f'  \u21b3 unexpected response, skipping')
                continue

            total = len(data)
            avail = sum(1 for s in data if s['SLT_STATUS'] == 1)
            slots = [{'time': s['SLT_Desc'], 'avail': s['SLT_STATUS'] == 1} for s in data]

            if avail == total and total > 0:
                fully.append({'name': room_name, 'slots': [s['time'] for s in slots if s['avail']]})
                log_fn('done', f'  \u21b3 \u2713 fully available ({avail}/{total} slots)')
            elif avail > 0:
                partial.append({'name': room_name, 'avail': avail, 'total': total, 'slots': slots})
                log_fn('info', f'  \u21b3 {avail}/{total} slots free')
            else:
                none_list.append(room_name)
                log_fn('warn', f'  \u21b3 fully booked')

        except Exception as exc:
            none_list.append(room_name + ' (error)')
            log_fn('warn', f'  \u21b3 error: {str(exc)[:60]}')

        log_fn('progress', done=4 + (i + 1), total=4 + total_rooms)

    log_fn('done', f'All done! \u2705 {len(fully)} fully available  \U0001F7E1 {len(partial)} partial  \u274C {len(none_list)} booked')
    return {'fully': fully, 'partial': partial, 'none': none_list}


def check_rooms(username, password, date, start_time, end_time, log_fn):
    """Used by the Flask web app. Authenticates with username and password."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        log_fn('step', 'Navigating to RBS login...')
        page.goto("https://rbs.singaporetech.edu.sg/SRB001/SRB001Page", timeout=20000)
        page.wait_for_selector(
            '#userNameInput, input[name="UserName"], input[placeholder="someone@example.com"]',
            timeout=15000
        )
        log_fn('step', 'Signing in...')
        page.fill('input[placeholder="someone@example.com"]', username)
        page.fill('input[type="password"]', password)
        page.locator('#submitButton, input[type="submit"], button[type="submit"]').first.click()

        try:
            page.wait_for_selector(
                '[data-bind*="errorText"], #errorText, #usernameError, '
                '.alert-error, [class*="error"], [id*="error"]',
                timeout=3000
            )
            error_el = page.locator(
                '[data-bind*="errorText"], #errorText, #usernameError, '
                '.alert-error, [class*="error"], [id*="error"]'
            ).first
            error_text = error_el.inner_text().strip()
            if error_text:
                raise Exception(f"Login failed: {error_text}")
            raise Exception("Login failed: incorrect email or password.")
        except Exception as e:
            if "Login failed" in str(e):
                raise

        page.wait_for_url("**/rbs.singaporetech.edu.sg/**", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if 'login.microsoftonline' in page.url or 'sts.singaporetech' in page.url:
            raise Exception("Login failed: incorrect email or password.")

        log_fn('info', 'Login successful.')
        log_fn('progress', done=1, total=5)

        result = _do_check(page, date, start_time, end_time, log_fn)
        browser.close()
        return result


def check_rooms_with_cookie(cookie_string, date, start_time, end_time, log_fn):
    """Used by the Telegram bot. Authenticates using a browser cookie string.

    cookie_string: raw Cookie header value, e.g. "name=value; name2=value2"
    Raises Exception("SESSION_EXPIRED") if the cookie is invalid or expired.
    """
    cookies = []
    for part in cookie_string.split(';'):
        part = part.strip()
        if '=' in part:
            name, _, value = part.partition('=')
            cookies.append({
                'name': name.strip(),
                'value': value.strip(),
                'domain': 'rbs.singaporetech.edu.sg',
                'path': '/',
            })

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

        log_fn('step', 'Navigating to RBS with saved session...')
        page.goto("https://rbs.singaporetech.edu.sg/SRB001/SRB001Page", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if 'login.microsoftonline' in page.url or 'sts.singaporetech' in page.url:
            browser.close()
            raise Exception("SESSION_EXPIRED")

        log_fn('info', 'Session valid. Proceeding...')
        log_fn('progress', done=1, total=5)

        result = _do_check(page, date, start_time, end_time, log_fn)
        browser.close()
        return result
```

- [ ] **Step 2: Verify file was created**

```bash
ls shared/
```

Expected: `__init__.py  scraper.py`

- [ ] **Step 3: Commit**

```bash
git add shared/
git commit -m "feat: add shared/scraper.py with check_rooms, check_rooms_with_cookie, _do_check"
```

---

## Task 3: Update `web-app/app.py`

**Files:**
- Modify: `web-app/app.py`

Remove the `check_rooms` function body, add `sys.path` insertion at the top, import `check_rooms` from `shared.scraper`.

- [ ] **Step 1: Add sys.path insert and import at the top of `web-app/app.py`**

Replace the existing first line:
```python
from flask import Flask, request, jsonify, render_template_string, Response
from playwright.sync_api import sync_playwright
import json, threading, queue, uuid, time
```

With:
```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, render_template_string, Response
from shared.scraper import check_rooms
import json, threading, queue, uuid, time
```

- [ ] **Step 2: Delete the `check_rooms` function from `web-app/app.py`**

Remove the entire `check_rooms` function (lines 275–444 in the original file — the function starting with `def check_rooms(username, password, date, start_time, end_time, log_fn):` and ending before `@app.route("/")`).

- [ ] **Step 3: Verify the web app still runs**

```bash
cd web-app
python app.py
```

Expected: browser opens at http://localhost:5000 with no errors. Run a test check to confirm the scraper import works.

- [ ] **Step 4: Commit**

```bash
git add web-app/app.py
git commit -m "refactor: web-app/app.py now imports check_rooms from shared.scraper"
```

---

## Task 4: Update `web-app/rbs-checker.spec`

**Files:**
- Modify: `web-app/rbs-checker.spec`

The spec moved from root to `web-app/`. Update it so PyInstaller can find the `shared` package at the repo root.

- [ ] **Step 1: Replace the top of `web-app/rbs-checker.spec`**

Replace:
```python
import os
import playwright

# Path to the playwright driver bundled with the Python package
_playwright_driver = os.path.join(os.path.dirname(playwright.__file__), 'driver')

a = Analysis(
    ['app.py'],
    pathex=[],
```

With:
```python
import os
import playwright

# Path to the playwright driver bundled with the Python package
_playwright_driver = os.path.join(os.path.dirname(playwright.__file__), 'driver')

# Repo root (parent of web-app/) so the 'shared' package is findable
_root = os.path.dirname(SPECPATH)

a = Analysis(
    ['app.py'],
    pathex=[_root],
```

- [ ] **Step 2: Add `shared.scraper` to `hiddenimports` in `web-app/rbs-checker.spec`**

Change:
```python
    hiddenimports=[
        'playwright',
```

To:
```python
    hiddenimports=[
        'shared.scraper',
        'playwright',
```

- [ ] **Step 3: Update `web-app/build.bat` to cd to its own directory first**

Replace the entire content of `web-app/build.bat` with:
```bat
@echo off
cd /d "%~dp0"
echo Installing PyInstaller...
py -m pip install pyinstaller

echo.
echo Building rbs-checker.exe...
py -m PyInstaller rbs-checker.spec --clean --noconfirm

echo.
echo Done! Distributable is in: dist\rbs-checker\
echo Zip the entire "dist\rbs-checker" folder and share it.
pause
```

- [ ] **Step 4: Update `web-app/start.bat` to cd to its own directory first**

Replace the entire content of `web-app/start.bat` with:
```bat
@echo off
cd /d "%~dp0"
echo Installing/updating dependencies...
pip install -r ..\requirements.txt -q
echo Installing browser...
python -m playwright install chromium
echo.
echo Starting SIT RBS Checker...
python app.py
pause
```

- [ ] **Step 5: Test the build**

```bash
cd web-app
build.bat
```

Expected: build completes without errors, `dist\rbs-checker\rbs-checker.exe` exists.

- [ ] **Step 6: Commit**

```bash
git add web-app/
git commit -m "fix: update spec pathex and bat files for new web-app/ folder structure"
```

---

## Task 5: Create `bookmarklet.js`

**Files:**
- Create: `bookmarklet.js`

A JavaScript bookmarklet users drag to their bookmarks bar. When clicked on the RBS site, it copies the page's accessible cookies to clipboard. (Note: HttpOnly cookies won't be accessible — if the session cookie is HttpOnly, the bot will detect SESSION_EXPIRED and show DevTools instructions as fallback.)

- [ ] **Step 1: Create `bookmarklet.js`**

```javascript
// bookmarklet.js
// To use: copy the javascript: line below and save it as a browser bookmark.
// Click it while logged into RBS to copy your session cookies to clipboard.

// Paste this as the URL of a new bookmark:
javascript:(function(){
  var cookies = document.cookie;
  if (!cookies) {
    alert('No cookies found. Make sure you are logged into RBS first.');
    return;
  }
  navigator.clipboard.writeText(cookies).then(function() {
    alert('\u2705 Cookie copied! Paste it into the Telegram bot.');
  }).catch(function() {
    prompt('Copy this cookie string:', cookies);
  });
})();
```

- [ ] **Step 2: Commit**

```bash
git add bookmarklet.js
git commit -m "feat: add browser bookmarklet for one-click cookie extraction"
```

---

## Task 6: Create `bot/bot.py`

**Files:**
- Create: `bot/bot.py`

Full Telegram bot using python-telegram-bot v20+ async API. ConversationHandler manages the date → start time → end time → cookie flow. Uses `asyncio.Lock` for concurrency.

- [ ] **Step 1: Create `bot/bot.py`**

```python
# bot/bot.py
import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Add repo root to path so 'shared' package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.scraper import check_rooms_with_cookie

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
BOT_TOKEN = os.environ['BOT_TOKEN']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Conversation states
CHOOSE_DATE, CHOOSE_START, CHOOSE_END, AWAITING_COOKIE, AWAITING_CUSTOM_DATE = range(5)

# One scrape at a time
scrape_lock = asyncio.Lock()
executor = ThreadPoolExecutor(max_workers=1)

# Cookie file
COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.json')

COOKIE_INSTRUCTIONS = (
    "🍪 <b>Session cookie needed</b>\n\n"
    "Your saved cookie has expired or hasn't been set yet.\n\n"
    "<b>Option A — Bookmarklet (try first):</b>\n"
    "1. Add the bookmarklet from the GitHub repo to your browser\n"
    "2. Log into RBS, then click the bookmarklet\n"
    "3. Paste the copied text here\n\n"
    "<b>Option B — DevTools (always works):</b>\n"
    "1. Log into <a href='https://rbs.singaporetech.edu.sg'>RBS</a>\n"
    "2. Press <code>F12</code> → <b>Network</b> tab\n"
    "3. Press <code>F5</code> to reload\n"
    "4. Click any request in the list\n"
    "5. Scroll to <b>Request Headers</b> → find <code>Cookie:</code>\n"
    "6. Copy the full value after <code>Cookie:</code> and paste it here"
)


def _load_cookies() -> dict:
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE) as f:
            return json.load(f)
    return {}


def _save_cookie(user_id: int, cookie_str: str) -> None:
    data = _load_cookies()
    data[str(user_id)] = cookie_str
    with open(COOKIES_FILE, 'w') as f:
        json.dump(data, f)


def _delete_cookie(user_id: int) -> None:
    data = _load_cookies()
    data.pop(str(user_id), None)
    with open(COOKIES_FILE, 'w') as f:
        json.dump(data, f)


def _get_cookie(user_id: int) -> str | None:
    return _load_cookies().get(str(user_id))


def _fmt_date(dt: datetime) -> str:
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def _date_keyboard() -> InlineKeyboardMarkup:
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"Today ({today.strftime('%d %b')})", callback_data="date_today"),
        InlineKeyboardButton(f"Tomorrow ({tomorrow.strftime('%d %b')})", callback_data="date_tomorrow"),
        InlineKeyboardButton("Pick a date", callback_data="date_pick"),
    ]])


def _time_keyboard(after: str | None = None) -> InlineKeyboardMarkup:
    times = []
    for h in range(7, 22):
        times.append(f"{h:02d}:00")
        times.append(f"{h:02d}:30")
    times.append("22:00")
    if after:
        times = [t for t in times if t > after]
    rows, row = [], []
    for t in times:
        row.append(InlineKeyboardButton(t, callback_data=f"time_{t}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _format_results(data: dict) -> str:
    fully = data['fully']
    partial = data['partial']
    none_list = data['none']
    lines = [f"✅ <b>Fully Available ({len(fully)})</b>"]
    lines += [f"• {r['name']}" for r in fully] or ["<i>None</i>"]
    lines.append(f"\n🟡 <b>Partially Available ({len(partial)})</b>")
    lines += [f"• {r['name']} — {r['avail']}/{r['total']} slots free" for r in partial] or ["<i>None</i>"]
    lines.append(f"\n❌ <b>Fully Booked ({len(none_list)})</b>")
    lines += [f"• {n}" for n in none_list] or ["<i>None</i>"]
    return "\n".join(lines)


async def _run_check(update: Update, context: ContextTypes.DEFAULT_TYPE, cookie_str: str) -> int:
    """Run the scraper and edit the status message with results. Returns next state."""
    date = context.user_data['date']
    start = context.user_data['start']
    end = context.user_data['end']
    user_id = update.effective_user.id

    loop = asyncio.get_event_loop()
    async with scrape_lock:
        try:
            result = await loop.run_in_executor(
                executor,
                lambda: check_rooms_with_cookie(
                    cookie_str, date, start, end,
                    lambda *a, **kw: None  # progress logging not used in bot
                )
            )
            text = _format_results(result)
        except Exception as e:
            if "SESSION_EXPIRED" in str(e):
                _delete_cookie(user_id)
                reply_text = COOKIE_INSTRUCTIONS
                if update.callback_query:
                    await update.callback_query.edit_message_text(reply_text, parse_mode='HTML')
                else:
                    await update.message.reply_text(reply_text, parse_mode='HTML')
                return AWAITING_COOKIE
            text = f"❌ Error: {str(e)[:200]}"

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='HTML')
    else:
        status_msg = context.user_data.get('_status_msg')
        if status_msg:
            await status_msg.edit_text(text, parse_mode='HTML')
        else:
            await update.message.reply_text(text, parse_mode='HTML')

    return ConversationHandler.END


# ── Command handlers ────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>Welcome to SIT RBS Room Checker!</b>\n\n"
        "Commands:\n"
        "/check — Check room availability\n"
        "/cookie — Update your session cookie\n\n"
        "Use /check to get started.",
        parse_mode='HTML'
    )


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if scrape_lock.locked():
        await update.message.reply_text("⏳ A check is already running. Please try again in a moment.")
        return ConversationHandler.END
    await update.message.reply_text("📅 Select a date:", reply_markup=_date_keyboard())
    return CHOOSE_DATE


async def date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "date_pick":
        await query.edit_message_text("Type the date in <code>YYYY-MM-DD</code> format (e.g. <code>2026-04-15</code>):", parse_mode='HTML')
        return AWAITING_CUSTOM_DATE
    dt = datetime.now() if query.data == "date_today" else datetime.now() + timedelta(days=1)
    context.user_data['date'] = _fmt_date(dt)
    await query.edit_message_text(
        f"📅 Date: {context.user_data['date']}\n\n🕐 Select start time:",
        reply_markup=_time_keyboard()
    )
    return CHOOSE_START


async def custom_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        dt = datetime.strptime(update.message.text.strip(), '%Y-%m-%d')
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use <code>YYYY-MM-DD</code> (e.g. <code>2026-04-15</code>):", parse_mode='HTML')
        return AWAITING_CUSTOM_DATE
    context.user_data['date'] = _fmt_date(dt)
    await update.message.reply_text(
        f"📅 Date: {context.user_data['date']}\n\n🕐 Select start time:",
        reply_markup=_time_keyboard()
    )
    return CHOOSE_START


async def start_time_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    start = query.data.replace("time_", "")
    context.user_data['start'] = start
    await query.edit_message_text(
        f"📅 Date: {context.user_data['date']}\n🕐 Start: {start}\n\n🕑 Select end time:",
        reply_markup=_time_keyboard(after=start)
    )
    return CHOOSE_END


async def end_time_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    end = query.data.replace("time_", "")
    context.user_data['end'] = end
    user_id = update.effective_user.id
    cookie_str = _get_cookie(user_id)
    if not cookie_str:
        await query.edit_message_text(COOKIE_INSTRUCTIONS, parse_mode='HTML')
        return AWAITING_COOKIE
    await query.edit_message_text(
        f"🔍 Checking rooms for {context.user_data['date']}, {context.user_data['start']}–{end}..."
    )
    return await _run_check(update, context, cookie_str)


async def cookie_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cookie_str = update.message.text.strip()
    user_id = update.effective_user.id
    _save_cookie(user_id, cookie_str)
    date = context.user_data['date']
    start = context.user_data['start']
    end = context.user_data['end']
    msg = await update.message.reply_text(f"🔍 Checking rooms for {date}, {start}–{end}...")
    context.user_data['_status_msg'] = msg
    return await _run_check(update, context, cookie_str)


async def cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(COOKIE_INSTRUCTIONS, parse_mode='HTML')
    return AWAITING_COOKIE


async def cookie_only_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cookie_str = update.message.text.strip()
    _save_cookie(update.effective_user.id, cookie_str)
    await update.message.reply_text("✅ Cookie saved! Use /check to check rooms.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    check_conv = ConversationHandler(
        entry_points=[CommandHandler('check', check_command)],
        states={
            CHOOSE_DATE: [CallbackQueryHandler(date_chosen, pattern='^date_')],
            AWAITING_CUSTOM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_date_received)],
            CHOOSE_START: [CallbackQueryHandler(start_time_chosen, pattern='^time_')],
            CHOOSE_END: [CallbackQueryHandler(end_time_chosen, pattern='^time_')],
            AWAITING_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cookie_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    cookie_conv = ConversationHandler(
        entry_points=[CommandHandler('cookie', cookie_command)],
        states={
            AWAITING_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cookie_only_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(check_conv)
    application.add_handler(cookie_conv)

    print("Bot started. Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Commit**

```bash
git add bot/bot.py
git commit -m "feat: add Telegram bot with cookie-based auth and inline keyboard flow"
```

---

## Task 7: Create bot launchers and config files

**Files:**
- Create: `bot/start-bot.bat`
- Create: `bot/.env.example`

- [ ] **Step 1: Create `bot/start-bot.bat`**

```bat
@echo off
cd /d "%~dp0"
echo Installing dependencies...
pip install -r ..\requirements.txt -q
echo.
echo Starting SIT RBS Telegram Bot...
python bot.py
pause
```

- [ ] **Step 2: Create `bot/.env.example`**

```
# Copy this file to .env and fill in your bot token
# Get a token from @BotFather on Telegram

BOT_TOKEN=your-telegram-bot-token-here
```

- [ ] **Step 3: Commit**

```bash
git add bot/start-bot.bat bot/.env.example
git commit -m "feat: add bot launcher and .env.example"
```

---

## Task 8: Update config files and README

**Files:**
- Modify: `.gitignore`
- Modify: `requirements.txt`
- Modify: `README.md`

- [ ] **Step 1: Update `.gitignore`**

Add to the bottom of `.gitignore`:
```
# Bot secrets and session data
bot/.env
bot/cookies.json
```

- [ ] **Step 2: Update `requirements.txt`**

Replace the entire file:
```
flask==3.1.3
playwright==1.58.0
python-telegram-bot==21.10
python-dotenv==1.1.0
```

(Verify latest stable versions of python-telegram-bot and python-dotenv if needed.)

- [ ] **Step 3: Update `README.md`**

Replace the entire file:
```markdown
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
```

- [ ] **Step 4: Commit all**

```bash
git add .gitignore requirements.txt README.md
git commit -m "chore: update gitignore, requirements, README for new repo structure and bot"
git push
```

---

## Self-Review

**Spec coverage check:**
- ✅ Repo restructure into web-app/, bot/, shared/ — Task 1
- ✅ shared/scraper.py with check_rooms, check_rooms_with_cookie, _do_check — Task 2
- ✅ web-app/app.py updated to import from shared — Task 3
- ✅ PyInstaller spec updated for new paths — Task 4
- ✅ Bookmarklet — Task 5
- ✅ bot/bot.py with ConversationHandler, inline keyboards, cookie flow — Task 6
- ✅ start-bot.bat, .env.example — Task 7
- ✅ .gitignore (bot/.env, bot/cookies.json), requirements.txt, README — Task 8
- ✅ Concurrency via asyncio.Lock — in bot/bot.py (_run_check)
- ✅ Smart cookie reuse (try saved → SESSION_EXPIRED → ask for new) — in end_time_chosen + _run_check
- ✅ /start, /check, /cookie commands — in bot/bot.py

**Type consistency check:**
- `check_rooms_with_cookie(cookie_string, date, start_time, end_time, log_fn)` — defined in Task 2, called in Task 6 ✅
- `_fmt_date(dt)` — defined and used within bot.py ✅
- `_time_keyboard(after=None)` — defined and called with `after=start` in start_time_chosen ✅
- `AWAITING_COOKIE` state — used consistently in both check_conv and cookie_conv ✅

**No placeholders:** All steps contain complete code. ✅

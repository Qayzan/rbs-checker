# bot/bot.py
from __future__ import annotations
import asyncio
import html
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
import uuid

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
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
from shared.scraper import building_of, check_rooms_with_cookie, login_and_get_cookie
from shared.scraper import _sessions as browser_sessions
from shared.scraper import verify_session_cookie, warm_up, cached_result

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')

def _now():
    return datetime.now(timezone(timedelta(hours=8)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)

# Conversation states
CHOOSE_DATE, AWAITING_CUSTOM_DATE, CHOOSE_BUILDING, CHOOSE_START, CHOOSE_END, \
    AWAITING_EMAIL, AWAITING_PASSWORD = range(7)

# RBS only answers one lookup at a time per logged-in person, but different
# people have different sessions, so two people's checks can run side by side.
MAX_PARALLEL_CHECKS = max(1, int(os.environ.get('RBS_MAX_PARALLEL_CHECKS', '2')))
check_slots = asyncio.Semaphore(MAX_PARALLEL_CHECKS)

# Availability is the same for everyone, so a recent result can be reused.
CACHE_TTL_SECS = 120
_result_cache: dict[tuple, tuple[float, dict]] = {}

BOT_DIR = os.path.dirname(__file__)
DATA_DIR = os.environ.get('RBS_DATA_DIR', BOT_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
COOKIES_FILE = os.path.join(DATA_DIR, 'cookies.json')
BUILDINGS_FILE = os.path.join(DATA_DIR, 'buildings.json')
PREFERENCES_FILE = os.path.join(DATA_DIR, 'preferences.json')
ALERTS_FILE = os.path.join(DATA_DIR, 'alerts.json')
_active_checks = {}
_status_checks = {}
_alert_checks = {}
_warmups = {}
_inflight = {}
_user_locks = {}
_alert_task = None
DEFAULT_BUILDINGS = ['E2', 'E6', 'W3', 'W5']

RBS_URL = "https://rbs.singaporetech.edu.sg/SRB001/SRB001Page"
CHECK_BUTTON_TEXT = "🔍 Check rooms"
CHECK_BUTTON_FILTER = filters.Regex(r'^🔍 Check rooms$')
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton(CHECK_BUTTON_TEXT)], [KeyboardButton('🔁 Repeat search')]], resize_keyboard=True, is_persistent=True
)

NOT_LOGGED_IN = (
    "🔐 <b>You're not logged in</b> (or your SIT session expired).\n\n"
    "Send /login to sign in with your SIT account. I'll keep your search, "
    "so you can run it straight after."
)

START_TEXT = (
    "👋 <b>SIT Discussion Room Checker</b>\n"
    "I find free SIT discussion rooms for you, so you don't have to click through RBS.\n\n"
    "1️⃣ Send /login and sign in with your SIT account (once)\n"
    f"2️⃣ Tap <b>{CHECK_BUTTON_TEXT}</b> below\n\n"
    "<i>I only check rooms; you still book on RBS. /help shows all commands and tips.</i>"
)

HELP_TEXT = (
    "👋 <b>SIT Discussion Room Checker</b>\n\n"
    "I look up which SIT discussion rooms are free, so you don't have to click "
    "through every room on RBS yourself.\n\n"
    "<b>How to use</b>\n"
    "1️⃣ /login — sign in with your SIT email and password (once; I remember you "
    "until SIT logs you out)\n"
    f"2️⃣ Tap <b>{CHECK_BUTTON_TEXT}</b> (or send /check) → pick a day, a building, "
    "and when you'd arrive and leave\n"
    "3️⃣ Rooms appear as I find them — you don't have to wait for the whole list\n\n"
    "<b>Reading the results</b>\n"
    "✅ <b>Free the whole time</b> — book it for your full window\n"
    "🟡 <b>Free for part of the time</b> — shows exactly when, e.g. <code>16:00–18:00</code> "
    "means free from 4pm until 6pm\n"
    "❌ <b>Taken</b> — no free time in your window\n"
    "👉 The <b>best pick</b> is shown at the top. Use <b>≥1h</b> / <b>≥2h</b> under the "
    "results to hide short gaps; tap it again to show everything.\n\n"
    "⚡ <b>Tip:</b> picking one building is ~3× faster than \"Any building\".\n\n"
    f'I only <i>check</i> rooms — booking is still done on <a href="{RBS_URL}">RBS</a>.\n\n'
    "<b>More options</b>\n"
    "/find YYYY-MM-DD 12:00 18:00 120 E2 — find a continuous 2h gap\n"
    "Add a final 3 to stop after three matching rooms.\n"
    "/repeat repeats your last search. Under a result, <b>⋯ More options</b> has:\n"
    "🔔 tell me when a room frees up (checks every 5 minutes, up to 24 hours), "
    "gap finders, and ⭐ remember your building and length.\n"
    "/alerts shows your alert; /unwatch stops it.\n"
    "All times are Singapore time. /cancel stops a running search.\n"
    "Other commands: /preferences, /status, /logout"
)


# ---------------------------------------------------------------------------
# Small storage helpers
# ---------------------------------------------------------------------------
def _load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _save_cookie(user_id: int, cookie_str: str) -> None:
    data = _load_json(COOKIES_FILE, {})
    data[str(user_id)] = cookie_str
    _save_json(COOKIES_FILE, data)


def _delete_cookie(user_id: int) -> None:
    data = _load_json(COOKIES_FILE, {})
    if data.pop(str(user_id), None) is not None:
        _save_json(COOKIES_FILE, data)


def _get_cookie(user_id: int) -> str | None:
    return _load_json(COOKIES_FILE, {}).get(str(user_id))


def _buildings() -> list[str]:
    found = _load_json(BUILDINGS_FILE, [])
    return found if found else DEFAULT_BUILDINGS


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def _fmt_date(dt: datetime) -> str:
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def _to_min(hhmm: str) -> int:
    h, m = map(int, hhmm.split(':'))
    return h * 60 + m


def _to_hhmm(mins: int) -> str:
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"


def _fmt_duration(mins: int) -> str:
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


EARLIEST, LATEST = "07:00", "22:00"     # range offered on the time buttons
WHOLE_DAY = ("08:00", "22:00")


def _fetch_window(q: dict) -> tuple[str, str]:
    """Ask SIT for the whole day, not just the requested window.

    SIT takes the same ~1.4 s per room whatever the window, and the extra slots
    let later searches for the same day and building (any times) be answered
    from saved data with no browser at all. For today, start no earlier than
    the current half-hour, since past slots are useless."""
    start = EARLIEST
    if q['date'] == _fmt_date(_now()):
        now = _now()
        start = max(EARLIEST, _to_hhmm((now.hour * 60 + now.minute) // 30 * 30))
    return min(start, q['start']), max(LATEST, q['end'])


def _next_two_hours() -> tuple[str, str] | None:
    """From the next half-hour mark, for 2 hours (only makes sense for today)."""
    now = _now()
    start = max(((now.hour * 60 + now.minute + 29) // 30) * 30, _to_min(EARLIEST))
    end = min(start + 120, _to_min(LATEST))
    if end - start < 120:
        return None
    return _to_hhmm(start), _to_hhmm(end)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------
def _date_keyboard() -> InlineKeyboardMarkup:
    today = _now()
    buttons = []
    for i in range(7):
        d = today + timedelta(days=i)
        if i == 0:
            label = f"Today ({d.strftime('%a %d %b')})"
        elif i == 1:
            label = f"Tomorrow ({d.strftime('%a %d %b')})"
        else:
            label = d.strftime('%a %d %b')
        buttons.append(InlineKeyboardButton(label, callback_data=f"date_{d.strftime('%Y-%m-%d')}"))
    rows = [buttons[0:2]] + [buttons[i:i + 3] for i in range(2, 7, 3)]
    rows.append([InlineKeyboardButton("📝 Another date (type it)", callback_data="date_pick")])
    return InlineKeyboardMarkup(rows)


def _building_keyboard() -> InlineKeyboardMarkup:
    blds = _buildings()
    row = [InlineKeyboardButton(b, callback_data=f"bld_{b}") for b in blds]
    rows = [row[i:i + 4] for i in range(0, len(row), 4)]
    rows.append([InlineKeyboardButton("🌐 Any building (slower)", callback_data="bld_ANY")])
    rows.append([InlineKeyboardButton('← Change date', callback_data='back_date')])
    return InlineKeyboardMarkup(rows)


def _time_keyboard(after: str | None = None, quick: list | None = None) -> InlineKeyboardMarkup:
    times = []
    t = _to_min(EARLIEST)
    while t <= _to_min(LATEST):
        times.append(_to_hhmm(t))
        t += 30
    if after:
        times = [x for x in times if x > after]
    else:
        times = times[:-1]          # can't start at the very last time
    rows = []
    if quick:
        rows.append(quick)
    row = []
    for x in times:
        row.append(InlineKeyboardButton(x, callback_data=f"time_{x}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton('← Change start time' if after else '← Change building',
                                     callback_data='back_start' if after else 'back_building')])
    return InlineKeyboardMarkup(rows)


def _start_time_keyboard(is_today: bool) -> InlineKeyboardMarkup:
    quick = []
    if is_today and _next_two_hours():
        a, b = _next_two_hours()
        quick.append(InlineKeyboardButton(f"⏩ Next 2h ({a}–{b})", callback_data="quick_next2"))
    quick.append(InlineKeyboardButton(f"🌞 Whole day ({WHOLE_DAY[0]}–{WHOLE_DAY[1]})", callback_data="quick_day"))
    return _time_keyboard(quick=quick)


def _result_keyboard(min_free: int) -> InlineKeyboardMarkup:
    """Used when a check failed and there is no result to filter."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Check again", callback_data="recheck"),
         InlineKeyboardButton("📅 New search", callback_data="newcheck")],
    ])


def _result_rows(token: str, min_free: int, page: int = 0, pages: int = 1,
                 expanded: bool = False) -> list:
    """Buttons under a result: filters, check again / new search, and a 'More options'
    toggle that reveals the less-used actions."""
    def opt(label, value):
        active = min_free == value     # tapping the active filter turns it off
        return InlineKeyboardButton(("✓ " if active else "") + label,
                                    callback_data=f"minfree_{0 if active else value}:{token}")
    rows = []
    if pages > 1:
        nav = []
        if page:
            nav.append(InlineKeyboardButton('← Previous', callback_data=f'page{page-1}:{token}'))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton('Next →', callback_data=f'page{page+1}:{token}'))
        rows.append(nav)
    rows.append([opt("≥1h free", 60), opt("≥2h free", 120)])
    rows.append([InlineKeyboardButton("🔄 Check again", callback_data=f"recheck:{token}"),
                 InlineKeyboardButton("📅 New search", callback_data="newcheck")])
    if expanded:
        rows.append([InlineKeyboardButton('🔔 Tell me when a room frees up', callback_data=f'watch:{token}')])
        rows.append([InlineKeyboardButton('Find a 1h gap', callback_data=f'gap60:{token}'),
                     InlineKeyboardButton('Find a 2h gap', callback_data=f'gap120:{token}')])
        rows.append([InlineKeyboardButton('⚡ Just the first 3 rooms', callback_data=f'fast:{token}')])
        rows.append([InlineKeyboardButton('⭐ Remember this building & length', callback_data=f'save:{token}')])
        rows.append([InlineKeyboardButton('▲ Fewer options', callback_data=f'less:{token}')])
    else:
        rows.append([InlineKeyboardButton('⋯ More options', callback_data=f'more:{token}')])
    return rows


# ---------------------------------------------------------------------------
# Results formatting
# ---------------------------------------------------------------------------
def _free_ranges(slots: list[dict]) -> list[tuple[str, str]]:
    """Each RBS slot is 30 min, labelled by its start time.
    ['16:00','16:30','17:00'] free -> [('16:00','17:30')]."""
    ranges: list[list[str]] = []
    prev_free = False
    for s in sorted(slots, key=lambda s: _to_min(s['time'])):
        end = _to_hhmm(_to_min(s['time']) + 30)
        if s['avail']:
            if prev_free and ranges[-1][1] == s['time']:
                ranges[-1][1] = end
            else:
                ranges.append([s['time'], end])
        prev_free = s['avail']
    return [tuple(r) for r in ranges]


def _partial_rows(partial: list[dict]) -> list[tuple[int, int, str, list]]:
    rows = []
    for r in partial:
        ranges = _free_ranges(r.get('slots', []))
        longest = max((_to_min(b) - _to_min(a) for a, b in ranges), default=0)
        total_free = sum(_to_min(b) - _to_min(a) for a, b in ranges)
        rows.append((longest, total_free, r['name'], ranges))
    rows.sort(key=lambda x: (-x[0], -x[1], x[2]))   # longest free stretch first
    return rows


def _header(q: dict) -> list[str]:
    where = "all buildings" if q['building'] == 'ANY' else q['building']
    return [f"📍 <b>{html.escape(q['date'])}, {q['start']}–{q['end']} · {where}</b>"]


def _best_pick(fully: list[dict], rows: list) -> str | None:
    """One recommendation: a room free the whole time, else the longest free stretch."""
    if fully:
        return f"👉 <b>Best pick: {html.escape(fully[0]['name'])}</b>, free the whole time"
    if rows:
        longest, _, name, ranges = rows[0]
        a, b = next((a, b) for a, b in ranges if _to_min(b) - _to_min(a) == longest)
        return f"👉 <b>Best pick: {html.escape(name)}</b>, free {a}–{b} ({_fmt_duration(longest)})"
    return None


def _format_results(result: dict, q: dict, min_free: int = 0, age_secs: float | None = None) -> str:
    fully, partial, taken = result['fully'], result['partial'], result['none']
    window = _to_min(q['end']) - _to_min(q['start'])

    lines = _header(q)
    if age_secs is not None and age_secs >= 5:
        lines.append(f"<i>Checked {int(age_secs // 60)}m {int(age_secs % 60)}s ago. "
                     "Tap 🔄 for a fresh check.</i>")

    if min_free > window:
        fully = []
    rows = _partial_rows(partial)
    hidden = 0
    if min_free:
        shown = [r for r in rows if r[0] >= min_free]
        hidden = len(rows) - len(shown)
        rows = shown

    best = _best_pick(fully, rows)
    lines.append("")
    if best:
        lines.append(best)
    else:
        lines.append("😕 <b>No free room in this window.</b>")
        lines.append("<i>Try another building, a different time, or 🔔 under “More options” "
                     "to be told when one frees up.</i>")

    if fully:
        lines.append(f"\n✅ <b>Free the whole time ({len(fully)})</b>")
        lines += [f"• {html.escape(r['name'])}" for r in fully]

    if rows:
        title = "Free for part of the time"
        if min_free:
            title += f", at least {_fmt_duration(min_free)} in a row"
        lines.append(f"\n🟡 <b>{title} ({len(rows)})</b>")
        for longest, total_free, name, ranges in rows:
            when = ", ".join(f"{a}–{b}" for a, b in ranges)
            lines.append(f"• {html.escape(name)}\n   free <b>{when}</b> ({_fmt_duration(total_free)})")
    if hidden:
        lines.append(f"<i>{hidden} more with shorter gaps are hidden. Tap the ✓ filter to show them.</i>")

    if taken:
        lines.append(f"\n❌ <b>Taken the whole time ({len(taken)}):</b> "
                     "<i>" + ", ".join(html.escape(n) for n in taken) + "</i>")

    lines.append(f'\n<a href="{RBS_URL}">Book on RBS</a> · /help explains this list')
    if result.get('errors'):
        lines.append('\n⚠️ <b>Could not check:</b> ' + ', '.join(html.escape(n) for n in result['errors']))
    if result.get('incomplete'):
        lines.append('<i>Stopped after finding enough matches; other rooms were not checked.</i>')
    return "\n".join(lines)


def _format_live(found: dict, q: dict, done: int, total: int, step: str) -> str:
    """Shown while a check is running: progress + rooms found so far."""
    filled = int(done / total * 10) if total else 0
    bar = '▓' * filled + '░' * (10 - filled)
    lines = _header(q)
    lines.append(f"🔍 Checking rooms… {bar} {done}/{total}" if total else "🔍 Getting the room list…")
    if step:
        lines.append(f"<i>{html.escape(step)}</i>")

    fully = found['fully']
    rows = _partial_rows(found['partial'])
    if fully or rows:
        lines.append("\n<b>Found so far</b> (you can book these already):")
        for r in fully[:15]:
            lines.append(f"✅ {html.escape(r['name'])} — whole time")
        for longest, total_free, name, ranges in rows[:15]:
            when = ", ".join(f"{a}–{b}" for a, b in ranges)
            lines.append(f"🟡 {html.escape(name)} — {when}")
        more = max(0, len(fully) - 15) + max(0, len(rows) - 15)
        if more:
            lines.append(f"<i>…and {more} more</i>")
    return "\n".join(lines)


def _split_message(text: str, limit: int = 3900) -> list[str]:
    """Telegram rejects messages over 4096 chars, so split on line breaks."""
    chunks, cur = [], ''
    for line in text.split('\n'):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks or ['']


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _filter_building(result: dict, building: str) -> dict:
    keep = lambda name: building_of(name) == building
    return {
        'fully': [r for r in result['fully'] if keep(r['name'])],
        'partial': [r for r in result['partial'] if keep(r['name'])],
        'none': [n for n in result['none'] if keep(n)],
        'errors': [n for n in result.get('errors', []) if keep(n)],
    }


def _cache_get(q: dict) -> tuple[dict, float] | None:
    now = time.time()
    for key in list(_result_cache):
        if now - _result_cache[key][0] > CACHE_TTL_SECS:
            del _result_cache[key]
    base = (q['date'], q['start'], q['end'])
    hit = _result_cache.get(base + (q['building'],))
    if hit:
        return hit[1], now - hit[0]
    if q['building'] != 'ANY':
        hit = _result_cache.get(base + ('ANY',))
        if hit:
            return _filter_building(hit[1], q['building']), now - hit[0]
    return None


def _cache_put(q: dict, result: dict) -> None:
    if result.get('incomplete') or result.get('errors'):
        return
    _result_cache[(q['date'], q['start'], q['end'], q['building'])] = (time.time()-result.get('cache_age', 0), result)


# ---------------------------------------------------------------------------
# Sending results
# ---------------------------------------------------------------------------
async def _show_results(context: ContextTypes.DEFAULT_TYPE, chat, target_msg, text: str,
                        min_free: int, page: int = 0) -> None:
    """Show results starting in target_msg (edited), adding follow-up messages if the
    text is longer than Telegram allows. Results that span several messages form a
    group: re-showing them (e.g. the ≥1h button, which sits on the LAST message)
    rewrites the group's first message and replaces its follow-ups."""
    groups = context.user_data.setdefault('result_groups', {})   # head id -> follow-up ids
    head_id = target_msg.message_id
    for h, follow in groups.items():
        if head_id in follow:
            head_id = h
            break
    for mid in groups.pop(head_id, []):
        try:
            await context.bot.delete_message(chat.id, mid)
        except Exception:
            pass

    chunks = _split_message(text)
    token = context.user_data.get('result_token', '')
    page = max(0, min(page, len(chunks)-1))
    kb = _result_keyboard(min_free)
    if token:
        pages = len(chunks)
        record = context.user_data.get('search_records', {}).get(token)
        if record is not None:
            record.update(min_free=min_free, page=page, pages=pages)
        kb = InlineKeyboardMarkup(_result_rows(token, min_free, page, pages))
        if pages > 1:
            chunks = [f'<i>Page {page+1}/{pages}</i>\n' + chunks[page]]
    follow_ids = []
    for i, chunk in enumerate(chunks):
        markup = kb if i == len(chunks) - 1 else None
        if i == 0:
            try:
                await context.bot.edit_message_text(
                    chunk, chat_id=chat.id, message_id=head_id, parse_mode='HTML',
                    reply_markup=markup, disable_web_page_preview=True)
                continue
            except Exception as e:
                if 'not modified' in str(e).lower():
                    continue
                logging.warning("Could not edit result message, sending a new one: %s", e)
        try:
            m = await chat.send_message(chunk, parse_mode='HTML', reply_markup=markup,
                                        disable_web_page_preview=True)
            if i > 0:
                follow_ids.append(m.message_id)
        except Exception:
            logging.exception("Result chunk %d could not be sent", i)
    if follow_ids:
        groups[head_id] = follow_ids
    while len(groups) > 20:          # don't grow forever
        groups.pop(next(iter(groups)))


def _query_from(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    ud = context.user_data
    if all(k in ud for k in ('date', 'start', 'end')):
        return {'date': ud['date'], 'start': ud['start'], 'end': ud['end'],
                'building': ud.get('building', 'ANY')}
    return None


async def _perform_check(update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg,
                     force_fresh: bool = False, search_query=None) -> int:
    """Run (or reuse) a check, streaming rooms into status_msg as they're found."""
    user_id = update.effective_user.id
    chat = update.effective_chat
    q = dict(search_query or _query_from(context) or {})
    if not q:
        await status_msg.edit_text("Something went wrong — please start again with /check.")
        return ConversationHandler.END

    cookie_str = _get_cookie(user_id)
    if not cookie_str:
        await status_msg.edit_text(NOT_LOGGED_IN, parse_mode='HTML')
        return ConversationHandler.END

    _validate_query(q)
    context.user_data['min_free'] = 0
    token = uuid.uuid4().hex[:10]
    context.user_data['result_token'] = token

    def remember(result, age):
        records = context.user_data.setdefault('search_records', {})
        records[token] = {'q': dict(q), 'result': result, 'checked': time.time() - age}
        while len(records) > 20:
            records.pop(next(iter(records)))
        context.user_data['result_token'] = token
        prefs = _load_json(PREFERENCES_FILE, {})
        prefs.setdefault(str(user_id), {})['last'] = dict(q)
        _save_json(PREFERENCES_FILE, prefs)

    if not force_fresh and not q.get('limit') and not _cache_get(q):
        saved = cached_result(q['date'], q['start'], q['end'],
                              None if q['building'] == 'ANY' else q['building'])
        if saved:
            _cache_put(q, saved)
    if not force_fresh:
        cached = _cache_get(q)
        if cached:
            result, age = cached
            remember(result, age)
            context.user_data['last_result'] = result
            context.user_data['last_age_base'] = time.time() - age
            await _show_results(context, chat, status_msg,
                                _format_search(result, q, 0, age), 0)
            return ConversationHandler.END

    found = {'fully': [], 'partial': [], 'none': [], 'errors': []}
    progress = {'done': 0, 'total': 0, 'step': ''}
    finished = [False]
    last_edit = [0.0]
    edit_lock = asyncio.Lock()

    async def refresh(force: bool = False) -> None:
        if finished[0]:
            return
        now = time.monotonic()
        if not force and now - last_edit[0] < 2.0:     # Telegram dislikes rapid edits
            return
        last_edit[0] = now
        async with edit_lock:
            if finished[0]:
                return
            try:
                await status_msg.edit_text(
                    _format_live(found, q, progress['done'], progress['total'], progress['step']),
                    parse_mode='HTML')
            except Exception:
                pass

    def log_fn(level, msg=None, **kwargs):
        if level == 'step':
            progress['step'] = msg or ''
        elif level == 'progress':
            # The scraper reports 4 set-up steps first, then one step per room.
            if kwargs.get('done', 0) > 4:
                progress['total'] = kwargs.get('total', 5) - 4
                progress['done'] = kwargs.get('done', 0) - 4
            asyncio.get_running_loop().create_task(refresh())

    async def on_room(category: str, entry) -> None:
        found[category].append(entry)
        if category != 'none':
            await refresh()
        if q.get('limit') and len(_matches(found, q)) >= q['limit']:
            raise _EnoughMatches()

    if check_slots.locked():
        try:
            await status_msg.edit_text(
                "⏳ Other people are checking right now — you're next in line…")
        except Exception:
            pass

    queued_at = time.monotonic()
    async with check_slots:
        logging.info('RBS queue_seconds=%.3f', time.monotonic()-queued_at)
        # Someone may have finished the same search while we waited.
        cached = None if force_fresh else _cache_get(q)
        if cached:
            result, age = cached
        else:
            await refresh(force=True)
            try:
                building = None if q['building'] == 'ANY' else q['building']
                prefs = _load_json(PREFERENCES_FILE, {}).get(str(user_id), {})
                result = await _shared_lookup(q, cookie_str, log_fn, on_room,
                    fresh=force_fresh, preferred_rooms=prefs.get('rooms', []))
            except _EnoughMatches:
                result = dict(found, incomplete=True)
            except asyncio.CancelledError:
                finished[0] = True
                raise
            except Exception as e:
                finished[0] = True
                if "SESSION_EXPIRED" in str(e):
                    _delete_cookie(user_id)
                    await status_msg.edit_text(NOT_LOGGED_IN, parse_mode='HTML')
                    return ConversationHandler.END
                logging.exception("Check failed")
                await status_msg.edit_text(
                    f"❌ Something went wrong while checking: {html.escape(str(e)[:200])}\n\n"
                    "Try again with /check.", parse_mode='HTML',
                    reply_markup=_result_keyboard(0))
                return ConversationHandler.END
            age = result.get('cache_age', 0)
            if result.get('buildings'):
                _save_json(BUILDINGS_FILE, result['buildings'])
            _cache_put(q, result)
        finished[0] = True

    async with edit_lock:
        remember(result, age)
        context.user_data['last_result'] = result
        context.user_data['last_age_base'] = time.time() - age
        await _show_results(context, chat, status_msg, _format_search(result, q, 0, age), 0)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /check conversation
# ---------------------------------------------------------------------------
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop('duration', None)
    context.user_data.pop('limit', None)
    _start_warmup(context, update.effective_user.id)
    await update.message.reply_text("📅 <b>Which day?</b>", parse_mode='HTML',
                                    reply_markup=_date_keyboard())
    return CHOOSE_DATE


async def newcheck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop('duration', None)
    context.user_data.pop('limit', None)
    query = update.callback_query
    await query.answer()
    _start_warmup(context, update.effective_user.id)
    await query.message.reply_text("📅 <b>Which day?</b>", parse_mode='HTML',
                                   reply_markup=_date_keyboard())
    return CHOOSE_DATE


async def _ask_building(send, date_label: str) -> int:
    await send(f"📅 {date_label}\n\n🏢 <b>Which building?</b>",
               parse_mode='HTML', reply_markup=_building_keyboard())
    return CHOOSE_BUILDING


async def date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "date_pick":
        await query.edit_message_text(
            "Type the date as <code>DD MM</code> or <code>YYYY-MM-DD</code> "
            "(e.g. <code>15 12</code> or <code>2026-12-15</code>):", parse_mode='HTML')
        return AWAITING_CUSTOM_DATE
    dt = datetime.strptime(query.data.replace("date_", ""), '%Y-%m-%d')
    context.user_data['date'] = _fmt_date(dt)
    context.user_data['is_today'] = dt.date() == _now().date()
    return await _ask_building(query.edit_message_text,
                               f"{dt.strftime('%a')} {context.user_data['date']}")


async def custom_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    dt = None
    for fmt in ('%Y-%m-%d', '%d %m %Y', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            pass
    if dt is None:
        try:   # "15 12" or "15/12" → this year, or next year if already past
            d, m = map(int, text.replace('/', ' ').replace('-', ' ').split())
            today = _now()
            dt = datetime(today.year, m, d)
            if dt.date() < today.date():
                dt = datetime(today.year + 1, m, d)
        except Exception:
            await update.message.reply_text(
                "❌ I couldn't read that date. Try <code>15 12</code> or <code>2026-12-15</code>:",
                parse_mode='HTML')
            return AWAITING_CUSTOM_DATE
    context.user_data['date'] = _fmt_date(dt)
    context.user_data['is_today'] = dt.date() == _now().date()
    return await _ask_building(update.message.reply_text,
                               f"{dt.strftime('%a')} {context.user_data['date']}")


async def building_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    building = query.data.replace("bld_", "")
    context.user_data['building'] = building
    where = "any building" if building == 'ANY' else building
    await query.edit_message_text(
        f"📅 {context.user_data['date']} · {where}\n\n"
        "🕐 <b>From what time?</b> (when you'd arrive)\n"
        "<i>Or tap a quick option.</i>",
        parse_mode='HTML',
        reply_markup=_start_time_keyboard(context.user_data.get('is_today', False)))
    return CHOOSE_START


async def start_time_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data in ("quick_next2", "quick_day"):
        span = _next_two_hours() if query.data == "quick_next2" else WHOLE_DAY
        if not span:
            await query.answer("It's too late today for that — pick a time.", show_alert=True)
            return CHOOSE_START
        context.user_data['start'], context.user_data['end'] = span
        return await _run_check(update, context, query.message)

    start = query.data.replace("time_", "")
    context.user_data['start'] = start
    await query.edit_message_text(
        f"📅 {context.user_data['date']}, from {start}\n\n"
        "🕑 <b>Until what time?</b> (when you'd leave)",
        parse_mode='HTML',
        reply_markup=_time_keyboard(after=start))
    return CHOOSE_END


async def end_time_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['end'] = query.data.replace("time_", "")
    return await _run_check(update, context, query.message)


async def recheck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _query_from(context):
        await query.message.reply_text("Start a new search with /check.")
        return
    await _run_check(update, context, query.message, force_fresh=True)


async def minfree_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-show the last result with a 'free for at least X' filter (no new check)."""
    query = update.callback_query
    q = _query_from(context)
    result = context.user_data.get('last_result')
    if not q or not result:
        await query.answer("That result is gone — tap 🔄 Check again.", show_alert=True)
        return
    min_free = int(query.data.replace("minfree_", ""))
    await query.answer()
    context.user_data['min_free'] = min_free
    age = time.time() - context.user_data.get('last_age_base', time.time())
    await _show_results(context, update.effective_chat, query.message,
                        _format_results(result, q, min_free, age), min_free)


# ---------------------------------------------------------------------------
# Login conversation
# ---------------------------------------------------------------------------
async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != 'private':
        await update.message.reply_text('Please open a private chat with me to log in.')
        return ConversationHandler.END
    await update.message.reply_text(
        "🔐 <b>Log in to RBS</b>\n\nSend your SIT email address\n"
        "(students: <code>STUDENTID@sit.singaporetech.edu.sg</code>).\n\n"
        "Your credentials pass through Telegram and this bot to sign in to SIT. "
        "I try to delete your credential messages and save only the session locally:",
        parse_mode='HTML')
    return AWAITING_EMAIL


async def email_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['login_email'] = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message(
        "🔑 Now send your SIT password.\n\n<i>I try to delete your message straight away. "
        "Your password passes through Telegram; I save only your login session.</i>",
        parse_mode='HTML')
    return AWAITING_PASSWORD


async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text
    email = context.user_data.pop('login_email', '')
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    msg = await update.effective_chat.send_message("⏳ Logging in, please wait…")
    try:
        cookie_str = await login_and_get_cookie(email, password)
        _save_cookie(user_id, cookie_str)
        markup = None
        text = f"✅ Logged in! Tap <b>{CHECK_BUTTON_TEXT}</b> below to find a room."
        q = _query_from(context)
        if q:
            where = "any building" if q['building'] == 'ANY' else q['building']
            text = "✅ Logged in!"
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(
                f"🔍 Run my search ({q['date']}, {q['start']}–{q['end']}, {where})",
                callback_data="recheck")]])
        await msg.edit_text(text, parse_mode='HTML', reply_markup=markup)
        if not q:
            await update.effective_chat.send_message("👇", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        err = str(e)
        if "LOGIN_FAILED" in err:
            await msg.edit_text("❌ Login failed. Check your email and password, then /login again.")
        else:
            logging.exception("Login error")
            await msg.edit_text(f"❌ Unexpected error: {err[:200]}\n\nTry /login again.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Simple commands
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(START_TEXT, parse_mode='HTML',
                                    disable_web_page_preview=True, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode='HTML',
                                    disable_web_page_preview=True, reply_markup=MAIN_KEYBOARD)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    cookie = _get_cookie(uid)
    if not cookie:
        await update.message.reply_text(
            "❌ <b>Not logged in.</b> Send /login to sign in with your SIT account.",
            parse_mode='HTML')
        return
    if uid in _status_checks:
        await update.message.reply_text('Your session check is already running…')
        return
    queued = uid in _active_checks or check_slots.locked()
    msg = await update.message.reply_text(
        '⏳ A room check is running. I’ll verify your SIT session when it finishes.' if queued
        else '🔐 Checking your session with SIT…')
    async def verify():
        try:
            async def probe():
                async with _user_locks.setdefault(uid, asyncio.Lock()):
                    async with check_slots:
                        return await asyncio.wait_for(verify_session_cookie(cookie), timeout=35)
            valid = await probe()
            if _get_cookie(uid) != cookie:
                await msg.edit_text('Your login changed during this check. Send /status again.')
                return
            if valid:
                await msg.edit_text('✅ Logged in — verified with SIT just now. You can /check for rooms.')
            else:
                _delete_cookie(uid)
                await msg.edit_text('🔐 Your SIT session expired. Send /login to sign in again.')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning('Could not verify SIT session: %s', type(exc).__name__)
            await msg.edit_text('⚠️ A saved session exists, but I could not verify it with SIT right now. Try /status again shortly. Your session has been kept.')
        finally:
            _status_checks.pop(uid, None)
    _status_checks[uid] = context.application.create_task(verify(), update=update)


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _stop_status_check(update.effective_user.id)
    await _stop_check(update.effective_user.id)
    await _stop_alert_check(str(update.effective_user.id))
    alerts = _load_json(ALERTS_FILE, {})
    alerts.pop(str(update.effective_user.id), None)
    _save_json(ALERTS_FILE, alerts)
    cookie = _get_cookie(update.effective_user.id)
    _delete_cookie(update.effective_user.id)
    if cookie:
        await browser_sessions.close(cookie)
    context.user_data.clear()
    await update.message.reply_text("✅ Logged out. Your saved session is deleted.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _stop_status_check(update.effective_user.id)
    await _stop_check(update.effective_user.id)
    context.user_data.pop('login_email', None)
    await update.message.reply_text("Cancelled.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


async def _post_init(application: Application) -> None:
    global _alert_task
    _alert_task = asyncio.create_task(_watch_loop(application))
    await application.bot.set_my_commands([
        BotCommand("check", "Find a free discussion room"),
        BotCommand("login", "Sign in with your SIT account"),
        BotCommand("help", "How this bot works"),
        BotCommand("status", "Am I logged in?"),
        BotCommand("logout", "Forget my session"),
        BotCommand("cancel", "Stop what I'm doing"),
        BotCommand("find", "Flexible search: /find 2026-11-10 12:00 18:00 120 E2 3"),
        BotCommand("repeat", "Repeat your last search"),
        BotCommand("preferences", "View saved building and duration"),
        BotCommand("favourites", "Set preferred rooms, separated by spaces; clear to reset"),
        BotCommand("alerts", "View your room alert"),
        BotCommand("unwatch", "Stop your room alert"),
    ])


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .post_stop(_post_stop)
        .concurrent_updates(False)
        .build()
    )

    repeat_filter = filters.Regex('^🔁 Repeat search$')
    free_text = filters.TEXT & ~filters.COMMAND & ~CHECK_BUTTON_FILTER & ~repeat_filter

    check_conv = ConversationHandler(
        entry_points=[
            CommandHandler('check', check_command),
            MessageHandler(CHECK_BUTTON_FILTER, check_command),
            CallbackQueryHandler(newcheck_callback, pattern='^newcheck$'),
        ],
        states={
            CHOOSE_DATE: [CallbackQueryHandler(date_chosen, pattern='^date_')],
            AWAITING_CUSTOM_DATE: [MessageHandler(free_text, custom_date_received)],
            CHOOSE_BUILDING: [CallbackQueryHandler(building_chosen, pattern='^bld_')],
            CHOOSE_START: [CallbackQueryHandler(start_time_chosen, pattern='^(time_|quick_)')],
            CHOOSE_END: [CallbackQueryHandler(end_time_chosen, pattern='^time_')],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    login_conv = ConversationHandler(
        entry_points=[CommandHandler('login', login_command)],
        states={
            AWAITING_EMAIL: [MessageHandler(free_text, email_received)],
            AWAITING_PASSWORD: [MessageHandler(free_text, password_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    for handlers in check_conv.states.values():
        handlers.insert(0, CallbackQueryHandler(back_callback, pattern='^back_'))

    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('logout', logout_command))
    application.add_handler(CommandHandler('repeat', repeat_command))
    application.add_handler(MessageHandler(repeat_filter, repeat_command))
    application.add_handler(CommandHandler('find', find_command))
    application.add_handler(CommandHandler('preferences', preferences_command))
    application.add_handler(CommandHandler('favourites', favourites_command))
    application.add_handler(CommandHandler('alerts', alerts_command))
    application.add_handler(CommandHandler('unwatch', unwatch_command))
    application.add_handler(CallbackQueryHandler(result_action, pattern=r'^(recheck|minfree_\d+|page\d+|watch|save|gap60|gap120|fast|more|less):'))
    application.add_handler(CallbackQueryHandler(recheck_callback, pattern='^recheck$'))
    application.add_handler(login_conv)
    application.add_handler(check_conv)
    application.add_handler(CommandHandler('cancel', cancel))

    print("Bot started. Leave this window open. Press Ctrl+C to stop.")
    application.run_polling()


class _EnoughMatches(BaseException):
    """Leave the scraper while still closing its browser context."""


async def back_callback(update, context):
    cb = update.callback_query
    await cb.answer()
    if cb.data == 'back_date':
        await cb.edit_message_text('📅 Which day?', reply_markup=_date_keyboard())
        return CHOOSE_DATE
    if cb.data == 'back_building':
        return await _ask_building(cb.edit_message_text, context.user_data['date'])
    await cb.edit_message_text('🕐 From what time?',
        reply_markup=_start_time_keyboard(context.user_data.get('is_today', False)))
    return CHOOSE_START


def _validate_query(q):
    day = datetime.strptime(q['date'], '%d %b %Y').date()
    if day < _now().date():
        raise ValueError('Choose today or a future date.')
    start, end = _to_min(q['start']), _to_min(q['end'])
    if q['start'] != _to_hhmm(start) or q['end'] != _to_hhmm(end):
        raise ValueError('Use times in HH:MM format, such as 12:00.')
    if not (420 <= start < end <= 1320) or start % 30 or end % 30:
        raise ValueError('Choose half-hour times between 07:00 and 22:00, with the end after the start.')
    if day == _now().date() and end <= _now().hour * 60 + _now().minute:
        raise ValueError('That time window has already ended.')
    if q.get('duration', 30) < 30 or q.get('duration', 30) > end - start:
        raise ValueError('The required duration must fit inside your search window.')
    if q['building'] not in _buildings() + ['ANY']:
        raise ValueError('Unknown building. Choose ' + ', '.join(_buildings()) + ' or ANY.')


def _matches(result, q):
    matches = []
    for category in ('fully', 'partial'):
        for room in result[category]:
            slots = ([{'time': t, 'avail': True} for t in room['slots']]
                     if category == 'fully' else room['slots'])
            for a, b in _free_ranges(slots):
                if _to_min(b) - _to_min(a) >= q.get('duration', _to_min(q['end']) - _to_min(q['start'])):
                    matches.append((a, b, room['name']))
                    break
    return sorted(matches, key=lambda r: (r[0], r[2]))


def _format_search(result, q, min_free=0, age=None):
    if not q.get('duration'):
        return _format_results(result, q, min_free, age)
    filtered = dict(q, duration=max(q['duration'], min_free))
    matches = _matches(result, filtered)
    lines = _header(q) + [f"Find {_fmt_duration(filtered['duration'])} continuously; earliest start first."]
    lines += [f"✅ {html.escape(name)} — <b>{a}–{b}</b>" for a, b, name in matches]
    if not matches:
        lines.append('No matching rooms found in the rooms checked.')
    if result.get('errors'):
        lines.append('⚠️ Could not check: ' + ', '.join(html.escape(n) for n in result['errors']))
    if result.get('incomplete'):
        lines.append('Stopped after enough matches; remaining rooms were not checked.')
    if age is not None:
        lines.append(f'Checked {int(age)} seconds ago.')
    lines.append(f'<a href="{RBS_URL}">Book on RBS</a>')
    return '\n'.join(lines)


async def _stop_check(user_id):
    task = _active_checks.pop(user_id, None)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# Keep conversation updates ordered, while slow browser work runs separately.
async def _run_check(update, context, status_msg, force_fresh=False, search_query=None):
    q = dict(search_query or _query_from(context) or {})
    try:
        _validate_query(q)
    except (ValueError, KeyError) as exc:
        await status_msg.edit_text(str(exc))
        return ConversationHandler.END
    uid = update.effective_user.id
    await _stop_check(uid)
    await _stop_alert_check(str(uid))
    async def work():
        try:
            async with _user_locks.setdefault(uid, asyncio.Lock()):
                await _perform_check(update, context, status_msg, force_fresh, q)
        except asyncio.CancelledError:
            await status_msg.edit_text('Search cancelled.')
            raise
        except Exception:
            logging.exception('Search failed')
            await status_msg.edit_text('Could not finish the search. Try /check again.')
        finally:
            if _active_checks.get(uid) is asyncio.current_task():
                _active_checks.pop(uid, None)
    _active_checks[uid] = context.application.create_task(work(), update=update)
    return ConversationHandler.END


async def repeat_command(update, context):
    saved = _load_json(PREFERENCES_FILE, {}).get(str(update.effective_user.id), {}).get('last')
    if not saved:
        await update.message.reply_text('Run /check or /find first.')
        return
    msg = await update.message.reply_text('Repeating your search…')
    await _run_check(update, context, msg, search_query=saved)


async def preferences_command(update, context):
    prefs = _load_json(PREFERENCES_FILE, {}).get(str(update.effective_user.id), {})
    await update.message.reply_text(
        f"Saved building: {prefs.get('building', 'ANY')}\nDuration: {prefs.get('duration', 120)} minutes.\n"
        'Tap ⭐ under a result to save its building and duration. /repeat repeats the last search.')


async def favourites_command(update, context):
    data = _load_json(PREFERENCES_FILE, {})
    prefs = data.setdefault(str(update.effective_user.id), {})
    if context.args:
        prefs['rooms'] = [] if context.args == ['clear'] else list(dict.fromkeys(context.args))[:10]
        _save_json(PREFERENCES_FILE, data)
    await update.message.reply_text('Preferred rooms: ' + (', '.join(prefs.get('rooms', [])) or 'none') +
        '\nUse /favourites ROOM-NAME ROOM-NAME to check those first, or /favourites clear.')


async def find_command(update, context):
    prefs = _load_json(PREFERENCES_FILE, {}).get(str(update.effective_user.id), {})
    try:
        args = context.args
        q = {'date': _fmt_date(datetime.strptime(args[0], '%Y-%m-%d')),
             'start': args[1], 'end': args[2],
             'duration': int(args[3]) if len(args) > 3 else prefs.get('duration', 120),
             'building': args[4].upper() if len(args) > 4 else prefs.get('building', 'ANY')}
        if len(args) > 5:
            q['limit'] = int(args[5])
            if not 1 <= q['limit'] <= 20:
                raise ValueError('Match limit must be 1–20.')
        _validate_query(q)
    except (ValueError, IndexError) as exc:
        await update.message.reply_text(
            f'{exc}\nUsage: /find YYYY-MM-DD 12:00 18:00 [minutes] [building] [match limit]\n'
            'Example: /find 2026-11-10 12:00 18:00 120 E2 3\nOmit the limit to check every room.')
        return
    msg = await update.message.reply_text('Finding suitable rooms…')
    await _run_check(update, context, msg, search_query=q)


async def result_action(update, context):
    cb = update.callback_query
    action, token = cb.data.split(':', 1)
    record = context.user_data.get('search_records', {}).get(token)
    if not record:
        await cb.answer('This result has expired. Start /check again.', show_alert=True)
        return
    await cb.answer()
    q = dict(record['q'])
    uid = str(update.effective_user.id)
    if action in ('more', 'less'):
        rows = _result_rows(token, record.get('min_free', 0), record.get('page', 0),
                            record.get('pages', 1), expanded=action == 'more')
        try:
            await cb.message.edit_reply_markup(InlineKeyboardMarkup(rows))
        except Exception as exc:
            if 'not modified' not in str(exc).lower():
                logging.warning('Could not change result buttons: %s', exc)
        return
    if action.startswith('page'):
        context.user_data['result_token'] = token
        minimum = record.get('min_free', 0)
        await _show_results(context, update.effective_chat, cb.message,
            _format_search(record['result'], q, minimum, time.time()-record['checked']),
            minimum, int(action[4:]))
        return
    if action in ('gap60', 'gap120'):
        q['duration'] = int(action[3:])
        q.pop('limit', None)
        if q['duration'] > _to_min(q['end']) - _to_min(q['start']):
            await cb.message.reply_text('That duration is longer than this search window. Start /check with a wider window.')
            return
        token = uuid.uuid4().hex[:10]
        records = context.user_data['search_records']
        records[token] = dict(record, q=q)
        while len(records) > 20:
            records.pop(next(iter(records)))
        context.user_data['result_token'] = token
        msg = await cb.message.reply_text('Finding gaps in these results…')
        await _show_results(context, update.effective_chat, msg,
            _format_search(record['result'], q, 0, time.time()-record['checked']), 0)
        return
    if action in ('recheck', 'fast'):
        if action.startswith('gap'):
            q['duration'] = int(action[3:])
            q.pop('limit', None)
        elif action == 'fast':
            q['limit'] = 3
        msg = await cb.message.reply_text('Checking this search again…')
        await _run_check(update, context, msg, True, q)
    elif action.startswith('minfree_'):
        minimum = int(action.split('_')[1])
        context.user_data['result_token'] = token
        await _show_results(context, update.effective_chat, cb.message,
                            _format_search(record['result'], q, minimum, time.time()-record['checked']), minimum)
    elif action == 'save':
        data = _load_json(PREFERENCES_FILE, {})
        data.setdefault(uid, {}).update(building=q['building'], duration=q.get('duration', _to_min(q['end'])-_to_min(q['start'])))
        _save_json(PREFERENCES_FILE, data)
        await cb.message.reply_text('Saved your building and duration for /find.')
    elif action == 'watch':
        try:
            _validate_query(q)
        except ValueError as exc:
            await cb.message.reply_text(str(exc))
            return
        await _stop_alert_check(uid)
        alerts = _load_json(ALERTS_FILE, {})
        expiry = datetime.strptime(q['date'], '%d %b %Y').replace(tzinfo=_now().tzinfo)
        expiry += timedelta(minutes=_to_min(q['end']) - q.get('duration', _to_min(q['end'])-_to_min(q['start'])))
        if expiry.timestamp() <= time.time():
            await cb.message.reply_text('The latest start for that duration has passed. Choose a later window.')
            return
        q.setdefault('duration', _to_min(q['end']) - _to_min(q['start']))
        alerts[uid] = {'q': q, 'chat': update.effective_chat.id,
                       'expires': min(time.time()+86400, expiry.timestamp()), 'next': time.time()+300}
        _save_json(ALERTS_FILE, alerts)
        await cb.message.reply_text('Watching every 5 minutes for up to 24 hours, or until the latest suitable start. I’ll notify once and stop. /unwatch cancels.')


async def alerts_command(update, context):
    alert = _load_json(ALERTS_FILE, {}).get(str(update.effective_user.id))
    await update.message.reply_text('No active alert.' if not alert else
        f"Watching {alert['q']['date']} {alert['q']['start']}–{alert['q']['end']}, {alert['q']['building']}. /unwatch stops it.")


async def unwatch_command(update, context):
    await _stop_alert_check(str(update.effective_user.id))
    alerts = _load_json(ALERTS_FILE, {})
    alerts.pop(str(update.effective_user.id), None)
    _save_json(ALERTS_FILE, alerts)
    await update.message.reply_text('Room alert stopped.')


async def _watch_loop(application):
    while True:
        try:
            await _watch_cycle(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception('Alert service interrupted; retrying shortly')
            await asyncio.sleep(30)


async def _watch_cycle(application):
    while True:
        await asyncio.sleep(15)
        for uid, alert in list(_load_json(ALERTS_FILE, {}).items()):
            now = time.time()
            if alert['expires'] <= now:
                data = _load_json(ALERTS_FILE, {})
                if data.get(uid) == alert:
                    data.pop(uid)
                    _save_json(ALERTS_FILE, data)
                    await application.bot.send_message(alert['chat'], 'Room alert expired without a match.')
                continue
            if alert.get('next', 0) > now or int(uid) in _active_checks:
                continue
            try:
                cookie = _get_cookie(int(uid))
                if not cookie:
                    raise RuntimeError('SESSION_EXPIRED')
                q = dict(alert['q'])
                if q['date'] == _fmt_date(_now()):
                    q['start'] = max(q['start'], _to_hhmm(((_now().hour*60+_now().minute+29)//30)*30))
                search_q = dict(q)
                # Fetch one covering window; following alerts reuse its room slots.
                for other_uid, other in _load_json(ALERTS_FILE, {}).items():
                    oq = other['q']
                    if (other_uid != uid and int(other_uid) not in _active_checks
                        and other.get('next', 0) <= now < other['expires']
                        and oq['date'] == q['date'] and oq['building'] == q['building']
                        and oq['start'] < search_q['end'] and oq['end'] > search_q['start']):
                        search_q['start'] = min(search_q['start'], oq['start'])
                        search_q['end'] = max(search_q['end'], oq['end'])
                async with _user_locks.setdefault(int(uid), asyncio.Lock()):
                    async with check_slots:
                        if _load_json(ALERTS_FILE, {}).get(uid) != alert:
                            continue
                        task = asyncio.create_task(_shared_lookup(search_q, cookie, lambda *a, **kw: None))
                        _alert_checks[uid] = task
                        try:
                            result = _clip_result(await task, q)
                        finally:
                            if _alert_checks.get(uid) is task:
                                _alert_checks.pop(uid, None)
                data = _load_json(ALERTS_FILE, {})
                if data.get(uid) != alert:
                    continue
                if _matches(result, q):
                    await application.bot.send_message(alert['chat'], '🔔 A matching room is available!\n' + _format_search(result, q), parse_mode='HTML')
                    data.pop(uid)
                else:
                    data[uid]['next'] = time.time()+300
                _save_json(ALERTS_FILE, data)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as exc:
                logging.warning('Room alert check failed: %s', type(exc).__name__)
                data = _load_json(ALERTS_FILE, {})
                if data.get(uid) != alert:
                    continue
                if 'SESSION_EXPIRED' in str(exc):
                    data.pop(uid)
                    _save_json(ALERTS_FILE, data)
                    await application.bot.send_message(alert['chat'], 'Your room alert stopped because your session expired. /login and set the alert again.')
                else:
                    data[uid]['next'] = time.time()+300
                    _save_json(ALERTS_FILE, data)


async def _post_stop(application):
    for task in list(_warmups.values()):
        task.cancel()
    if _alert_task:
        _alert_task.cancel()
        await asyncio.gather(_alert_task, return_exceptions=True)
    await browser_sessions.close()


async def _stop_alert_check(uid):
    task = _alert_checks.pop(uid, None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _stop_status_check(uid):
    task = _status_checks.pop(uid, None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _clip_result(result, q):
    """Never offer a match outside an alert's own requested window."""
    clipped = dict(result, fully=[], partial=[], none=list(result['none']))
    for category in ('fully', 'partial'):
        for room in result[category]:
            slots = ([{'time': t, 'avail': True} for t in room['slots']]
                     if category == 'fully' else room['slots'])
            slots = [s for s in slots if q['start'] <= s['time'] < q['end']]
            available = sum(s['avail'] for s in slots)
            expected = (_to_min(q['end'])-_to_min(q['start']))//30
            if available == len(slots) == expected and expected:
                clipped['fully'].append({'name': room['name'], 'slots': [s['time'] for s in slots]})
            elif available:
                clipped['partial'].append({'name': room['name'], 'slots': slots,
                                           'avail': available, 'total': len(slots)})
            else:
                clipped['none'].append(room['name'])
    return clipped


async def _shared_lookup(q, cookie, log_fn, on_room=None, fresh=False, preferred_rooms=None):
    """Share in-flight lookups; cancelling one viewer leaves others running.

    Normal searches fetch the whole day (see _fetch_window) and each viewer gets
    the result cut down to their own window, so people searching the same day
    and building at different times share one trip to SIT."""
    if q.get('limit'):
        fetch_q = dict(q)
    else:
        start, end = _fetch_window(q)
        fetch_q = dict(q, start=start, end=end)
    narrowed = (fetch_q['start'], fetch_q['end']) != (q['start'], q['end'])

    def clip_room(listener):
        if listener is None or not narrowed:
            return listener
        async def clipped(category, entry):
            if category not in ('fully', 'partial'):
                await listener(category, entry)
                return
            one = {'fully': [entry] if category == 'fully' else [],
                   'partial': [entry] if category == 'partial' else [], 'none': [], 'errors': []}
            c = _clip_result(one, q)
            for cat in ('fully', 'partial'):
                for e in c[cat]:
                    await listener(cat, e)
            for name in c['none']:
                await listener('none', name)
        return clipped

    async def direct(log, room):
        return await asyncio.wait_for(
            check_rooms_with_cookie(cookie, fetch_q['date'], fetch_q['start'], fetch_q['end'], log,
                building=None if q['building'] == 'ANY' else q['building'], on_room=room,
                use_cache=not fresh, preferred_rooms=preferred_rooms), timeout=180)

    def finish(result):
        return _clip_result(result, q) if narrowed else result

    if q.get('limit'):
        return finish(await direct(log_fn, on_room))
    key = tuple(fetch_q[k] for k in ('date', 'start', 'end', 'building')) + (fresh,)
    record = _inflight.get(key)
    subscriber = (log_fn, clip_room(on_room))
    if record is None:
        record = {'subscribers': [], 'cookie': cookie}
        def log(*args, **kwargs):
            for listener, _ in list(record['subscribers']):
                listener(*args, **kwargs)
        async def room(category, entry):
            for _, listener in list(record['subscribers']):
                if listener:
                    await listener(category, entry)
        record['task'] = asyncio.create_task(direct(log, room))
        _inflight[key] = record
    record['subscribers'].append(subscriber)
    try:
        try:
            return finish(await asyncio.shield(record['task']))
        except Exception as exc:
            # A different person's expired session must not invalidate ours.
            if 'SESSION_EXPIRED' in str(exc) and cookie != record['cookie']:
                return finish(await direct(log_fn, subscriber[1]))
            raise
    finally:
        record['subscribers'].remove(subscriber)
        if not record['subscribers']:
            if _inflight.get(key) is record:
                _inflight.pop(key, None)
            if not record['task'].done():
                record['task'].cancel()
            await asyncio.gather(record['task'], return_exceptions=True)


def _start_warmup(context, user_id: int) -> None:
    """Open the browser and this person's RBS page while they pick day/building/time."""
    cookie = _get_cookie(user_id)
    if not cookie:
        return
    running = _warmups.get(user_id)
    if running and not running.done():
        return
    async def warm():
        try:
            if not await asyncio.wait_for(warm_up(cookie), timeout=60):
                logging.info('Warm-up found an expired session for a user')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning('Warm-up failed (the check will retry): %s', type(exc).__name__)
        finally:
            if _warmups.get(user_id) is asyncio.current_task():
                _warmups.pop(user_id, None)
    _warmups[user_id] = context.application.create_task(warm())


if __name__ == '__main__':
    main()

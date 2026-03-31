# bot/bot.py
from __future__ import annotations
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter
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
from shared.scraper import check_rooms_with_cookie, login_and_get_cookie

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
BOT_TOKEN = os.environ['BOT_TOKEN']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Conversation states
CHOOSE_DATE, CHOOSE_START, CHOOSE_END, AWAITING_COOKIE, AWAITING_CUSTOM_DATE, AWAITING_EMAIL, AWAITING_PASSWORD = range(7)

# Queue-based scrape system — jobs processed one at a time, users told their position
@dataclass
class _CheckJob:
    update: Any
    context: Any
    cookie_str: str
    status_msg: Any

_job_queue: asyncio.Queue = asyncio.Queue()
_worker_busy: bool = False

# Cookie file
COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.json')

COOKIE_INSTRUCTIONS = (
    "🔐 <b>Not logged in</b>\n\n"
    "Use /login to sign in with your SIT credentials.\n\n"
    "<i>Or paste a session cookie directly if you have one.</i>"
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
    if fully:
        for r in fully:
            slots = r.get('slots', [])
            slot_str = ', '.join(slots[:10]) + (f' +{len(slots)-10} more' if len(slots) > 10 else '')
            lines.append(f"• {r['name']}" + (f"\n  ↳ <i>{slot_str}</i>" if slot_str else ''))
    else:
        lines.append("<i>None</i>")

    lines.append(f"\n🟡 <b>Partially Available ({len(partial)})</b>")
    if partial:
        for r in partial:
            free = [s['time'] for s in r.get('slots', []) if s['avail']]
            lines.append(f"• {r['name']} — {r['avail']}/{r['total']} free")
            if free:
                lines.append(f"  ↳ <i>{', '.join(free)}</i>")
    else:
        lines.append("<i>None</i>")

    lines.append(f"\n❌ <b>Fully Booked ({len(none_list)})</b>")
    lines += [f"• {n}" for n in none_list] or ["<i>None</i>"]
    return "\n".join(lines)


_RESULT_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔄 Check again", callback_data="recheck"),
    InlineKeyboardButton("📅 New search", callback_data="newcheck"),
]])


async def _run_check(update: Update, context: ContextTypes.DEFAULT_TYPE, cookie_str: str, status_msg: Any = None) -> None:
    """Run the scraper for one job, showing live progress then results."""
    date = context.user_data['date']
    start = context.user_data['start']
    end = context.user_data['end']
    user_id = update.effective_user.id

    # Guard flag: stops queued progress edits from overwriting the final result
    _done = [False]
    _last_edit = [0.0]
    _step = ['']

    async def _do_edit(text: str) -> None:
        if _done[0]:
            return
        try:
            await status_msg.edit_text(text, parse_mode='HTML')
        except Exception:
            pass

    def log_fn(level, msg=None, **kwargs):
        if level == 'step':
            _step[0] = msg or ''
            return
        if level != 'progress':
            return
        now = time.monotonic()
        if now - _last_edit[0] < 2.5:   # throttle: max one Telegram edit per 2.5 s
            return
        _last_edit[0] = now
        done = kwargs.get('done', 0)
        total = kwargs.get('total', 1)
        filled = int(done / total * 10)
        bar = '▓' * filled + '░' * (10 - filled)
        asyncio.get_running_loop().create_task(
            _do_edit(
                f"🔍 <b>Checking rooms…</b>\n"
                f"{bar} {done}/{total}\n"
                f"<i>{_step[0]}</i>"
            )
        )

    try:
        result = await check_rooms_with_cookie(cookie_str, date, start, end, log_fn)
        final_text = _format_results(result)
    except Exception as e:
        _done[0] = True
        if "SESSION_EXPIRED" in str(e):
            _delete_cookie(user_id)
            try:
                if status_msg:
                    await status_msg.edit_text(COOKIE_INSTRUCTIONS, parse_mode='HTML')
                else:
                    await update.effective_chat.send_message(COOKIE_INSTRUCTIONS, parse_mode='HTML')
            except Exception:
                pass
            return
        final_text = f"❌ Error: {str(e)[:200]}"
    # Set _done, then yield so any in-flight create_task progress edits fire and
    # see _done=True before we attempt the final edit.
    _done[0] = True
    await asyncio.sleep(0)

    # Send final results, retrying once if Telegram rate-limits us.
    for attempt in range(3):
        try:
            if status_msg:
                await status_msg.edit_text(final_text, parse_mode='HTML', reply_markup=_RESULT_KEYBOARD)
            else:
                await update.effective_chat.send_message(final_text, parse_mode='HTML', reply_markup=_RESULT_KEYBOARD)
            break
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception:
            # Non-rate-limit failure — fall back to a fresh message.
            try:
                await update.effective_chat.send_message(final_text, parse_mode='HTML', reply_markup=_RESULT_KEYBOARD)
            except Exception:
                pass
            break


async def _queue_worker() -> None:
    """Background task — picks up check jobs and runs them one at a time."""
    global _worker_busy
    while True:
        job: _CheckJob = await _job_queue.get()
        _worker_busy = True
        try:
            await _run_check(job.update, job.context, job.cookie_str, job.status_msg)
        except Exception as exc:
            logging.exception("Queue worker unhandled error: %s", exc)
        finally:
            _worker_busy = False
            _job_queue.task_done()


async def _enqueue_check(update: Update, context: ContextTypes.DEFAULT_TYPE, cookie_str: str, status_msg: Any) -> None:
    """Add a check job to the queue. If others are ahead, tells the user their position."""
    await _job_queue.put(_CheckJob(update, context, cookie_str, status_msg))
    if _worker_busy:
        pos = _job_queue.qsize()
        try:
            await status_msg.edit_text(
                f"⏳ <b>You're #{pos} in the queue.</b>\n\n"
                "<i>Your check will start automatically when it's your turn.</i>",
                parse_mode='HTML'
            )
        except Exception:
            pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>Welcome to SIT RBS Room Checker!</b>\n\n"
        "Commands:\n"
        "/login — Log in with your SIT credentials\n"
        "/check — Check room availability\n"
        "/status — Show your current login status\n"
        "/logout — Clear your saved session\n\n"
        "Use /login to get started.",
        parse_mode='HTML'
    )


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    status_msg = query.message
    await query.edit_message_text(
        f"🔍 Checking rooms for {context.user_data['date']}, {context.user_data['start']}–{end}…"
    )
    await _enqueue_check(update, context, cookie_str, status_msg)
    return ConversationHandler.END


async def cookie_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cookie_str = update.message.text.strip()
    user_id = update.effective_user.id
    _save_cookie(user_id, cookie_str)
    date = context.user_data['date']
    start = context.user_data['start']
    end = context.user_data['end']
    msg = await update.message.reply_text(f"🔍 Checking rooms for {date}, {start}–{end}…")
    await _enqueue_check(update, context, cookie_str, msg)
    return ConversationHandler.END


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🔐 <b>Login to RBS</b>\n\nEnter your SIT email address:",
        parse_mode='HTML'
    )
    return AWAITING_EMAIL


async def email_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['login_email'] = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message(
        "🔑 Enter your SIT password:\n\n<i>Your message will be deleted immediately.</i>",
        parse_mode='HTML'
    )
    return AWAITING_PASSWORD


async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text.strip()
    email = context.user_data.get('login_email', '')
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    msg = await update.effective_chat.send_message("⏳ Logging in, please wait...")

    try:
        cookie_str = await login_and_get_cookie(email, password)
        _save_cookie(user_id, cookie_str)
        await msg.edit_text("✅ Logged in successfully! Use /check to check rooms.")
    except Exception as e:
        err = str(e)
        if "LOGIN_FAILED" in err:
            await msg.edit_text("❌ Login failed. Check your email and password and try /login again.")
        else:
            await msg.edit_text(f"❌ Unexpected error: {err[:200]}\n\nTry /login again.")

    context.user_data.pop('login_email', None)
    return ConversationHandler.END


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cookie_str = _get_cookie(update.effective_user.id)
    if cookie_str:
        date = context.user_data.get('date', '—')
        start = context.user_data.get('start', '—')
        end = context.user_data.get('end', '—')
        last = f"\n\nLast search: {date}, {start}–{end}" if context.user_data.get('date') else ''
        await update.message.reply_text(
            f"✅ <b>Logged in</b>\n\nYou have an active session.{last}\n\nUse /check to check rooms or /logout to clear your session.",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "❌ <b>Not logged in</b>\n\nUse /login to sign in with your SIT credentials.",
            parse_mode='HTML'
        )


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _delete_cookie(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text("✅ Logged out. Your session has been cleared.")


async def recheck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    cookie_str = _get_cookie(user_id)
    if not cookie_str:
        await query.message.reply_text("❌ No session found. Please /login first.")
        return
    date = context.user_data.get('date', '')
    start = context.user_data.get('start', '')
    end = context.user_data.get('end', '')
    status_msg = query.message
    await query.edit_message_text(
        f"🔍 Checking rooms for {date}, {start}–{end}…",
        parse_mode='HTML'
    )
    await _enqueue_check(update, context, cookie_str, status_msg)


async def newcheck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📅 Select a date:", reply_markup=_date_keyboard())
    return CHOOSE_DATE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def main() -> None:
    async def post_init(app: Application) -> None:
        asyncio.create_task(_queue_worker())

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    check_conv = ConversationHandler(
        entry_points=[
            CommandHandler('check', check_command),
            CallbackQueryHandler(newcheck_callback, pattern='^newcheck$'),
        ],
        states={
            CHOOSE_DATE: [CallbackQueryHandler(date_chosen, pattern='^date_')],
            AWAITING_CUSTOM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_date_received)],
            CHOOSE_START: [CallbackQueryHandler(start_time_chosen, pattern='^time_')],
            CHOOSE_END: [CallbackQueryHandler(end_time_chosen, pattern='^time_')],
            AWAITING_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cookie_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    login_conv = ConversationHandler(
        entry_points=[CommandHandler('login', login_command)],
        states={
            AWAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, email_received)],
            AWAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('logout', logout_command))
    application.add_handler(CallbackQueryHandler(recheck_callback, pattern='^recheck$'))
    application.add_handler(login_conv)
    application.add_handler(check_conv)

    print("Bot started. Press Ctrl+C to stop.")
    application.run_polling()


if __name__ == '__main__':
    main()

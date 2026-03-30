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
                    lambda *a, **kw: None
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

# Telegram /login Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/login` command to the Telegram bot so mobile users can authenticate with their SIT email and password instead of manually extracting cookies.

**Architecture:** Add `login_and_get_cookie(username, password)` to `shared/scraper.py` that reuses the existing Playwright login logic but returns the cookie string. The bot gets two new conversation states (`AWAITING_EMAIL`, `AWAITING_PASSWORD`) and a `/login` handler that saves the cookie on success. Password messages are deleted from chat immediately for security.

**Tech Stack:** python-telegram-bot 21.10, Playwright (already installed), python-dotenv

---

### Task 1: Add `login_and_get_cookie` to shared/scraper.py

**Files:**
- Modify: `shared/scraper.py`

- [ ] **Step 1: Add the function at the bottom of `shared/scraper.py`**

```python
def login_and_get_cookie(username, password):
    """Log in with username/password and return the session cookie string.

    Raises Exception("LOGIN_FAILED: <reason>") on bad credentials.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://rbs.singaporetech.edu.sg/SRB001/SRB001Page", timeout=20000)
        page.wait_for_selector(
            '#userNameInput, input[name="UserName"], input[placeholder="someone@example.com"]',
            timeout=15000
        )
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
                raise Exception(f"LOGIN_FAILED: {error_text}")
            raise Exception("LOGIN_FAILED: incorrect email or password.")
        except Exception as e:
            if "LOGIN_FAILED" in str(e):
                browser.close()
                raise

        page.wait_for_url("**/rbs.singaporetech.edu.sg/**", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if 'login.microsoftonline' in page.url or 'sts.singaporetech' in page.url:
            browser.close()
            raise Exception("LOGIN_FAILED: incorrect email or password.")

        cookies = page.context.cookies()
        cookie_str = '; '.join(
            f"{c['name']}={c['value']}" for c in cookies
            if 'singaporetech.edu.sg' in c.get('domain', '')
        )
        browser.close()
        return cookie_str
```

- [ ] **Step 2: Verify import still works**

```bash
python -c "from shared.scraper import login_and_get_cookie; print('OK')"
```
Expected: `OK`

---

### Task 2: Add login conversation to bot/bot.py

**Files:**
- Modify: `bot/bot.py`

- [ ] **Step 1: Extend the conversation states tuple**

Change:
```python
CHOOSE_DATE, CHOOSE_START, CHOOSE_END, AWAITING_COOKIE, AWAITING_CUSTOM_DATE = range(5)
```
To:
```python
CHOOSE_DATE, CHOOSE_START, CHOOSE_END, AWAITING_COOKIE, AWAITING_CUSTOM_DATE, AWAITING_EMAIL, AWAITING_PASSWORD = range(7)
```

- [ ] **Step 2: Update the import from shared.scraper**

Change:
```python
from shared.scraper import check_rooms_with_cookie
```
To:
```python
from shared.scraper import check_rooms_with_cookie, login_and_get_cookie
```

- [ ] **Step 3: Add the login handlers (insert before `cancel`)**

```python
async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🔐 <b>Login to RBS</b>\n\nEnter your SIT email address:",
        parse_mode='HTML'
    )
    return AWAITING_EMAIL


async def email_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['login_email'] = update.message.text.strip()
    await update.message.reply_text(
        "🔑 Enter your SIT password:\n\n<i>Your message will be deleted immediately.</i>",
        parse_mode='HTML'
    )
    return AWAITING_PASSWORD


async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text.strip()
    email = context.user_data.get('login_email', '')
    user_id = update.effective_user.id

    # Delete the password message for security
    try:
        await update.message.delete()
    except Exception:
        pass

    msg = await update.effective_chat.send_message("⏳ Logging in, please wait...")

    loop = asyncio.get_event_loop()
    try:
        cookie_str = await loop.run_in_executor(
            executor,
            lambda: login_and_get_cookie(email, password)
        )
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
```

- [ ] **Step 4: Register the login conversation handler in `main()`**

Add after `cookie_conv = ConversationHandler(...)`:

```python
login_conv = ConversationHandler(
    entry_points=[CommandHandler('login', login_command)],
    states={
        AWAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, email_received)],
        AWAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_received)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
```

And add `application.add_handler(login_conv)` alongside the other handlers.

- [ ] **Step 5: Update `/start` help text to mention `/login`**

Change the start message to:
```python
await update.message.reply_text(
    "👋 <b>Welcome to SIT RBS Room Checker!</b>\n\n"
    "Commands:\n"
    "/login — Log in with your SIT credentials\n"
    "/check — Check room availability\n"
    "/cookie — Manually update your session cookie\n\n"
    "Use /login to get started.",
    parse_mode='HTML'
)
```

- [ ] **Step 6: Commit**

```bash
git add shared/scraper.py bot/bot.py
git commit -m "feat: add /login command for password-based auth on mobile"
```

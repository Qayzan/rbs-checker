# shared/store.py
from __future__ import annotations
import json
import os
import secrets
import time

_ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COOKIES_FILE = os.path.join(_ROOT, 'bot', 'cookies.json')
_TOKENS_FILE  = os.path.join(_ROOT, 'bot', 'pending_logins.json')


# ── Cookie store ─────────────────────────────────────────────────────────────

def _load_cookies() -> dict:
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            return json.load(f)
    return {}

def save_cookie(user_id: int, cookie_str: str) -> None:
    data = _load_cookies()
    data[str(user_id)] = cookie_str
    with open(_COOKIES_FILE, 'w') as f:
        json.dump(data, f)

def get_cookie(user_id: int) -> str | None:
    return _load_cookies().get(str(user_id))

def delete_cookie(user_id: int) -> None:
    data = _load_cookies()
    data.pop(str(user_id), None)
    with open(_COOKIES_FILE, 'w') as f:
        json.dump(data, f)


# ── Login token store ─────────────────────────────────────────────────────────

def _load_tokens() -> dict:
    if os.path.exists(_TOKENS_FILE):
        with open(_TOKENS_FILE) as f:
            return json.load(f)
    return {}

def _save_tokens(data: dict) -> None:
    with open(_TOKENS_FILE, 'w') as f:
        json.dump(data, f)

def create_login_token(tg_user_id: int, ttl: int = 600) -> str:
    """Create a one-time login token valid for ttl seconds (default 10 min)."""
    data = _load_tokens()
    now  = time.time()
    # Prune expired tokens
    data = {k: v for k, v in data.items() if v['expires'] > now}
    token = secrets.token_urlsafe(32)
    data[token] = {'user_id': tg_user_id, 'expires': now + ttl}
    _save_tokens(data)
    return token

def is_valid_token(token: str) -> bool:
    entry = _load_tokens().get(token)
    return bool(entry and time.time() < entry['expires'])

def consume_login_token(token: str) -> int | None:
    """Return the Telegram user ID if the token is valid, then delete it."""
    data  = _load_tokens()
    entry = data.pop(token, None)
    _save_tokens(data)
    if entry and time.time() < entry['expires']:
        return int(entry['user_id'])
    return None

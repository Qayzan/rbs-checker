# shared/scraper.py
"""Talks to SIT's Resource Booking System (RBS) through a hidden Chromium browser.

Speed notes (measured Nov 2026):
- RBS answers only ONE time-slot lookup per logged-in session at a time
  (~1.4 s each), so firing lookups in parallel does not help. The real levers
  are: look up fewer rooms (building filter), skip work (caching, in the bot),
  and avoid fixed waits / browser start-up.
"""
import asyncio
import time
import logging
import os
from shared.slot_cache import slot_cache
from shared.session_pool import SessionPool
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

RBS_SEARCH_URL = "https://rbs.singaporetech.edu.sg/SRB001/SRB001Page"

# Rooms seen in the most recent search for each date: [{'id', 'name'}].
# Together with slot_cache this lets a repeat search be answered with no browser.
_room_catalog = {}
_ROOM_CATALOG_DATES = 14

# Pages whose search form was just loaded by warm_up(), so a check can skip
# reloading it. id(page) -> monotonic time it was loaded.
_fresh_forms = {}
FRESH_FORM_SECONDS = 90


def _remember_rooms(date, cards):
    rooms = [{'id': c.get('rsrcID') or c['rsrcName'], 'name': c['rsrcName']} for c in cards]
    if not rooms:
        return
    _room_catalog.pop(date, None)
    _room_catalog[date] = rooms
    while len(_room_catalog) > _ROOM_CATALOG_DATES:
        _room_catalog.pop(next(iter(_room_catalog)))


def cached_result(date, start_time, end_time, building=None):
    """Build a full result from recently fetched slots, or None if anything is missing.

    No browser and no request to SIT: used when every room in the search was
    looked up recently (e.g. by a whole-day check a minute ago).
    """
    rooms = _room_catalog.get(date)
    if not rooms:
        return None
    if building:
        rooms = [r for r in rooms if building_of(r['name']) == building.upper()]
        if not rooms:
            return None
    fully, partial, none_list, oldest = [], [], [], 0
    for room in rooms:
        hit = slot_cache.get(room['id'], date, start_time, end_time)
        if not hit:
            return None
        data, age = hit
        oldest = max(oldest, age)
        slots = [{'time': s['SLT_Desc'], 'avail': s['SLT_STATUS'] == 1} for s in data]
        avail = sum(s['avail'] for s in slots)
        if avail == len(slots) and slots:
            fully.append({'name': room['name'], 'slots': [s['time'] for s in slots]})
        elif avail:
            partial.append({'name': room['name'], 'avail': avail, 'total': len(slots), 'slots': slots})
        else:
            none_list.append(room['name'])
    return {'fully': fully, 'partial': partial, 'none': none_list, 'errors': [],
            'buildings': sorted({building_of(r['name']) for r in _room_catalog[date]}),
            'cache_age': oldest, 'metrics': {'from_cache': True, 'rooms': len(rooms)}}


def _login_page(url):
    return any(marker in url.lower() for marker in ('login.microsoftonline', '/adfs', 'sts.singaporetech'))


async def _reset_search_form(page):
    """Keep session cookies, but never reuse a mutated search form DOM."""
    for attempt in range(2):
        await page.goto(RBS_SEARCH_URL, wait_until='domcontentloaded', timeout=20000)
        if _login_page(page.url):
            raise RuntimeError('SESSION_EXPIRED')
        try:
            await page.get_by_role('combobox', name='Resource Type').wait_for(state='visible', timeout=8000)
            return
        except PlaywrightTimeoutError:
            if _login_page(page.url) or await page.locator('input[type="password"], #userNameInput').count():
                raise RuntimeError('SESSION_EXPIRED') from None
            if attempt:
                raise RuntimeError('RBS search form did not load. Please try again shortly.') from None
            logging.warning('RBS search form missing; reloading once')


def building_of(room_name: str) -> str:
    """'E2-03-07-DR209' -> 'E2'."""
    return room_name.split('-', 1)[0].strip().upper()


async def _block_heavy(route):
    """Skip downloads nobody looks at (pictures, fonts, video) to load pages faster."""
    if route.request.resource_type in ('image', 'font', 'media'):
        await route.abort()
    else:
        await route.continue_()


async def _wait_for_cards(page, timeout_ms=20000):
    """Wait until the result cards appear and their count stops changing.

    Replaces a fixed 1.5 s sleep: usually finishes in ~0.3 s once cards render.
    """
    # Search completion can legitimately contain no cards. The caller waits
    # for the search response before invoking this stabilisation check.
    last, stable_since = -1, time.monotonic()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        n = await page.locator('.cardwimg').count()
        if n != last:
            last, stable_since = n, time.monotonic()
        elif time.monotonic() - stable_since >= 0.3:
            return
        await page.wait_for_timeout(100)


async def _do_check_async(page, date, start_time, end_time, log_fn,
                          building=None, on_room=None, use_cache=False, preferred_rooms=None):
    """Search discussion rooms and look up each room's 30-minute slots.

    building: e.g. 'E2' to only look up rooms in that building (much faster).
    on_room:  optional async callback(category, entry) called as each room is done,
              category is 'fully' | 'partial' | 'none'.
    """
    started = time.monotonic()
    cache_hits, cache_age, lookup_seconds = 0, 0, 0
    if 'SRB001' not in page.url:   # skip a second load if we're already there
        log_fn('step', 'Loading booking search page...')
        await page.goto(RBS_SEARCH_URL, timeout=20000)
        await page.wait_for_load_state("networkidle", timeout=20000)
    log_fn('progress', done=2, total=5)

    log_fn('step', 'Selecting Discussion Room type...')
    await page.get_by_role('combobox', name='Resource Type').click()
    await page.get_by_role('option', name='Discussion Room').click()
    await page.wait_for_timeout(300)

    log_fn('step', f'Setting date to {date}...')
    await page.evaluate("""
        (date) => {
            var input = document.getElementById('searchSlotDate');
            var months = {'Jan':0,'Feb':1,'Mar':2,'Apr':3,'May':4,'Jun':5,
                          'Jul':6,'Aug':7,'Sep':8,'Oct':9,'Nov':10,'Dec':11};
            var parts = date.split(' ');
            var d = new Date(parseInt(parts[2]), months[parts[1]], parseInt(parts[0]));
            input.removeAttribute('readonly');
            input.value = date;
            input.setAttribute('readonly', 'readonly');
            input.setAttribute('day', d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate());
        }
    """, date)

    log_fn('step', f'Setting time {start_time} → {end_time}...')
    await page.select_option('#SearchHoursFrom', label=start_time)
    await page.select_option('#SearchHoursTo', label=end_time)
    log_fn('progress', done=3, total=5)

    log_fn('step', 'Searching for rooms...')
    async with page.expect_response(
        lambda response: response.request.method in ('GET', 'POST')
        and 'rbs.singaporetech.edu.sg' in response.url
        and 'SRB001' in response.url
        and response.request.resource_type in ('xhr', 'fetch', 'document'),
        timeout=20000,
    ) as search_response:
        await page.get_by_role('button', name='Search', exact=True).click()
    response = await search_response.value
    if response.status >= 400:
        raise RuntimeError('Room search failed; please try again.')
    # Reading the body also waits for completion, without Response.finished()'s
    # detached target-close watcher in the installed Playwright version.
    await response.body()
    await _wait_for_cards(page)
    log_fn('progress', done=4, total=5)

    cards = await page.evaluate("""
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

    all_buildings = sorted({building_of(c['rsrcName']) for c in cards})
    _remember_rooms(date, cards)
    if building:
        cards = [c for c in cards if building_of(c['rsrcName']) == building.upper()]

    preferred = {name: i for i, name in enumerate(preferred_rooms or [])}
    cards.sort(key=lambda c: preferred.get(c['rsrcName'], len(preferred)))
    setup_seconds = time.monotonic()-started
    total_rooms = len(cards)
    where = f' in {building}' if building else ''
    log_fn('info', f'Found {total_rooms} rooms{where}. Checking each one...')

    fully, partial, none_list, errors = [], [], [], []

    async def emit(category, entry):
        if on_room:
            try:
                await on_room(category, entry)
            except Exception:
                pass

    for i, card in enumerate(cards):
        room_name = card['rsrcName']
        log_fn('step', f'[{i+1}/{total_rooms}] {room_name}')

        try:
            room_key = card.get('rsrcID') or room_name
            hit = slot_cache.get(room_key, date, start_time, end_time) if use_cache else None
            if hit:
                data, age = hit
                cache_hits += 1
                cache_age = max(cache_age, age)
            else:
                lookup_started = time.monotonic()
                data = await page.evaluate("""
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
                for (let attempt = 0; attempt < 2; attempt++) {
                try {
                const resp = await fetch('/SRB001/GetTimeSlotListByresidNdatetime', {
                    method: 'POST',
                    signal: AbortSignal.timeout(15000),
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: params.toString()
                });
                const text = await resp.text();
                if (resp.redirected && /login|adfs|sts[.]/i.test(resp.url)) return 'SESSION_LOGIN';
                if (resp.status === 401 || resp.status === 403) return 'SESSION_LOGIN';
                if (!resp.ok) throw new Error('HTTP_ERROR');
                return JSON.parse(text);
                } catch (e) {
                    if (attempt === 1) return 'LOOKUP_FAILED';
                    await new Promise(resolve => setTimeout(resolve, 500));
                }
                }
            }
            """, card)
                lookup_seconds += time.monotonic()-lookup_started

            if isinstance(data, str):
                if 'login' in data.lower():
                    raise RuntimeError('SESSION_EXPIRED')
                errors.append(room_name)
                log_fn('warn', f'  ↳ unexpected response, skipping')
                await emit('errors', room_name)
            else:
                if not isinstance(data, list) or not data or any(
                    not isinstance(s, dict) or 'SLT_STATUS' not in s or 'SLT_Desc' not in s
                    for s in data
                ):
                    raise ValueError('Empty or invalid slot response')
                if not hit:
                    slot_cache.put(room_key, date, data)
                total = len(data)
                avail = sum(1 for s in data if s['SLT_STATUS'] == 1)
                slots = [{'time': s['SLT_Desc'], 'avail': s['SLT_STATUS'] == 1} for s in data]

                if avail == total and total > 0:
                    entry = {'name': room_name, 'slots': [s['time'] for s in slots]}
                    fully.append(entry)
                    log_fn('done', f'  ↳ ✓ fully available ({avail}/{total} slots)')
                    await emit('fully', entry)
                elif avail > 0:
                    entry = {'name': room_name, 'avail': avail, 'total': total, 'slots': slots}
                    partial.append(entry)
                    log_fn('info', f'  ↳ {avail}/{total} slots free')
                    await emit('partial', entry)
                else:
                    none_list.append(room_name)
                    log_fn('warn', f'  ↳ fully booked')
                    await emit('none', room_name)

        except Exception as exc:
            if 'SESSION_EXPIRED' in str(exc):
                raise
            errors.append(room_name)
            log_fn('warn', f'  ↳ error: {str(exc)[:60]}')
            await emit('errors', room_name)

        log_fn('progress', done=4 + (i + 1), total=4 + total_rooms)

    log_fn('done', f'All done! ✅ {len(fully)} fully available  \U0001f7e1 {len(partial)} partial  ❌ {len(none_list)} booked')
    metrics = {'setup_seconds': round(setup_seconds, 3), 'lookup_seconds': round(lookup_seconds, 3),
               'total_seconds': round(time.monotonic()-started, 3), 'rooms': total_rooms,
               'slot_cache_hits': cache_hits}
    logging.info('RBS timing %s', metrics)
    return {'fully': fully, 'partial': partial, 'none': none_list, 'errors': errors,
            'buildings': all_buildings, 'cache_age': cache_age + time.monotonic()-started, 'metrics': metrics}


# ---------------------------------------------------------------------------
# Shared browser for the long-running Telegram bot.
# Starting Chromium costs a few seconds, so the bot keeps one open between
# checks and closes it after IDLE_SECS without use to give the memory back.
# ---------------------------------------------------------------------------
class _BrowserKeeper:
    IDLE_SECS = max(10, int(os.environ.get('RBS_BROWSER_IDLE_SECONDS', '600')))

    def __init__(self):
        self._pw = None
        self._browser = None
        self._lock = None
        self._users = 0
        self._idle_task = None

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        if self._pw is None:
            self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self._browser

    @asynccontextmanager
    async def context(self):
        async with self._get_lock():
            if self._idle_task:
                self._idle_task.cancel()
                self._idle_task = None
            browser = await self._ensure_browser()
            self._users += 1
        ctx = None
        try:
            ctx = await browser.new_context()
            await ctx.route("**/*", _block_heavy)
            yield ctx
        finally:
            if ctx is not None:
                try:
                    await ctx.close()
                except Exception:
                    pass
            async with self._get_lock():
                self._users -= 1
                if self._users == 0:
                    self._idle_task = asyncio.get_running_loop().create_task(self._close_later())

    async def _close_later(self):
        try:
            await asyncio.sleep(self.IDLE_SECS)
        except asyncio.CancelledError:
            return
        async with self._get_lock():
            if self._users:
                return
            try:
                if self._browser:
                    await self._browser.close()
                if self._pw:
                    await self._pw.stop()
            except Exception:
                pass
            self._browser = None
            self._pw = None
            self._idle_task = None


_keeper = _BrowserKeeper()
_sessions = SessionPool(_keeper,
    idle_seconds=max(5, int(os.environ.get('RBS_SESSION_IDLE_SECONDS', '120'))),
    capacity=max(1, int(os.environ.get('RBS_SESSION_CAPACITY', '8'))))


async def _sign_in(page, username, password, fail_prefix):
    await page.goto(RBS_SEARCH_URL, timeout=20000)
    await page.wait_for_selector(
        '#userNameInput, input[name="UserName"], input[placeholder="someone@example.com"]',
        timeout=15000
    )
    await page.fill('input[placeholder="someone@example.com"]', username)
    await page.fill('input[type="password"]', password)
    await page.locator('#submitButton, input[type="submit"], button[type="submit"]').first.click()

    try:
        await page.wait_for_selector(
            '[data-bind*="errorText"], #errorText, #usernameError, '
            '.alert-error, [class*="error"], [id*="error"]',
            timeout=3000
        )
        error_el = page.locator(
            '[data-bind*="errorText"], #errorText, #usernameError, '
            '.alert-error, [class*="error"], [id*="error"]'
        ).first
        error_text = (await error_el.inner_text()).strip()
        raise Exception(f"{fail_prefix}: {error_text or 'incorrect email or password.'}")
    except Exception as e:
        if fail_prefix in str(e):
            raise

    await page.wait_for_url("**/rbs.singaporetech.edu.sg/**", timeout=20000)
    await page.wait_for_load_state("networkidle", timeout=20000)

    if 'login.microsoftonline' in page.url or 'singaporetech.edu.sg/adfs' in page.url \
            or 'sts.singaporetech' in page.url:
        raise Exception(f"{fail_prefix}: incorrect email or password.")


def check_rooms(username, password, date, start_time, end_time, log_fn, building=None):
    """Used by the Flask web app (one-off browser per check)."""
    async def _inner():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                await context.route("**/*", _block_heavy)
                page = await context.new_page()
                log_fn('step', 'Signing in...')
                await _sign_in(page, username, password, "Login failed")
                log_fn('info', 'Login successful.')
                log_fn('progress', done=1, total=5)
                return await _do_check_async(page, date, start_time, end_time, log_fn, building)
            finally:
                await browser.close()

    return asyncio.run(_inner())


async def login_and_get_cookie(username, password):
    """Log in with username/password and return the session cookie string.

    Raises Exception("LOGIN_FAILED: <reason>") on bad credentials.
    """
    async with _keeper.context() as context:
        page = await context.new_page()
        await _sign_in(page, username, password, "LOGIN_FAILED")
        cookies = await context.cookies()
        return '; '.join(
            f"{c['name']}={c['value']}" for c in cookies
            if 'singaporetech.edu.sg' in c.get('domain', '')
        )


async def warm_up(cookie_string):
    """Start the browser and load the search form for this session, ahead of a check.

    Called when someone taps "Check rooms", so the slow part (starting Chromium,
    opening RBS) happens while they pick a day, building and time.
    Returns False if SIT says the session has expired.
    """
    async with _sessions.page(cookie_string, _cookie_list(cookie_string)) as (page, reused):
        if time.monotonic() - _fresh_forms.get(id(page), -1e9) < FRESH_FORM_SECONDS / 2:
            return True
        try:
            await _reset_search_form(page)
        except RuntimeError as exc:
            if str(exc) == 'SESSION_EXPIRED':
                return False
            raise
        _fresh_forms[id(page)] = time.monotonic()
        return True


async def check_rooms_with_cookie(cookie_string, date, start_time, end_time, log_fn,
                                  building=None, on_room=None, use_cache=False, preferred_rooms=None):
    """Used by the Telegram bot. Authenticates using a saved session cookie.

    Raises Exception("SESSION_EXPIRED") if the cookie is invalid or expired.
    """
    opening_started = time.monotonic()
    cookies = _cookie_list(cookie_string)

    async with _sessions.page(cookie_string, cookies) as (page, reused):

        log_fn('step', 'Opening RBS with your saved session...')
        loaded_at = _fresh_forms.pop(id(page), None)
        if reused and loaded_at is not None and time.monotonic() - loaded_at < FRESH_FORM_SECONDS \
                and 'SRB001' in page.url:
            log_fn('info', 'Search form was already loaded while you were choosing.')
        else:
            await _reset_search_form(page)
            log_fn('info', 'Reusing session with a fresh search form.' if reused else 'Opened a fresh RBS page.')

        log_fn('info', 'Session valid. Proceeding...')
        log_fn('progress', done=1, total=5)
        opening_seconds = time.monotonic()-opening_started
        logging.info('RBS page_seconds=%.3f page_reused=%s', opening_seconds, reused)
        return await _do_check_async(page, date, start_time, end_time, log_fn,
                                     building=building, on_room=on_room,
                                     use_cache=use_cache, preferred_rooms=preferred_rooms)


def _cookie_list(cookie_string):
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

    return cookies


async def verify_session_cookie(cookie_string):
    """Verify against SIT in a fresh context, independent of any warm page."""
    async with _keeper.context() as context:
        await context.add_cookies(_cookie_list(cookie_string))
        page = await context.new_page()
        try:
            await _reset_search_form(page)
        except RuntimeError as exc:
            if str(exc) == 'SESSION_EXPIRED':
                return False
            raise
        return True

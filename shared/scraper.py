# shared/scraper.py
import os
from playwright.sync_api import sync_playwright
from playwright.async_api import async_playwright


def _do_check_sync(page, date, start_time, end_time, log_fn):
    """Sync version of the room check for threaded web-app workers."""
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

    log_fn('step', f'Setting time {start_time} → {end_time}...')
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
                log_fn('warn', f'  ↳ unexpected response, skipping')
                continue

            total = len(data)
            avail = sum(1 for s in data if s['SLT_STATUS'] == 1)
            slots = [{'time': s['SLT_Desc'], 'avail': s['SLT_STATUS'] == 1} for s in data]

            if avail == total and total > 0:
                fully.append({'name': room_name, 'slots': [s['time'] for s in slots if s['avail']]})
                log_fn('done', f'  ↳ ✓ fully available ({avail}/{total} slots)')
            elif avail > 0:
                partial.append({'name': room_name, 'avail': avail, 'total': total, 'slots': slots})
                log_fn('info', f'  ↳ {avail}/{total} slots free')
            else:
                none_list.append(room_name)
                log_fn('warn', f'  ↳ fully booked')

        except Exception as exc:
            none_list.append(room_name + ' (error)')
            log_fn('warn', f'  ↳ error: {str(exc)[:60]}')

        log_fn('progress', done=4 + (i + 1), total=4 + total_rooms)

    log_fn('done', f'All done! ✅ {len(fully)} fully available  🟡 {len(partial)} partial  ❌ {len(none_list)} booked')
    return {'fully': fully, 'partial': partial, 'none': none_list}


async def _do_check_async(page, date, start_time, end_time, log_fn):
    """Async version of _do_check for use with async_playwright."""
    log_fn('step', 'Loading booking search page...')
    await page.goto("https://rbs.singaporetech.edu.sg/SRB001/SRB001Page", timeout=15000)
    await page.wait_for_load_state("networkidle", timeout=15000)
    log_fn('progress', done=2, total=5)

    log_fn('step', 'Selecting Discussion Room type...')
    await page.get_by_role('combobox', name='Resource Type').click()
    await page.wait_for_timeout(500)
    await page.get_by_role('option', name='Discussion Room').click()
    await page.wait_for_timeout(500)

    log_fn('step', f'Setting date to {date}...')
    await page.evaluate(f"""
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
    await page.wait_for_timeout(300)

    log_fn('step', f'Setting time {start_time} \u2192 {end_time}...')
    await page.select_option('#SearchHoursFrom', label=start_time)
    await page.select_option('#SearchHoursTo', label=end_time)
    log_fn('progress', done=3, total=5)

    log_fn('step', 'Searching for available rooms...')
    await page.get_by_role('button', name='Search', exact=True).click()
    await page.wait_for_selector('.cardwimg', timeout=20000)
    await page.wait_for_timeout(1500)
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

    total_rooms = len(cards)
    log_fn('info', f'Found {total_rooms} rooms. Checking each one...')

    fully, partial, none_list = [], [], []

    for i, card in enumerate(cards):
        room_name = card['rsrcName']
        log_fn('step', f'[{i+1}/{total_rooms}] {room_name}')

        try:
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

    log_fn('done', f'All done! \u2705 {len(fully)} fully available  \U0001f7e1 {len(partial)} partial  \u274c {len(none_list)} booked')
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
            browser.close()
            if error_text:
                raise Exception(f"Login failed: {error_text}")
            raise Exception("Login failed: incorrect email or password.")
        except Exception as e:
            if "Login failed" in str(e):
                raise

        page.wait_for_url("**/rbs.singaporetech.edu.sg/**", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if 'login.microsoftonline' in page.url or 'sts.singaporetech' in page.url:
            browser.close()
            raise Exception("Login failed: incorrect email or password.")

        log_fn('info', 'Login successful.')
        log_fn('progress', done=1, total=5)

        result = _do_check_sync(page, date, start_time, end_time, log_fn)
        browser.close()
        return result


async def login_and_get_cookie(username, password):
    """Log in with username/password and return the session cookie string.

    Raises Exception("LOGIN_FAILED: <reason>") on bad credentials.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto("https://rbs.singaporetech.edu.sg/SRB001/SRB001Page", timeout=20000)
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
            await browser.close()
            if error_text:
                raise Exception(f"LOGIN_FAILED: {error_text}")
            raise Exception("LOGIN_FAILED: incorrect email or password.")
        except Exception as e:
            if "LOGIN_FAILED" in str(e):
                raise

        await page.wait_for_url("**/rbs.singaporetech.edu.sg/**", timeout=20000)
        await page.wait_for_load_state("networkidle", timeout=20000)

        if 'login.microsoftonline' in page.url or 'sts.singaporetech' in page.url:
            await browser.close()
            raise Exception("LOGIN_FAILED: incorrect email or password.")

        cookies = await page.context.cookies()
        cookie_str = '; '.join(
            f"{c['name']}={c['value']}" for c in cookies
            if 'singaporetech.edu.sg' in c.get('domain', '')
        )
        await browser.close()
        return cookie_str


async def check_rooms_with_cookie(cookie_string, date, start_time, end_time, log_fn):
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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        log_fn('step', 'Navigating to RBS with saved session...')
        await page.goto("https://rbs.singaporetech.edu.sg/SRB001/SRB001Page", timeout=20000)
        await page.wait_for_load_state("networkidle", timeout=20000)

        if 'login.microsoftonline' in page.url or 'sts.singaporetech' in page.url:
            await browser.close()
            raise Exception("SESSION_EXPIRED")

        log_fn('info', 'Session valid. Proceeding...')
        log_fn('progress', done=1, total=5)

        result = await _do_check_async(page, date, start_time, end_time, log_fn)
        await browser.close()
        return result

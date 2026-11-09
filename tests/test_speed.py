import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from shared.scraper import _reset_search_form, PlaywrightTimeoutError

from shared.slot_cache import SlotCache
from shared.session_pool import SessionPool
from bot.bot import _clip_result, _matches


class SlotTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.cache = SlotCache(clock=lambda: self.now)
        self.slots = [{'SLT_Desc': t, 'SLT_STATUS': 1} for t in ('12:00', '12:30', '13:00')]

    def test_subset_reuses_slots_without_resetting_age(self):
        self.cache.put('r', 'day', self.slots)
        self.now = 45
        slots, age = self.cache.get('r', 'day', '12:30', '13:30')
        self.assertEqual(len(slots), 2)
        self.assertEqual(age, 45)
        self.assertIsNone(self.cache.get('r', 'day', '12:30', '14:00'))

    def test_expired_and_other_dates_never_reused(self):
        self.cache.put('r', 'day', self.slots)
        self.assertIsNone(self.cache.get('r', 'other', '12:00', '12:30'))
        self.now = 120
        self.assertIsNone(self.cache.get('r', 'day', '12:00', '12:30'))

    def test_cache_is_bounded_and_returns_copies(self):
        self.cache.capacity = 2
        self.cache.put('r', 'day', self.slots)
        self.assertEqual(len(self.cache.data), 2)
        slots, _ = self.cache.get('r', 'day', '12:30', '13:00')
        slots[0]['SLT_STATUS'] = 0
        self.assertEqual(self.cache.get('r', 'day', '12:30', '13:00')[0][0]['SLT_STATUS'], 1)

    def test_alert_cannot_match_outside_own_window(self):
        result = {'fully': [{'name': 'A', 'slots': ['12:00', '12:30', '13:00', '13:30']}],
                  'partial': [], 'none': [], 'errors': []}
        q = {'start': '13:00', 'end': '14:00', 'duration': 120}
        clipped = _clip_result(result, q)
        self.assertEqual(clipped['fully'][0]['slots'], ['13:00', '13:30'])
        self.assertEqual(_matches(clipped, q), [])


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.created, self.closed = [], []
        @asynccontextmanager
        async def context():
            page = SimpleNamespace(is_closed=lambda: False)
            ctx = SimpleNamespace(add_cookies=AsyncMock(), new_page=AsyncMock(return_value=page))
            self.created.append(ctx)
            try:
                yield ctx
            finally:
                self.closed.append(ctx)
        self.pool = SessionPool(SimpleNamespace(context=context))

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_reuse_and_user_isolation(self):
        async with self.pool.page('one', []) as (first, reused):
            self.assertFalse(reused)
        async with self.pool.page('one', []) as (second, reused):
            self.assertTrue(reused)
            self.assertIs(first, second)
        async with self.pool.page('two', []) as (other, reused):
            self.assertFalse(reused)
            self.assertIsNot(first, other)
        self.assertEqual(len(self.created), 2)

    async def test_failed_search_discards_page(self):
        with self.assertRaises(ValueError):
            async with self.pool.page('one', []):
                raise ValueError('failed')
        self.assertEqual(len(self.closed), 1)
        self.assertEqual(self.pool.records, {})

    async def test_idle_expiry_releases_context(self):
        self.pool.idle_seconds = 0
        async with self.pool.page('one', []):
            pass
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(len(self.closed), 1)

    async def test_logout_releases_only_its_session(self):
        async with self.pool.page('one', []):
            pass
        async with self.pool.page('two', []):
            pass
        await self.pool.close('one')
        self.assertEqual(len(self.closed), 1)
        self.assertEqual(len(self.pool.records), 1)


class FormTests(unittest.IsolatedAsyncioTestCase):
    def page(self, failures=None):
        locator = SimpleNamespace(wait_for=AsyncMock(side_effect=failures))
        return SimpleNamespace(url='https://rbs.singaporetech.edu.sg/SRB001/SRB001Page',
            goto=AsyncMock(), get_by_role=lambda *a, **kw: locator,
            locator=lambda *a: SimpleNamespace(count=AsyncMock(return_value=0)))

    async def test_repeat_search_always_reloads_form(self):
        page = self.page()
        await _reset_search_form(page)
        await _reset_search_form(page)
        self.assertEqual(page.goto.await_count, 2)

    async def test_missing_form_reloads_once(self):
        page = self.page([PlaywrightTimeoutError('missing'), None])
        await _reset_search_form(page)
        self.assertEqual(page.goto.await_count, 2)

    async def test_expired_session_is_recognised(self):
        page = self.page()
        page.url = 'https://login.microsoftonline.com/login'
        with self.assertRaisesRegex(RuntimeError, 'SESSION_EXPIRED'):
            await _reset_search_form(page)

    async def test_persistent_missing_form_has_clear_error(self):
        page = self.page(PlaywrightTimeoutError('missing'))
        with self.assertRaisesRegex(RuntimeError, 'search form did not load'):
            await _reset_search_form(page)
        self.assertEqual(page.goto.await_count, 2)

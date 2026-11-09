"""Tests for whole-day fetching, instant answers from saved slots, warm-up and the result layout."""
import asyncio
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import bot as b
from shared import scraper
from shared.slot_cache import SlotCache


def tomorrow():
    return b._fmt_date(b._now() + timedelta(days=1))


def query(**kw):
    return dict(dict(date=tomorrow(), start='12:00', end='14:00', building='E2'), **kw)


def day_slots(free):
    """RBS-style slots 07:00-22:00; `free` is a set of free start times."""
    out, t = [], 7 * 60
    while t < 22 * 60:
        hhmm = b._to_hhmm(t)
        out.append({'SLT_Desc': hhmm, 'SLT_STATUS': 1 if hhmm in free else 3})
        t += 30
    return out


class FetchWindowTests(unittest.TestCase):
    def test_future_day_fetches_whole_day(self):
        self.assertEqual(b._fetch_window(query()), ('07:00', '22:00'))

    def test_today_starts_at_current_half_hour(self):
        now = b._now().replace(hour=15, minute=10)
        with patch.object(b, '_now', return_value=now):
            q = query(date=b._fmt_date(now), start='16:00', end='18:00')
            self.assertEqual(b._fetch_window(q), ('15:00', '22:00'))


class SharedLookupNarrowingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        b._inflight.clear()

    async def test_fetches_whole_day_but_reports_only_own_window(self):
        whole = {'fully': [], 'none': [], 'errors': [], 'partial': [{
            'name': 'E2-A', 'slots': [{'time': t, 'avail': t in ('09:00', '12:30', '13:00')}
                                      for t in ('09:00', '12:00', '12:30', '13:00', '13:30')]}]}
        seen = []

        async def scrape(cookie, date, start, end, log, building=None, on_room=None, **kw):
            self.assertEqual((start, end), ('07:00', '22:00'))
            await on_room('partial', whole['partial'][0])
            return whole

        async def on_room(cat, entry):
            seen.append((cat, entry))

        with patch.object(b, 'check_rooms_with_cookie', scrape):
            r = await b._shared_lookup(query(), 'c', lambda *a, **k: None, on_room)
        times = [s['time'] for s in r['partial'][0]['slots']]
        self.assertEqual(times, ['12:00', '12:30', '13:00', '13:30'])
        self.assertEqual(b._free_ranges(r['partial'][0]['slots']), [('12:30', '13:30')])
        # The live view never shows the 09:00 slot outside the person's window.
        self.assertEqual(len(seen), 1)
        self.assertNotIn('09:00', [s['time'] for s in seen[0][1]['slots']])

    async def test_two_windows_same_day_share_one_trip(self):
        release = asyncio.Event()
        calls = []

        async def scrape(cookie, date, start, end, *a, **kw):
            calls.append((start, end))
            await release.wait()
            return {'fully': [{'name': 'E2-A', 'slots': [s['SLT_Desc'] for s in day_slots(set())]}],
                    'partial': [], 'none': [], 'errors': []}

        with patch.object(b, 'check_rooms_with_cookie', scrape):
            one = asyncio.create_task(b._shared_lookup(query(), 'x', lambda *a, **k: None))
            await asyncio.sleep(0)
            two = asyncio.create_task(b._shared_lookup(query(start='15:00', end='16:00'), 'y',
                                                       lambda *a, **k: None))
            await asyncio.sleep(0)
            release.set()
            r1, r2 = await one, await two
        self.assertEqual(len(calls), 1)
        self.assertEqual(r1['fully'][0]['slots'], ['12:00', '12:30', '13:00', '13:30'])
        self.assertEqual(r2['fully'][0]['slots'], ['15:00', '15:30'])


class CachedResultTests(unittest.TestCase):
    def setUp(self):
        self.cache = SlotCache()
        self.p = patch.object(scraper, 'slot_cache', self.cache)
        self.p.start()
        scraper._room_catalog.clear()

    def tearDown(self):
        self.p.stop()
        scraper._room_catalog.clear()

    def test_any_window_answered_from_a_whole_day_lookup(self):
        date = tomorrow()
        scraper._remember_rooms(date, [{'rsrcID': '1', 'rsrcName': 'E2-A'},
                                       {'rsrcID': '2', 'rsrcName': 'E2-B'},
                                       {'rsrcID': '3', 'rsrcName': 'W3-C'}])
        self.cache.put('1', date, day_slots({'12:00', '12:30'}))
        self.cache.put('2', date, day_slots(set()))
        r = scraper.cached_result(date, '12:00', '13:00', 'E2')
        self.assertEqual([x['name'] for x in r['fully']], ['E2-A'])
        self.assertEqual(r['none'], ['E2-B'])
        self.assertTrue(r['metrics']['from_cache'])
        # W3-C was never looked up, so "any building" can't be answered from saved data.
        self.assertIsNone(scraper.cached_result(date, '12:00', '13:00'))

    def test_unknown_date_is_not_answered(self):
        self.assertIsNone(scraper.cached_result(tomorrow(), '12:00', '13:00', 'E2'))


class PerformCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(b, n, str(Path(self.tmp.name) / n))
                        for n in ('PREFERENCES_FILE', 'ALERTS_FILE', 'COOKIES_FILE', 'BUILDINGS_FILE')]
        for p in self.patches:
            p.start()
        b._result_cache.clear()
        b._save_cookie(1, 'cookie')
        self.msg = SimpleNamespace(message_id=5, edit_text=AsyncMock())
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                      effective_chat=SimpleNamespace(id=2, send_message=AsyncMock()))
        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace(
            edit_message_text=AsyncMock(), delete_message=AsyncMock()))

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    async def test_saved_slots_answer_without_browser(self):
        saved = {'fully': [{'name': 'E2-A', 'slots': ['12:00', '12:30', '13:00', '13:30']}],
                 'partial': [], 'none': [], 'errors': [], 'cache_age': 30}
        with patch.object(b, 'cached_result', return_value=saved), \
             patch.object(b, '_shared_lookup', AsyncMock()) as browser:
            await b._perform_check(self.update, self.context, self.msg, search_query=query())
        browser.assert_not_awaited()
        text = self.context.bot.edit_message_text.call_args.args[0]
        self.assertIn('Best pick: E2-A', text)
        self.assertIn('Checked 0m 30s ago', text)

    async def test_check_again_ignores_saved_slots(self):
        fresh = {'fully': [], 'partial': [], 'none': ['E2-A'], 'errors': []}
        with patch.object(b, 'cached_result', return_value={'fully': [], 'partial': [], 'none': []}) as saved, \
             patch.object(b, '_shared_lookup', AsyncMock(return_value=fresh)) as browser:
            await b._perform_check(self.update, self.context, self.msg, force_fresh=True,
                                   search_query=query())
        saved.assert_not_called()
        browser.assert_awaited_once()


class WarmUpTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_skips_reload_after_warm_up(self):
        page = SimpleNamespace(url=scraper.RBS_SEARCH_URL)

        @asynccontextmanager
        async def fake_page(cookie, cookies):
            yield page, True

        with patch.object(scraper._sessions, 'page', fake_page), \
             patch.object(scraper, '_reset_search_form', AsyncMock()) as reset, \
             patch.object(scraper, '_do_check_async', AsyncMock(return_value={})):
            await scraper.warm_up('c=1')
            self.assertEqual(reset.await_count, 1)
            await scraper.check_rooms_with_cookie('c=1', 'd', '12:00', '13:00', lambda *a, **k: None)
            self.assertEqual(reset.await_count, 1)       # form reused, not reloaded
            await scraper.check_rooms_with_cookie('c=1', 'd', '12:00', '13:00', lambda *a, **k: None)
            self.assertEqual(reset.await_count, 2)       # used once; next check reloads

    async def test_warm_up_reports_expired_session(self):
        @asynccontextmanager
        async def fake_page(cookie, cookies):
            yield SimpleNamespace(url=''), False

        with patch.object(scraper._sessions, 'page', fake_page), \
             patch.object(scraper, '_reset_search_form', AsyncMock(side_effect=RuntimeError('SESSION_EXPIRED'))):
            self.assertFalse(await scraper.warm_up('expired=1'))

    async def test_tapping_check_starts_one_warm_up(self):
        tmp = tempfile.TemporaryDirectory()
        with patch.object(b, 'COOKIES_FILE', str(Path(tmp.name) / 'c.json')), \
             patch.object(b, 'warm_up', AsyncMock(return_value=True)) as warm:
            b._save_cookie(7, 'c')
            ctx = SimpleNamespace(user_data={}, application=SimpleNamespace(
                create_task=lambda coro, **kw: asyncio.create_task(coro)))
            upd = SimpleNamespace(effective_user=SimpleNamespace(id=7),
                                  message=SimpleNamespace(reply_text=AsyncMock()))
            await b.check_command(upd, ctx)
            await b.check_command(upd, ctx)       # second tap while warming: no duplicate
            await asyncio.gather(*b._warmups.values())
        tmp.cleanup()
        warm.assert_awaited_once()


class LayoutTests(unittest.IsolatedAsyncioTestCase):
    def result(self):
        return {'fully': [], 'none': ['E2-C'], 'errors': [], 'partial': [
            {'name': 'E2-A', 'slots': [{'time': '12:00', 'avail': True}, {'time': '12:30', 'avail': False}]},
            {'name': 'E2-B', 'slots': [{'time': '12:00', 'avail': True}, {'time': '12:30', 'avail': True},
                                       {'time': '13:00', 'avail': True}, {'time': '13:30', 'avail': False}]}]}

    def test_best_pick_is_longest_stretch_and_empty_sections_hidden(self):
        text = b._format_results(self.result(), query())
        self.assertIn('Best pick: E2-B</b>, free 12:00–13:30 (1h 30m)', text)
        self.assertNotIn('Free the whole time', text)
        self.assertIn('Taken the whole time (1)', text)

    def test_no_free_room_says_so(self):
        text = b._format_results({'fully': [], 'partial': [], 'none': ['E2-C'], 'errors': []}, query())
        self.assertIn('No free room in this window', text)
        self.assertNotIn('Best pick', text)

    def test_buttons_are_compact_until_more_options(self):
        short = b._result_rows('t', 0)
        self.assertEqual(len(short), 3)
        self.assertEqual(short[-1][0].callback_data, 'more:t')
        full = b._result_rows('t', 0, expanded=True)
        datas = [btn.callback_data for row in full for btn in row]
        for action in ('watch:t', 'gap60:t', 'fast:t', 'save:t', 'less:t'):
            self.assertIn(action, datas)

    def test_active_filter_button_turns_filter_off(self):
        row = b._result_rows('t', 60)[0]
        self.assertTrue(row[0].text.startswith('✓'))
        self.assertEqual(row[0].callback_data, 'minfree_0:t')
        self.assertEqual(row[1].callback_data, 'minfree_120:t')

    async def test_more_options_only_changes_buttons(self):
        msg = SimpleNamespace(edit_reply_markup=AsyncMock(), reply_text=AsyncMock())
        cb = SimpleNamespace(data='more:tok', answer=AsyncMock(), message=msg)
        ctx = SimpleNamespace(user_data={'search_records': {'tok': {'q': query(), 'result': self.result(),
                                                                    'checked': 0, 'min_free': 60}}})
        upd = SimpleNamespace(callback_query=cb, effective_user=SimpleNamespace(id=1))
        await b.result_action(upd, ctx)
        markup = msg.edit_reply_markup.call_args.args[0]
        datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn('watch:tok', datas)
        self.assertIn('minfree_0:tok', datas)       # keeps the ✓ filter state
        msg.reply_text.assert_not_called()

    async def test_start_is_short_and_help_is_full(self):
        msg = SimpleNamespace(reply_text=AsyncMock())
        upd = SimpleNamespace(message=msg)
        await b.start_command(upd, None)
        start = msg.reply_text.call_args.args[0]
        await b.help_command(upd, None)
        full = msg.reply_text.call_args.args[0]
        self.assertLess(len(start), 450)
        self.assertIn('/login', start)
        self.assertIn('/find', full)


if __name__ == '__main__':
    unittest.main()

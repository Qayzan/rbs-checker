import asyncio
import copy
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import bot as b
from shared import scraper


def query(**kwargs):
    return dict(date=b._fmt_date(b._now() + timedelta(days=1)),
                start='12:00', end='18:00', building='E2', **kwargs)


def result():
    return {'fully': [{'name': 'E2-A', 'slots': ['12:00', '12:30', '13:00', '13:30']}],
            'partial': [{'name': 'E2-B', 'slots': [
                {'time': '14:00', 'avail': True}, {'time': '14:30', 'avail': True}]}],
            'none': ['E2-C'], 'errors': ['E2-D']}


class LogicTests(unittest.TestCase):
    def setUp(self):
        b._result_cache.clear()

    def test_gaps_are_not_merged(self):
        self.assertEqual(b._free_ranges([{'time': t, 'avail': True} for t in ['13:00', '12:00']]),
                         [('12:00', '12:30'), ('13:00', '13:30')])

    def test_booked_slot_breaks_range(self):
        slots = [{'time': '12:00', 'avail': True}, {'time': '12:30', 'avail': False},
                 {'time': '13:00', 'avail': True}]
        self.assertEqual(len(b._free_ranges(slots)), 2)

    def test_duration_and_earliest_ranking(self):
        self.assertEqual([r[2] for r in b._matches(result(), query(duration=60))], ['E2-A', 'E2-B'])
        self.assertEqual(len(b._matches(result(), query(duration=120))), 1)
        self.assertEqual(b._matches(result(), query(duration=180)), [])

    def test_failed_rooms_are_explicit(self):
        text = b._format_results(result(), query())
        self.assertIn('Could not check:', text)
        self.assertNotIn('E2-D', text.split('Could not check:')[0])

    def test_filter_does_not_lower_requested_duration(self):
        text = b._format_results(result(), dict(query(), end='12:30'), 120)
        self.assertNotIn('Free the whole time', text)
        self.assertNotIn('E2-A', text.split('Taken')[0])

    def test_cache_rejects_failed_or_incomplete_results(self):
        b._cache_put(query(), result())
        self.assertIsNone(b._cache_get(query()))
        r = dict(result(), errors=[], incomplete=True)
        b._cache_put(query(), r)
        self.assertIsNone(b._cache_get(query()))

    def test_cache_all_buildings_preserves_errors(self):
        r = result()
        b._result_cache[(query()['date'], '12:00', '18:00', 'ANY')] = (b.time.time(), r)
        self.assertEqual(b._cache_get(query())[0]['errors'], ['E2-D'])

    def test_invalid_queries(self):
        for changes in ({'end': '11:00'}, {'start': '12:15'}, {'duration': 400},
                        {'building': 'BOGUS'}, {'start': '24:00'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                b._validate_query(dict(query(), **changes))

    def test_singapore_time(self):
        self.assertEqual(b._now().utcoffset(), timedelta(hours=8))

    def test_minimum_duration_flexible_results(self):
        self.assertNotIn('E2-B', b._format_search(result(), query(duration=120)))


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(b, name, str(Path(self.tmp.name) / name))
                        for name in ('PREFERENCES_FILE', 'ALERTS_FILE', 'COOKIES_FILE', 'BUILDINGS_FILE')]
        for p in self.patches:
            p.start()
        b._result_cache.clear()
        b._active_checks.clear()
        b._user_locks.clear()
        b.check_slots = asyncio.Semaphore(2)
        self.message = SimpleNamespace(message_id=1, edit_text=AsyncMock(), reply_text=AsyncMock())
        self.cb = SimpleNamespace(data='', answer=AsyncMock(), message=self.message)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                      effective_chat=SimpleNamespace(id=2, send_message=AsyncMock()),
                                      callback_query=self.cb, message=self.message)
        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace(edit_message_text=AsyncMock(),
                                           delete_message=AsyncMock()))
        self.context.application = SimpleNamespace(create_task=lambda coro, **kw: asyncio.create_task(coro))

    async def asyncTearDown(self):
        await b._stop_check(1)
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    async def test_old_button_uses_its_original_query(self):
        original = query(duration=120)
        self.context.user_data.update(date='different', start='08:00', end='09:00',
            search_records={'old': {'q': original, 'result': result(), 'checked': b.time.time()}})
        self.cb.data = 'recheck:old'
        with patch.object(b, '_run_check', new_callable=AsyncMock) as run:
            await b.result_action(self.update, self.context)
            self.assertEqual(run.call_args.args[4], original)

    async def test_status_expired_session_is_deleted(self):
        b._save_cookie(1, 'expired')
        self.message.reply_text.return_value = self.message
        with patch.object(b, 'verify_session_cookie', AsyncMock(return_value=False)):
            await b.status_command(self.update, self.context)
            await b._status_checks[1]
        self.assertIsNone(b._get_cookie(1))
        self.assertIn('session expired', self.message.edit_text.call_args.args[0])

    async def test_status_verified_session_is_logged_in(self):
        b._save_cookie(1, 'valid')
        self.message.reply_text.return_value = self.message
        with patch.object(b, 'verify_session_cookie', AsyncMock(return_value=True)):
            await b.status_command(self.update, self.context)
            await b._status_checks[1]
        self.assertIn('verified with SIT', self.message.edit_text.call_args.args[0])

    async def test_status_network_failure_keeps_session(self):
        b._save_cookie(1, 'maybe-valid')
        self.message.reply_text.return_value = self.message
        with patch.object(b, 'verify_session_cookie', AsyncMock(side_effect=TimeoutError)):
            await b.status_command(self.update, self.context)
            await b._status_checks[1]
        self.assertEqual(b._get_cookie(1), 'maybe-valid')
        self.assertIn('could not verify', self.message.edit_text.call_args.args[0])

    async def test_status_does_not_delete_replaced_session(self):
        b._save_cookie(1, 'old')
        self.message.reply_text.return_value = self.message
        async def changed(cookie):
            b._save_cookie(1, 'new')
            return False
        with patch.object(b, 'verify_session_cookie', changed):
            await b.status_command(self.update, self.context)
            await b._status_checks[1]
        self.assertEqual(b._get_cookie(1), 'new')

    async def test_status_waits_for_search_before_starting_verification_timeout(self):
        b._save_cookie(1, 'valid')
        self.message.reply_text.return_value = self.message
        lock = b._user_locks.setdefault(1, asyncio.Lock())
        await lock.acquire()
        with patch.object(b, 'verify_session_cookie', AsyncMock(return_value=True)) as verify, \
             patch.object(b.asyncio, 'wait_for', wraps=asyncio.wait_for) as timeout:
            await b.status_command(self.update, self.context)
            task = b._status_checks[1]
            await asyncio.sleep(0)
            verify.assert_not_awaited()
            timeout.assert_not_called()
            lock.release()
            await task
            verify.assert_awaited_once()
            self.assertEqual(timeout.call_args.kwargs['timeout'], 35)

    async def test_cancel_stops_pending_status_check(self):
        b._save_cookie(1, 'valid')
        self.message.reply_text.return_value = self.message
        lock = b._user_locks.setdefault(1, asyncio.Lock())
        await lock.acquire()
        await b.status_command(self.update, self.context)
        task = b._status_checks[1]
        await asyncio.sleep(0)
        await b.cancel(self.update, self.context)
        lock.release()
        self.assertTrue(task.cancelled())
        self.assertNotIn(1, b._status_checks)

    async def test_gap_button_filters_results_without_browser(self):
        original = query()
        self.context.user_data['search_records'] = {'old': {
            'q': original, 'result': result(), 'checked': b.time.time()-30}}
        self.cb.data = 'gap60:old'
        with patch.object(b, '_run_check', new_callable=AsyncMock) as run, \
             patch.object(b, '_show_results', new_callable=AsyncMock) as show:
            await b.result_action(self.update, self.context)
            run.assert_not_awaited()
            self.assertIn('Find 1h continuously', show.call_args.args[3])
        record = self.context.user_data['search_records'][self.context.user_data['result_token']]
        self.assertEqual(record['q']['duration'], 60)
        self.assertEqual(original, query())

    async def test_missing_old_result_is_rejected(self):
        self.cb.data = 'recheck:gone'
        await b.result_action(self.update, self.context)
        self.assertTrue(self.cb.answer.call_args.kwargs['show_alert'])

    async def test_cancel_stops_browser_work(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()
        async def perform(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        with patch.object(b, '_perform_check', perform):
            await b._run_check(self.update, self.context, self.message, search_query=query())
            await started.wait()
            await b._stop_check(1)
        self.assertTrue(cleaned.is_set())
        self.assertNotIn(1, b._active_checks)

    async def test_fast_search_returns_partial_results(self):
        b._save_cookie(1, 'session=test')
        async def scrape(*args, on_room, **kwargs):
            await on_room('fully', result()['fully'][0])
            self.fail('Should stop after one matching room')
        with patch.object(b, 'check_rooms_with_cookie', scrape):
            await b._perform_check(self.update, self.context, self.message,
                                   search_query=query(duration=120, limit=1))
        record = next(iter(self.context.user_data['search_records'].values()))
        self.assertTrue(record['result']['incomplete'])
        self.assertEqual(b._result_cache, {})

    async def test_watch_persists_and_unwatch_removes(self):
        self.context.user_data['search_records'] = {'a': {'q': query(duration=120), 'result': result()}}
        self.cb.data = 'watch:a'
        await b.result_action(self.update, self.context)
        alert = b._load_json(b.ALERTS_FILE, {})['1']
        self.assertEqual(alert['q']['duration'], 120)
        self.assertLessEqual(alert['expires'], b.time.time()+86400)
        await b.unwatch_command(self.update, self.context)
        self.assertEqual(b._load_json(b.ALERTS_FILE, {}), {})

    async def test_next_two_hours_is_two_hours(self):
        now = b._now().replace(hour=21, minute=0)
        with patch.object(b, '_now', return_value=now):
            self.assertIsNone(b._next_two_hours())

    async def test_shared_lookup_survives_one_viewer_cancelling(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def scrape(*args, **kwargs):
            entered.set()
            await release.wait()
            return result()
        with patch.object(b, 'check_rooms_with_cookie', AsyncMock(side_effect=scrape)) as call:
            first = asyncio.create_task(b._shared_lookup(query(), 'one', lambda *a, **kw: None))
            await entered.wait()
            second = asyncio.create_task(b._shared_lookup(query(), 'two', lambda *a, **kw: None))
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            release.set()
            self.assertEqual(await second, b._clip_result(result(), query()))
            self.assertEqual(call.await_count, 1)
            self.assertEqual(b._inflight, {})

    async def test_last_viewer_cancels_shared_work(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def scrape(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        with patch.object(b, 'check_rooms_with_cookie', scrape):
            task = asyncio.create_task(b._shared_lookup(query(), 'one', lambda *a, **kw: None))
            await entered.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(cleaned.is_set())
            self.assertEqual(b._inflight, {})

    async def test_pagination_edits_one_message(self):
        self.context.user_data.update(result_token='abc', search_records={'abc': {}})
        await b._show_results(self.context, self.update.effective_chat, self.message,
                              '\n'.join(['x'*100]*80), 0, page=1)
        call = self.context.bot.edit_message_text.call_args
        self.assertIn('Page 2/3', call.args[0])
        self.assertEqual(self.context.bot.edit_message_text.await_count, 1)
        self.update.effective_chat.send_message.assert_not_called()

    async def test_back_button_returns_to_start_time(self):
        self.cb.data = 'back_start'
        self.cb.edit_message_text = AsyncMock()
        self.assertEqual(await b.back_callback(self.update, self.context), b.CHOOSE_START)
        self.cb.edit_message_text.assert_awaited_once()

    async def test_unwatch_cancels_active_lookup(self):
        entered = asyncio.Event()
        async def work():
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(work())
        b._alert_checks['1'] = task
        await entered.wait()
        await b.unwatch_command(self.update, self.context)
        self.assertTrue(task.cancelled())
        self.assertNotIn('1', b._alert_checks)

    async def test_alert_notifies_once_and_removes_watch(self):
        b._save_cookie(1, 'session=test')
        b._save_json(b.ALERTS_FILE, {'1': {'q': query(duration=120), 'chat': 2,
                                         'expires': b.time.time()+1000, 'next': 0}})
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(b.asyncio, 'sleep', AsyncMock(side_effect=[None, asyncio.CancelledError])), \
             patch.object(b, 'check_rooms_with_cookie', AsyncMock(return_value=result())):
            with self.assertRaises(asyncio.CancelledError):
                await b._watch_cycle(app)
        self.assertEqual(app.bot.send_message.await_count, 1)
        self.assertEqual(b._load_json(b.ALERTS_FILE, {}), {})

    async def test_expired_session_stops_alert(self):
        b._save_json(b.ALERTS_FILE, {'1': {'q': query(), 'chat': 2,
                                         'expires': b.time.time()+1000, 'next': 0}})
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(b.asyncio, 'sleep', AsyncMock(side_effect=[None, asyncio.CancelledError])):
            with self.assertRaises(asyncio.CancelledError):
                await b._watch_cycle(app)
        self.assertEqual(b._load_json(b.ALERTS_FILE, {}), {})
        self.assertIn('session expired', app.bot.send_message.call_args.args[1])

    async def test_scraper_failure_is_not_booked(self):
        page = SimpleNamespace(url='https://rbs.singaporetech.edu.sg/SRB001/SRB001Page',
            get_by_role=lambda *a, **kw: SimpleNamespace(click=AsyncMock()),
            wait_for_timeout=AsyncMock(), select_option=AsyncMock(),
            evaluate=AsyncMock(side_effect=[None, [{'rsrcName': 'E2-A'}], 'NOT_JSON']))
        response = SimpleNamespace(status=200, body=AsyncMock(return_value=b''), finished=AsyncMock())
        future = asyncio.get_running_loop().create_future()
        future.set_result(response)
        manager = SimpleNamespace(value=future)
        from unittest.mock import MagicMock
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=manager)
        cm.__aexit__ = AsyncMock(return_value=False)
        page.expect_response = lambda *a, **kw: cm
        with patch.object(scraper, '_wait_for_cards', AsyncMock()):
            r = await scraper._do_check_async(page, query()['date'], '12:00', '18:00', lambda *a, **kw: None)
        self.assertEqual(r['none'], [])
        self.assertEqual(r['errors'], ['E2-A'])
        response.body.assert_awaited_once()
        response.finished.assert_not_awaited()


class StartupTests(unittest.TestCase):
    def test_application_builds_without_network(self):
        with patch.object(b, 'BOT_TOKEN', '123456:abcdefghijklmnopqrstuvwxyz'), \
             patch.object(b.Application, 'run_polling') as run:
            b.main()
            run.assert_called_once()


if __name__ == '__main__':
    unittest.main()

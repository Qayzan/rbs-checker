"""Keep a bounded set of isolated, authenticated pages warm between checks."""
import asyncio
import hashlib
from contextlib import asynccontextmanager


class SessionPool:
    def __init__(self, keeper, idle_seconds=120, capacity=8):
        self.keeper, self.idle_seconds, self.capacity = keeper, idle_seconds, capacity
        self.records = {}

    async def _close(self, key, record):
        if self.records.get(key) is record:
            self.records.pop(key)
        timer = record.get('timer')
        if timer and timer is not asyncio.current_task():
            timer.cancel()
        manager = record.pop('manager', None)
        if manager:
            await manager.__aexit__(None, None, None)

    async def _expire(self, key, record):
        await asyncio.sleep(self.idle_seconds)
        async with record['lock']:
            await self._close(key, record)

    @asynccontextmanager
    async def page(self, cookie, cookies):
        key = hashlib.sha256(cookie.encode()).hexdigest()
        while True:
            record = self.records.setdefault(key, {'lock': asyncio.Lock()})
            await record['lock'].acquire()
            if self.records.get(key) is record:
                break
            record['lock'].release()
        try:
            timer = record.pop('timer', None)
            if timer:
                timer.cancel()
            reused = 'page' in record and not record['page'].is_closed()
            if not reused:
                if 'manager' in record:
                    await self._close(key, record)
                    self.records[key] = record
                manager = self.keeper.context()
                context = await manager.__aenter__()
                record['manager'] = manager
                await context.add_cookies(cookies)
                record['page'] = await context.new_page()
            yield record['page'], reused
        except BaseException:
            await self._close(key, record)
            raise
        finally:
            if self.records.get(key) is record:
                record['timer'] = asyncio.create_task(self._expire(key, record))
            record['lock'].release()
            # Evict idle entries only; in-use sessions remain isolated and safe.
            for old_key, old in list(self.records.items()):
                if len(self.records) <= self.capacity:
                    break
                if not old['lock'].locked():
                    async with old['lock']:
                        await self._close(old_key, old)

    async def close(self, cookie=None):
        key = hashlib.sha256(cookie.encode()).hexdigest() if cookie else None
        for old_key, record in list(self.records.items()):
            if key is None or key == old_key:
                async with record['lock']:
                    await self._close(old_key, record)

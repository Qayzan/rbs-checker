"""Bounded, short-lived availability data; never stores credentials."""
import os
import time


def minutes(value):
    h, m = map(int, value.split(':'))
    return h * 60 + m


class SlotCache:
    def __init__(self, ttl=120, capacity=20000, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.data = {}

    def get(self, room, date, start, end):
        now = self.clock()
        slots, ages = [], []
        for minute in range(minutes(start), minutes(end), 30):
            hit = self.data.get((room, date, minute))
            if hit is None or now - hit[0] >= self.ttl:
                return None
            stamp, slot = hit
            slots.append(dict(slot))
            ages.append(now-stamp)
        return (slots, max(ages, default=0)) if slots else None

    def put(self, room, date, slots):
        now = self.clock()
        entries = {(room, date, minutes(slot['SLT_Desc'])): (now, dict(slot)) for slot in slots}
        self.data = {k: v for k, v in self.data.items() if now-v[0] < self.ttl}
        self.data.update(entries)
        while len(self.data) > self.capacity:
            self.data.pop(next(iter(self.data)))


# How long a room's availability is trusted before asking SIT again.
slot_cache = SlotCache(ttl=max(30, int(os.environ.get('RBS_SLOT_CACHE_SECONDS', '180'))))

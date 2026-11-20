import threading
import time
from collections import deque


class SlidingWindowLimiter:
    """Allow `limit` calls per key in any `window` seconds.

    State is in memory, so it is per server process. That is enough to stop one
    signed-in user running up provider calls from the browser. With several worker
    processes the real ceiling is limit x workers; move this to the database or
    Redis if that matters.
    """

    def __init__(self, limit, window, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits = {}
        self._lock = threading.Lock()

    def check(self, key):
        """Record a call. Returns (allowed, seconds_until_a_slot_frees)."""
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(self.window - (now - hits[0])) + 1)
            hits.append(now)
            # Forget keys that have gone quiet so the dict cannot grow without bound.
            if len(self._hits) > 1000:
                for stale in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                    del self._hits[stale]
            return True, 0

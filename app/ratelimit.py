import threading
import time
from collections import deque


class FailureLimiter:
    """Block a key after `limit` recorded failures within `window` seconds.

    Same in-memory pattern as the AI limiter, but separate code: successes are never
    recorded, only failures. State is per server process.
    """

    def __init__(self, limit, window, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._failures = {}
        self._lock = threading.Lock()

    def _trim(self, key, now):
        hits = self._failures.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        return hits

    def blocked(self, key):
        """(is_blocked, seconds_until_a_slot_frees)."""
        now = self.clock()
        with self._lock:
            hits = self._trim(key, now)
            if len(hits) >= self.limit:
                return True, max(1, int(self.window - (now - hits[0])) + 1)
            return False, 0

    def record_failure(self, key):
        now = self.clock()
        with self._lock:
            self._trim(key, now).append(now)
            if len(self._failures) > 1000:
                for stale in [k for k, v in self._failures.items() if not v or now - v[-1] >= self.window]:
                    del self._failures[stale]

"""Local limits on real provider requests, so the free quota cannot be used up by accident.

Every attempt counts, including retries and failures, because the provider counts them too.
Counts are kept in memory, so they start again when the backend restarts. OpenRouter enforces
its own limits as well, so this is a safety margin, not the only limit.
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone

WINDOW_SECONDS = 60


class RequestBudget:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._recent: deque[float] = deque()
        self._day = None
        self._today = 0

    def try_acquire(self, per_minute: int, per_day: int) -> bool:
        """Record one request and return True, or return False if a limit has been reached."""
        with self._lock:
            now = self._clock()
            while self._recent and now - self._recent[0] >= WINDOW_SECONDS:
                self._recent.popleft()
            # OpenRouter's daily free limit resets at midnight UTC.
            day = datetime.fromtimestamp(now, timezone.utc).date()
            if day != self._day:
                self._day, self._today = day, 0
            if len(self._recent) >= per_minute or self._today >= per_day:
                return False
            self._recent.append(now)
            self._today += 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._recent.clear()
            self._day, self._today = None, 0


BUDGET = RequestBudget()

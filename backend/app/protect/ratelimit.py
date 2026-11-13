"""A small in-memory throttle for failed logins. One server process, so no shared store is needed."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

MAX_TRACKED_KEYS = 10_000


class LoginThrottle:
    def __init__(
        self, max_failures: int = 10, window_seconds: int = 300, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = self._clock() - self.window
        kept = [t for t in self._failures.get(key, []) if t > cutoff]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed; 0 when not blocked."""
        with self._lock:
            recent = self._recent(key)
            if len(recent) < self.max_failures:
                return 0
            return max(1, int(recent[0] + self.window - self._clock()) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            if len(self._failures) >= MAX_TRACKED_KEYS and key not in self._failures:
                self._failures.clear()  # bounded memory beats perfect bookkeeping
            self._recent(key)
            self._failures.setdefault(key, []).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

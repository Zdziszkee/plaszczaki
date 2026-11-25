"""Per-host request pacing."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit


class HostRateLimiter:
    """Simple per-host spacing with support for server-requested backoff."""

    def __init__(self, rps: float = 1.0) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.interval = 1.0 / rps
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def host_of(url: str) -> str:
        return urlsplit(url).netloc.lower()

    def wait(self, url: str) -> None:
        host = self.host_of(url)
        with self._lock:
            now = time.monotonic()
            allowed_at = max(now, self._next_allowed.get(host, now))
            self._next_allowed[host] = allowed_at + self.interval
        delay = allowed_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def backoff(self, url: str, seconds: float) -> None:
        host = self.host_of(url)
        with self._lock:
            now = time.monotonic()
            self._next_allowed[host] = max(
                self._next_allowed.get(host, now),
                now + max(0.0, seconds),
            )

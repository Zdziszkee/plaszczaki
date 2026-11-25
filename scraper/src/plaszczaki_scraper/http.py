"""Polite HTTP fetching with robots.txt, pacing and retries."""

from __future__ import annotations

import logging
import os
import random
import time
from email.utils import parsedate_to_datetime
from typing import Any

from scrapling.fetchers import Fetcher

from plaszczaki_scraper.ratelimit import HostRateLimiter
from plaszczaki_scraper.robots import RobotsCache

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "plaszczaki-scraper/0.1 (+https://github.com/zdziszkee/plaszczaki)"


class FetchError(RuntimeError):
    """Raised when a page cannot be fetched after retries."""


class RobotsDenied(FetchError):
    """Raised when robots.txt does not permit fetching a URL."""


class HttpBlocked(FetchError):
    """Raised when the server actively blocks the request."""


def user_agent() -> str:
    return os.environ.get("SCRAPER_USER_AGENT", DEFAULT_USER_AGENT)


_CHALLENGE_BODY_MARKERS: tuple[tuple[bytes, str], ...] = (
    (b"_Incapsula_Resource", "Imperva"),
    (b"incident_id", "Imperva"),
    (b"geo.captcha-delivery.com", "DataDome"),
    (b"captcha-delivery.com", "DataDome"),
    (b"Request Rejected", "F5 ASM"),
    (b"challenges.cloudflare.com", "Cloudflare"),
)


def challenge_vendor(response: Any) -> str | None:
    """Return the WAF vendor when a response is a challenge shell, not content.

    Imperva, AWS WAF and DataDome serve their challenge with HTTP 200 and an
    empty/iframe body, so status-code checks alone are not enough.
    """
    headers = {
        str(name).lower(): str(value).lower()
        for name, value in dict(getattr(response, "headers", None) or {}).items()
    }

    if headers.get("cf-mitigated") == "challenge":
        return "Cloudflare"
    if headers.get("x-amzn-waf-action") in {"challenge", "captcha"}:
        return "AWS WAF"
    if "x-datadome-cid" in headers:
        return "DataDome"

    body = bytes(getattr(response, "body", b"") or b"")[:8192].lower()
    for marker, vendor in _CHALLENGE_BODY_MARKERS:
        if marker.lower() in body:
            return vendor
    return None


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    return max(0.0, parsed.timestamp() - time.time())


class HttpClient:
    """A thin Scrapling wrapper enforcing the scraper's politeness rules."""

    def __init__(
        self,
        *,
        rps: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 3,
        robots: RobotsCache | None = None,
        ua: str | None = None,
    ) -> None:
        self.limiter = HostRateLimiter(rps)
        self.timeout = timeout
        self.max_retries = max_retries
        self.ua = ua or user_agent()
        self.robots = robots or RobotsCache(user_agent=self.ua)
        self.headers = {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7",
        }

    def get(self, url: str, *, check_robots: bool = True) -> Any:
        """Fetch a URL, returning a Scrapling response."""
        if check_robots and not self.robots.can_fetch(url):
            raise RobotsDenied(f"robots.txt disallows {url}")

        crawl_delay = self.robots.crawl_delay(url)
        if crawl_delay and crawl_delay > 0:
            # Respect the site's own delay in addition to our configured pace.
            time.sleep(crawl_delay)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait(url)
            try:
                response = Fetcher.get(
                    url,
                    headers=self.headers,
                    timeout=self.timeout,
                    retries=0,
                    follow_redirects=True,
                    # Do not fabricate a Google referer; some sites redirect or
                    # serve a different page when stealthy headers are enabled.
                    stealthy_headers=False,
                )
            except Exception as exc:  # Scrapling wraps several transport errors.
                last_error = exc
                logger.debug("fetch error for %s (attempt %s): %s", url, attempt + 1, exc)
                if attempt >= self.max_retries:
                    break
                self._backoff(url, attempt)
                continue

            status = int(getattr(response, "status", 0) or 0)

            vendor = challenge_vendor(response)
            if vendor is not None:
                raise HttpBlocked(f"{vendor} challenge for {url}")

            if 200 <= status < 300:
                return response

            if status in {401, 403}:
                raise HttpBlocked(f"HTTP {status} for {url}")

            if status == 429 or status >= 500:
                retry_after = _retry_after_seconds(response.headers.get("retry-after"))
                wait = retry_after if retry_after is not None else self._retry_delay(attempt)
                logger.warning("HTTP %s for %s; backing off %.1fs", status, url, wait)
                self.limiter.backoff(url, wait)
                last_error = FetchError(f"HTTP {status} for {url}")
                if attempt >= self.max_retries:
                    break
                continue

            # 404/410 and other client errors are returned for the caller to handle.
            return response

        raise FetchError(f"failed to fetch {url}: {last_error}") from last_error

    def _retry_delay(self, attempt: int) -> float:
        base = min(30.0, 1.5 * (2**attempt))
        return base + random.uniform(0, 0.5)

    def _backoff(self, url: str, attempt: int) -> None:
        self.limiter.backoff(url, self._retry_delay(attempt))

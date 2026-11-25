"""Polite HTTP fetching with robots.txt, pacing and retries."""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from scrapling.fetchers import Fetcher

from plaszczaki_scraper.ratelimit import HostRateLimiter
from plaszczaki_scraper.robots import RobotsCache, default_cache_dir

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "plaszczaki-scraper/0.1 (+https://github.com/zdziszkee/plaszczaki)"


class FetchError(RuntimeError):
    """Raised when a page cannot be fetched after retries."""


class RobotsDenied(FetchError):
    """Raised when robots.txt does not permit fetching a URL."""


class HttpBlocked(FetchError):
    """Raised when the server actively blocks the request."""


class CrawlDeferred(FetchError):
    """Raised when robots.txt asks us to wait before the next request.

    The slow lane records the last request time on disk, so a one-shot run can
    return immediately and let the next scheduled run do the work instead of
    sleeping for twenty minutes inside the watch cycle.
    """

    def __init__(self, url: str, remaining: float) -> None:
        self.url = url
        self.remaining = remaining
        super().__init__(f"crawl delay not elapsed for {url}: {remaining:.0f}s left")


class HostDelayStore:
    """Persistent per-host last-request timestamps for robots crawl delays."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_dir() / "host-delays.json"

    def _read(self) -> dict[str, float]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError, json.JSONDecodeError, OSError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(host): float(value)
            for host, value in payload.items()
            if isinstance(value, int | float)
        }

    def remaining(self, host: str, delay: float) -> float:
        if delay <= 0:
            return 0.0
        last = self._read().get(host)
        if last is None:
            return 0.0
        return max(0.0, delay - (time.time() - last))

    def mark(self, host: str) -> None:
        payload = self._read()
        payload[host] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


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
        fetch_mode: Literal["http", "stealth"] = "http",
        browser_path: str | None = None,
        crawl_delay_mode: Literal["sleep", "defer"] = "sleep",
        delay_store: HostDelayStore | None = None,
    ) -> None:
        self.limiter = HostRateLimiter(rps)
        self.timeout = timeout
        self.max_retries = max_retries
        self.ua = ua or user_agent()
        self.robots = robots or RobotsCache(user_agent=self.ua)
        self.fetch_mode = fetch_mode
        self.crawl_delay_mode = crawl_delay_mode
        self.delay_store = delay_store or HostDelayStore()
        self.browser_path = (
            browser_path or os.environ.get("SCRAPER_CHROMIUM_PATH") or shutil.which("chromium")
        )
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
            host = urlsplit(url).netloc.lower()
            remaining = self.delay_store.remaining(host, crawl_delay)
            if remaining > 0:
                if self.crawl_delay_mode == "defer":
                    raise CrawlDeferred(url, remaining)
                # Respect the site's own delay in addition to our configured pace.
                time.sleep(remaining)
            self.delay_store.mark(host)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait(url)
            try:
                response = self._fetch(url)
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

    def _fetch(self, url: str) -> Any:
        """Fetch with the plain HTTP client or Scrapling's browser engine."""
        if self.fetch_mode == "stealth":
            return self._stealth_fetch(url)
        return Fetcher.get(
            url,
            headers=self.headers,
            timeout=self.timeout,
            retries=0,
            follow_redirects=True,
            # Do not fabricate a Google referer; some sites redirect or
            # serve a different page when stealthy headers are enabled.
            stealthy_headers=False,
        )

    def _stealth_fetch(self, url: str) -> Any:
        """Fetch through a real browser (Playwright) for WAF-protected pages."""
        from scrapling.fetchers import StealthyFetcher

        kwargs: dict[str, Any] = {
            "headless": True,
            "timeout": int(self.timeout * 1000),
            "network_idle": False,
            "solve_cloudflare": False,
            "extra_headers": dict(self.headers),
        }
        if self.browser_path:
            kwargs["executable_path"] = self.browser_path
        try:
            return StealthyFetcher.fetch(url, **kwargs)
        except Exception as exc:  # missing browser, launch failure, transport
            raise FetchError(f"stealth fetch failed for {url}: {exc}") from exc

    def _retry_delay(self, attempt: int) -> float:
        base = min(30.0, 1.5 * (2**attempt))
        return base + random.uniform(0, 0.5)

    def _backoff(self, url: str, attempt: int) -> None:
        self.limiter.backoff(url, self._retry_delay(attempt))

"""robots.txt fetching and full-URL permission checks."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from protego import Protego

from plaszczaki_scraper.config import repo_root

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 24 * 60 * 60


def default_cache_dir() -> Path:
    override = os.environ.get("PLASZCZAKI_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "data" / "cache"


def _host_root(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(slots=True)
class RobotsEntry:
    root: str
    fetched_at: float
    status: int | None
    allow_all: bool = False
    deny_all: bool = False
    parser: Protego | None = None
    text: str = ""
    error: str | None = None

    def can_fetch(self, url: str, user_agent: str) -> bool:
        if self.deny_all:
            return False
        if self.allow_all or self.parser is None:
            return True
        return self.parser.can_fetch(url, user_agent)

    def crawl_delay(self, user_agent: str) -> float | None:
        if self.parser is None:
            return None
        return self.parser.crawl_delay(user_agent)


class RobotsCache:
    """Fetch and cache robots.txt, honouring query-string rules."""

    def __init__(
        self,
        *,
        user_agent: str,
        cache_dir: Path | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.user_agent = user_agent
        self.cache_dir = cache_dir or default_cache_dir() / "robots"
        self.timeout = timeout
        self._memory: dict[str, RobotsEntry] = {}

    def can_fetch(self, url: str) -> bool:
        return self.entry(url).can_fetch(url, self.user_agent)

    def crawl_delay(self, url: str) -> float | None:
        return self.entry(url).crawl_delay(self.user_agent)

    def entry(self, url: str) -> RobotsEntry:
        root = _host_root(url)
        cached = self._memory.get(root)
        if cached is not None and time.time() - cached.fetched_at < CACHE_TTL_SECONDS:
            return cached

        entry = self._read_cache(root)
        if entry is None:
            entry = self._fetch(root)
            self._write_cache(entry)
        self._memory[root] = entry
        return entry

    def _cache_path(self, root: str) -> Path:
        digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, root: str) -> RobotsEntry | None:
        path = self._cache_path(root)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError, json.JSONDecodeError:
            return None

        if not isinstance(payload, dict) or payload.get("root") != root:
            return None
        fetched_at = payload.get("fetched_at")
        if not isinstance(fetched_at, int | float):
            return None
        if time.time() - fetched_at >= CACHE_TTL_SECONDS:
            return None

        text = payload.get("text", "")
        status = payload.get("status")
        entry = RobotsEntry(
            root=root,
            fetched_at=float(fetched_at),
            status=status if isinstance(status, int) else None,
            allow_all=bool(payload.get("allow_all", False)),
            deny_all=bool(payload.get("deny_all", False)),
            error=payload.get("error"),
        )
        if isinstance(text, str) and text:
            entry.parser = Protego.parse(text)
            entry.text = text
        return entry

    def _write_cache(self, entry: RobotsEntry) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "root": entry.root,
            "fetched_at": entry.fetched_at,
            "status": entry.status,
            "allow_all": entry.allow_all,
            "deny_all": entry.deny_all,
            "error": entry.error,
            "text": entry.text,
        }
        self._cache_path(entry.root).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def _fetch(self, root: str) -> RobotsEntry:
        url = f"{root}/robots.txt"
        now = time.time()
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            logger.warning("robots.txt unreachable for %s: %s (allowing)", root, exc)
            return RobotsEntry(
                root=root, fetched_at=now, status=None, allow_all=True, error=str(exc)
            )

        if response.status_code in {401, 403}:
            logger.warning("robots.txt denied access for %s (disallowing)", root)
            return RobotsEntry(
                root=root, fetched_at=now, status=response.status_code, deny_all=True
            )

        if response.status_code in {404, 410}:
            return RobotsEntry(
                root=root, fetched_at=now, status=response.status_code, allow_all=True
            )

        if response.status_code >= 500:
            logger.warning(
                "robots.txt server error for %s: %s (allowing)",
                root,
                response.status_code,
            )
            return RobotsEntry(
                root=root,
                fetched_at=now,
                status=response.status_code,
                allow_all=True,
            )

        text = response.text
        try:
            parser = Protego.parse(text)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("robots.txt parse failed for %s: %s (allowing)", root, exc)
            return RobotsEntry(
                root=root,
                fetched_at=now,
                status=response.status_code,
                allow_all=True,
                error=str(exc),
            )

        return RobotsEntry(
            root=root,
            fetched_at=now,
            status=response.status_code,
            parser=parser,
            text=text,
        )

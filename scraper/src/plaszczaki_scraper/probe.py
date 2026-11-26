"""Fetch one page per source and report what the site actually serves.

This is the feasibility gate before writing an adapter: robots.txt permission is
not the same as a usable HTML/JSON response.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from plaszczaki_scraper.config import SourceConfig
from plaszczaki_scraper.http import FetchError, HttpClient, RobotsDenied, user_agent
from plaszczaki_scraper.robots import RobotsCache, default_cache_dir

logger = logging.getLogger(__name__)

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>', re.IGNORECASE)
LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
CHALLENGE_MARKERS = (
    "cf-chl",
    "cf-challenge",
    "just a moment",
    "checking your browser",
    "datadome",
    "captcha",
)


@dataclass(slots=True)
class ProbeResult:
    source_id: str
    url: str
    allowed: bool
    robots_status: int | None
    crawl_delay: float | None
    http_status: int | None = None
    content_type: str | None = None
    bytes: int = 0
    title: str | None = None
    has_next_data: bool = False
    has_ld_json: bool = False
    ld_types: list[str] | None = None
    link_count: int = 0
    challenge: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _response_html(response: Any) -> str:
    html = getattr(response, "html_content", None)
    if isinstance(html, str):
        return html
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        encoding = getattr(response, "encoding", None) or "utf-8"
        return body.decode(encoding, errors="replace")
    return str(body)


def _ld_types(html: str) -> list[str]:
    types: list[str] = []
    for match in LD_JSON_RE.finditer(html):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        records = payload if isinstance(payload, list) else [payload]
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("@type")
            if isinstance(value, str):
                types.append(value)
            elif isinstance(value, list):
                types.extend(str(item) for item in value)
            graph = record.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict) and isinstance(item.get("@type"), str):
                        types.append(item["@type"])
    return sorted(set(types))


def probe_source(source: SourceConfig, *, rps: float = 1.0) -> ProbeResult:
    """Probe the first inventory URL of one source."""
    url = source.urls[0]
    ua = user_agent()
    robots = RobotsCache(user_agent=ua)
    entry = robots.entry(url)
    allowed = entry.can_fetch(url, ua)
    result = ProbeResult(
        source_id=source.id,
        url=url,
        allowed=allowed,
        robots_status=entry.status,
        crawl_delay=entry.crawl_delay(ua),
    )

    if not allowed:
        result.error = "robots.txt disallows the source URL"
        return result

    client = HttpClient(
        rps=rps,
        robots=robots,
        ua=ua,
        fetch_mode=source.fetch_mode,
        browser_headless=source.browser_headless,
        browser_solve_cloudflare=source.browser_solve_cloudflare,
        browser_wait_ms=source.browser_wait_ms,
        crawl_delay_mode=source.crawl_delay_mode,
    )
    try:
        try:
            response = client.get(url)
        except RobotsDenied as exc:
            result.error = f"robots: {exc}"
            return result
        except FetchError as exc:
            result.error = str(exc)
            return result

        headers = getattr(response, "headers", {}) or {}
        result.http_status = int(getattr(response, "status", 0) or 0)
        result.content_type = str(headers.get("content-type", "")) or None
        html = _response_html(response)
        result.bytes = len(html.encode("utf-8", errors="replace"))
        title_match = TITLE_RE.search(html)
        if title_match:
            result.title = " ".join(title_match.group(1).split())[:200] or None
        result.has_next_data = bool(NEXT_DATA_RE.search(html))
        result.has_ld_json = bool(LD_JSON_RE.search(html))
        result.ld_types = _ld_types(html)
        result.link_count = len(re.findall(r"<a\s", html, re.IGNORECASE))
        lowered = html.lower()
        title_lower = (result.title or "").lower()
        result.challenge = any(marker in title_lower for marker in CHALLENGE_MARKERS) or (
            result.bytes < 20_000 and any(marker in lowered for marker in CHALLENGE_MARKERS)
        )
        return result
    finally:
        client.close()


def probe_sources(
    sources: list[SourceConfig],
    *,
    rps: float = 1.0,
    output_path: Path | None = None,
) -> dict[str, Any]:
    results = []
    for index, source in enumerate(sources, start=1):
        logger.info("probe %s (%s/%s)", source.id, index, len(sources))
        result = probe_source(source, rps=rps)
        results.append(result.as_dict())
        if rps > 0:
            time.sleep(1.0 / rps)

    report = {
        "user_agent": user_agent(),
        "generated_at": int(time.time() * 1000),
        "total": len(results),
        "ok": sum(
            1
            for item in results
            if item["http_status"] is not None
            and 200 <= item["http_status"] < 300
            and not item["challenge"]
            and item["error"] is None
        ),
        "entries": results,
    }

    target = output_path or default_cache_dir().parent / "out" / "source-probes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    report["output"] = str(target)
    return report


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")

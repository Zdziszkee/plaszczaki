"""Source feasibility audit (robots.txt only, no page scraping)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from plaszczaki_scraper.config import load_source_urls
from plaszczaki_scraper.http import user_agent
from plaszczaki_scraper.robots import RobotsCache, default_cache_dir


def audit_sources(
    *,
    limit: int | None = None,
    rps: float = 1.0,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Check robots.txt permission for every configured source URL."""
    urls = load_source_urls()
    if limit is not None:
        urls = urls[:limit]

    ua = user_agent()
    robots = RobotsCache(user_agent=ua)
    entries: list[dict[str, Any]] = []

    for index, url in enumerate(urls, start=1):
        entry = robots.entry(url)
        entries.append(
            {
                "url": url,
                "host": entry.root,
                "allowed": entry.can_fetch(url, ua),
                "robots_status": entry.status,
                "crawl_delay": entry.crawl_delay(ua),
                "error": entry.error,
            },
        )
        if rps > 0:
            time.sleep(1.0 / rps)
        if index % 25 == 0:
            print(f"audited {index}/{len(urls)}")

    allowed = sum(1 for entry in entries if entry["allowed"])
    report = {
        "user_agent": ua,
        "generated_at": int(time.time() * 1000),
        "total": len(entries),
        "allowed": allowed,
        "disallowed": len(entries) - allowed,
        "entries": entries,
    }

    target = output_path or default_cache_dir().parent / "out" / "source-audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    report["output"] = str(target)
    return report

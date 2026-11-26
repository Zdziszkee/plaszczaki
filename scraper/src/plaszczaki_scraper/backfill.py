"""Maintenance commands for normalizing already stored offer data."""

from __future__ import annotations

import logging
from typing import Any

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.registry import build_adapter
from plaszczaki_scraper.attributes import normalize_attributes
from plaszczaki_scraper.config import (
    SourcesConfig,
    load_regions_config,
    load_sources_config,
)
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.http import CrawlDeferred, FetchError, HttpClient, RobotsDenied, user_agent
from plaszczaki_scraper.regions import RegionRegistry

logger = logging.getLogger(__name__)


def backfill_attributes(
    db: Database,
    *,
    source_id: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Normalize stored ``extra`` payloads without making network requests."""
    processed = 0
    updated = 0
    for listing in db.iter_listings(source_id=source_id):
        if limit is not None and processed >= limit:
            break
        processed += 1
        normalize_attributes(listing)
        if db.update_listing_backfill(listing, dry_run=dry_run):
            updated += 1
    return {
        "source_id": source_id,
        "processed": processed,
        "updated": updated,
        "unchanged": processed - updated,
        "dry_run": dry_run,
    }


def _response_html(response: Any) -> str:
    html = getattr(response, "html_content", None)
    if isinstance(html, str):
        return html
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        encoding = getattr(response, "encoding", None) or "utf-8"
        return body.decode(encoding, errors="replace")
    return str(body)


def _selected_sources(catalog: SourcesConfig, source_id: str | None) -> list[Any]:
    if source_id is not None:
        source = catalog.sources.get(source_id)
        if source is None:
            raise ValueError(f"unknown source: {source_id}")
        return [source]
    return list(catalog.implemented())


def rehydrate_missing_districts(
    db: Database,
    *,
    source_id: str | None = None,
    limit: int | None = None,
    rps: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Refetch stored detail URLs to recover fields that were never preserved.

    This is intentionally opt-in. It is primarily needed for Otodom, whose old
    ``extra`` payload did not contain reverse-geocoded district levels.
    """
    catalog = load_sources_config()
    regions = RegionRegistry(load_regions_config())
    attempted = 0
    updated = 0
    district_filled = 0
    failed = 0
    per_source: dict[str, dict[str, int]] = {}

    for source in _selected_sources(catalog, source_id):
        if source.status != "implemented" or not source.hydrate_details:
            continue
        if limit is not None and attempted >= limit:
            break
        adapter = build_adapter(source, regions=regions)
        client = HttpClient(
            rps=rps if rps is not None else source.rps,
            ua=user_agent(),
            fetch_mode=source.fetch_mode,
            browser_headless=source.browser_headless,
            browser_solve_cloudflare=source.browser_solve_cloudflare,
            browser_wait_ms=source.browser_wait_ms,
            crawl_delay_mode=source.crawl_delay_mode,
        )
        stats = per_source.setdefault(
            source.id,
            {"attempted": 0, "updated": 0, "district_filled": 0, "failed": 0},
        )
        try:
            for listing in db.iter_listings(source_id=source.id, missing_district=True):
                if limit is not None and attempted >= limit:
                    break
                attempted += 1
                stats["attempted"] += 1
                resolver = getattr(adapter, "detail_url", None)
                url = resolver(listing) if callable(resolver) else listing.url
                try:
                    response = client.get(url)
                    status = int(getattr(response, "status", 0) or 0)
                    if status >= 400:
                        raise ParseError(f"detail returned HTTP {status}")
                    detail = adapter.parse_detail(_response_html(response), listing)
                except (FetchError, RobotsDenied, CrawlDeferred, ParseError, ValueError) as exc:
                    failed += 1
                    stats["failed"] += 1
                    logger.warning("district rehydrate failed %s: %s", url, exc)
                    continue
                if detail.district is not None and listing.district is None:
                    district_filled += 1
                    stats["district_filled"] += 1
                if db.update_listing_backfill(detail, include_extra=True, dry_run=dry_run):
                    updated += 1
                    stats["updated"] += 1
        finally:
            client.close()

    return {
        "source_id": source_id,
        "attempted": attempted,
        "updated": updated,
        "district_filled": district_filled,
        "failed": failed,
        "dry_run": dry_run,
        "sources": per_source,
    }

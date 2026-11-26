"""Run orchestration: search page -> detail batch -> database."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from plaszczaki_scraper.adapters.base import Adapter, ParseError, Query
from plaszczaki_scraper.config import SourceConfig
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.http import CrawlDeferred, FetchError, HttpClient, RobotsDenied
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionMatch, RegionRegistry

logger = logging.getLogger(__name__)

RunMode = Literal["discover", "run", "backfill", "incremental"]


@dataclass(frozen=True, slots=True)
class RunOptions:
    source_id: str
    mode: RunMode = "run"
    regions: tuple[str, ...] = ("malopolskie", "slaskie")
    offer_types: tuple[str, ...] = ()
    limit: int | None = None
    max_pages: int | None = None
    dry_run: bool = False
    newest_only: bool = False
    incremental_pages: int = 2


@dataclass(slots=True)
class RunStats:
    discovered: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    details_ok: int = 0
    details_failed: int = 0
    details_skipped: int = 0
    deferred: int = 0
    known: int = 0
    pages_scanned: int = 0
    skipped_region: int = 0
    skipped_robots: int = 0
    http_errors: int = 0
    parse_errors: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def failures(self) -> int:
        return self.details_failed + self.http_errors + self.parse_errors


class Runner:
    """Coordinates one adapter run against the database."""

    def __init__(
        self,
        *,
        options: RunOptions,
        adapter: Adapter,
        db: Database,
        client: HttpClient,
        regions: RegionRegistry,
        source_config: SourceConfig | None = None,
        on_saved: Callable[[Sequence[Listing]], None] | None = None,
    ) -> None:
        self.options = options
        self.adapter = adapter
        self.db = db
        self.client = client
        self.regions = regions
        self.source_config = source_config
        self.on_saved = on_saved
        self.stats = RunStats()
        self.started_at = int(time.time() * 1000)
        self._discovery_count = 0

    def run(self) -> dict[str, Any]:
        run_id = (
            None
            if self.options.dry_run
            else self.db.start_run(source_id=self.options.source_id, mode=self.options.mode)
        )
        status = "ok"
        error: str | None = None

        try:
            self._discover()
            if self.stats.failures:
                status = "partial"
        except Exception as exc:  # pragma: no cover - defensive top-level guard
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("run failed")
        finally:
            stats = dict(self.stats.as_dict())
            stats["source_id"] = self.options.source_id
            stats["mode"] = self.options.mode
            stats["listings_total"] = self.db.count_listings(self.options.source_id)
            stats["duration_seconds"] = round(time.time() - (self.started_at / 1000), 1)
            if run_id is not None:
                self.db.finish_run(run_id=run_id, status=status, stats=stats, error=error)

        result = dict(self.stats.as_dict())
        result["status"] = status
        result["error"] = error
        result["listings_total"] = self.db.count_listings(self.options.source_id)
        return result

    def _discovery_limit_reached(self) -> bool:
        return self.options.limit is not None and self._discovery_count >= self.options.limit

    def _discover(self) -> None:
        queries = self.adapter.queries(self.options.regions)
        if self.options.offer_types:
            wanted = set(self.options.offer_types)
            queries = [query for query in queries if query.offer_type in wanted]
        if self.options.offer_types and not queries:
            logger.warning(
                "source %s emits no queries for offer types %s",
                self.options.source_id,
                ",".join(self.options.offer_types),
            )
        for query in queries:
            if self._discovery_limit_reached():
                break
            if self.options.newest_only:
                query = self._newest_query(query)
            self._discover_query(query)
            if self.stats.deferred:
                # A crawl-delay deferral means the whole source is done for now;
                # the next scheduled run resumes at the rotated query.
                break

    def _newest_query(self, query: Query) -> Query:
        """Apply the adapter's newest-first URL, or the generic config parameter."""
        transform = getattr(self.adapter, "newest", None)
        if callable(transform):
            transformed = transform(query)
            if transformed is not None:
                return transformed

        config = self.source_config
        if config is not None and config.newest_param and config.newest_value:
            return query.with_param(config.newest_param, config.newest_value)

        logger.warning(
            "source %s has no newest sort available; scanning up to %s page(s) as-is",
            self.options.source_id,
            self.options.incremental_pages,
        )
        return query

    def _discover_query(self, query: Query) -> None:
        page = 1
        total_pages = 1
        max_pages = self.options.max_pages
        if self.options.newest_only:
            configured = self.options.incremental_pages
            max_pages = min(max_pages or configured, configured)
        logger.info("discover %s page %s", query.key, page)

        while page <= total_pages:
            if max_pages is not None and page > max_pages:
                break
            if self._discovery_limit_reached():
                break

            page_query = self.adapter.with_page(query, page)
            try:
                if page_query.method == "POST":
                    response = self.client.post(page_query.url, page_query.body or "{}")
                else:
                    response = self.client.get(page_query.url)
            except RobotsDenied:
                self.stats.skipped_robots += 1
                logger.warning("robots.txt disallows %s", page_query.url)
                return
            except CrawlDeferred as exc:
                self.stats.deferred += 1
                logger.info(
                    "deferring %s until the robots crawl delay elapses (%.0fs left)",
                    page_query.url,
                    exc.remaining,
                )
                return
            except FetchError as exc:
                self.stats.http_errors += 1
                logger.warning("search fetch failed for %s: %s", page_query.url, exc)
                return

            status = int(getattr(response, "status", 0) or 0)
            if status >= 400:
                self.stats.http_errors += 1
                logger.warning("search returned HTTP %s for %s", status, page_query.url)
                return

            try:
                parsed = self.adapter.parse_search(_html(response), page_query)
            except ParseError as exc:
                self.stats.parse_errors += 1
                logger.warning("search parse failed for %s: %s", page_query.url, exc)
                return

            self.stats.pages_scanned += 1
            total_pages = parsed.total_pages or page
            known_ids = (
                self.db.existing_external_ids(
                    self.options.source_id,
                    [item.external_id for item in parsed.items],
                )
                if self.options.newest_only
                else set()
            )

            batch: list[Listing] = []
            for item in parsed.items:
                if item.external_id in known_ids:
                    self.stats.known += 1
                    self.stats.discovered += 1
                    continue
                if self._discovery_limit_reached():
                    break
                self._discovery_count += 1
                self.stats.discovered += 1
                if not self._resolve_summary(item):
                    continue
                if self.options.mode == "discover":
                    if self.options.dry_run:
                        print(json.dumps(item.to_json_dict(), ensure_ascii=False))
                    continue

                detail = item
                if self._hydrates_details():
                    detail = self._fetch_detail(item)
                    if detail is None:
                        self.stats.details_failed += 1
                        continue
                    self.stats.details_ok += 1
                else:
                    self.stats.details_skipped += 1
                if self.options.dry_run:
                    print(json.dumps(detail.to_json_dict(), ensure_ascii=False))
                else:
                    batch.append(detail)

            if batch:
                counts = self.db.upsert_listings(batch)
                self.stats.inserted += counts["inserted"]
                self.stats.updated += counts["updated"]
                self.stats.unchanged += counts["unchanged"]
                if self.on_saved is not None:
                    try:
                        self.on_saved(batch)
                    except Exception:  # geocoding queue must not fail the scrape
                        logger.exception("on_saved hook failed")

            if page == 1 or page % 10 == 0 or page >= total_pages:
                logger.info(
                    "page %s %s/%s batch=%s hydrated=%s skipped=%s known=%s failed=%s "
                    "inserted=%s updated=%s",
                    query.key,
                    page,
                    total_pages,
                    len(parsed.items),
                    len(batch),
                    len(known_ids),
                    self.stats.details_skipped,
                    self.stats.details_failed,
                    self.stats.inserted,
                    self.stats.updated,
                )
            page += 1

    def _resolve_summary(self, item: Listing) -> bool:
        match = self._resolve(item)
        if match is None:
            self.stats.skipped_region += 1
            return False
        item.voivodeship = match.voivodeship
        item.county = item.county or match.county
        item.city = item.city or match.city
        return True

    def _hydrates_details(self) -> bool:
        config = self.source_config
        return config is None or config.hydrate_details

    def _fetch_detail(self, summary: Listing) -> Listing | None:
        url = summary.url
        resolver = getattr(self.adapter, "detail_url", None)
        if callable(resolver):
            url = resolver(summary)
        try:
            response = self.client.get(url)
        except RobotsDenied:
            self.stats.skipped_robots += 1
            logger.warning("robots.txt disallows detail %s", url)
            return None
        except CrawlDeferred as exc:
            self.stats.deferred += 1
            logger.info(
                "deferring detail %s until the robots crawl delay elapses (%.0fs left)",
                url,
                exc.remaining,
            )
            return None
        except FetchError as exc:
            self.stats.http_errors += 1
            logger.warning("detail fetch failed for %s: %s", url, exc)
            return None

        status = int(getattr(response, "status", 0) or 0)
        if status in {404, 410}:
            logger.info("detail removed for %s (HTTP %s)", url, status)
            return None
        if status >= 400:
            self.stats.http_errors += 1
            logger.warning("detail returned HTTP %s for %s", status, url)
            return None

        try:
            detail = self.adapter.parse_detail(_html(response), summary)
        except ParseError as exc:
            self.stats.parse_errors += 1
            logger.warning("detail parse failed for %s: %s", url, exc)
            return None

        match = self._resolve(detail)
        if match is None:
            self.stats.skipped_region += 1
            return None

        detail.voivodeship = match.voivodeship
        return detail

    def _resolve(self, listing: Listing) -> RegionMatch | None:
        match = self.regions.resolve(
            province=listing.voivodeship,
            county=listing.county,
            city=listing.city,
            lat=listing.lat,
            lng=listing.lng,
        )
        if match is None:
            return None
        if match.voivodeship not in self.options.regions:
            return None
        return match


def _html(response: Any) -> str:
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("content-type", "")).lower()
    if "json" in content_type:
        body = getattr(response, "body", b"")
        if isinstance(body, bytes):
            return body.decode(getattr(response, "encoding", None) or "utf-8", "replace")
    html = getattr(response, "html_content", None)
    if isinstance(html, str):
        return html

    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        encoding = getattr(response, "encoding", None) or "utf-8"
        return body.decode(encoding, errors="replace")
    return str(body)

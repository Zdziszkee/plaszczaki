"""Scheduled incremental runs ("watch" mode).

The watcher polls only the sources that opted into ``incremental`` in
``config/sources.json``. Each pass runs the sources whose ``refresh_minutes``
have elapsed since their last ``incremental`` run; an incremental run reads the
newest search pages, skips listings already stored and hydrates only new detail
pages.

The module is intentionally standalone: it does not import :mod:`cli`, so the
CLI can wire it in without an import cycle.
"""

from __future__ import annotations

import fcntl
import logging
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from plaszczaki_scraper.adapters.registry import build_adapter
from plaszczaki_scraper.config import ConfigError, SourceConfig, SourcesConfig
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.http import HttpClient
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry
from plaszczaki_scraper.robots import default_cache_dir
from plaszczaki_scraper.runner import RunMode, Runner, RunOptions
from plaszczaki_scraper.scoring import score_database

logger = logging.getLogger(__name__)

# The mode recorded in ``scraper_run``; ``db.last_run_at`` defaults to it.
WATCH_MODE = "incremental"
DEFAULT_INTERVAL_SECONDS = 60.0
MINUTE_MS = 60_000

# Transactions that get their own region lane. Everything else (auctions,
# leases, mixed feeds) shares one lane so a rent/sale budget never hides it.
PRIMARY_OFFER_TYPES: tuple[str, ...] = ("sale", "rent")
SECONDARY_OFFER_TYPES: tuple[str, ...] = ("lease", "auction", "other")


def watch_mode(
    offer_types: Sequence[str] = (),
    region_ids: Sequence[str] = (),
    lane: str = "",
) -> RunMode:
    """A watch run mode, separate per lane, transaction and region filter.

    ``rent`` and ``sale`` jobs (and per-voivodeship jobs) must not share a
    last-run timestamp, otherwise the second job of the pair is skipped as
    "not due". Browser-backed sources get their own ``stealth`` lane as well.
    """
    parts: list[str] = []
    if lane:
        parts.append(lane)
    if offer_types:
        parts.append(",".join(sorted(set(offer_types))))
    if region_ids:
        parts.append(",".join(sorted(set(region_ids))))
    if not parts:
        return "incremental"
    return cast(RunMode, "incremental:" + ":".join(parts))


def _now_ms() -> int:
    return int(time.time() * 1000)


def default_lock_path() -> Path:
    """The lock file ``run``/``run-all`` already use."""
    return default_cache_dir().parent / "scraper.lock"


@contextmanager
def file_lock(path: Path) -> Generator[bool]:
    """Non-blocking exclusive lock, mirroring ``cli.file_lock``.

    Duplicated instead of imported so that ``cli`` can import ``watch`` without
    creating a cycle.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _refresh_minutes(source: SourceConfig) -> int:
    """The poll interval; adapter sources default to one hour."""
    return source.refresh_minutes or 60


def is_due(source: SourceConfig, last_run_ms: int | None, now_ms: int) -> bool:
    """Whether ``source`` should run at ``now_ms``.

    A source never run before is always due. A timestamp in the future (clock
    skew, or a run started by another process) is treated as not due.
    """
    if last_run_ms is None:
        return True
    return now_ms - last_run_ms >= _refresh_minutes(source) * MINUTE_MS


def _watchable(catalog: SourcesConfig) -> list[SourceConfig]:
    """Enabled, incremental adapter sources in a stable order."""
    return sorted(
        (source for source in catalog.enabled_adapters() if source.incremental),
        key=lambda source: (_refresh_minutes(source), source.id),
    )


def due_sources(
    catalog: SourcesConfig,
    db: Database,
    now_ms: int,
    *,
    offer_types: Sequence[str] = (),
    region_ids: Sequence[str] | None = None,
) -> list[SourceConfig]:
    """Watchable sources whose poll interval has elapsed, sorted by id.

    The run mode matches :func:`run_watch_once`, so a rent/śląskie job is due
    independently of a sale/małopolskie one.
    """
    mode = watch_mode(offer_types, region_ids or ())
    return [
        source
        for source in _watchable(catalog)
        if is_due(source, db.last_run_at(source.id, mode), now_ms)
    ]


def _select_sources(
    catalog: SourcesConfig,
    sources: list[str] | None,
) -> list[SourceConfig]:
    """Apply the optional ``--source`` filter to the watchable set."""
    watchable = _watchable(catalog)
    if sources is None:
        return watchable

    wanted = set(sources)
    unknown = sorted(wanted - set(catalog.sources))
    if unknown:
        raise ConfigError(f"unknown sources: {unknown}")

    watchable_ids = {source.id for source in watchable}
    unwatchable = sorted(wanted - watchable_ids)
    if unwatchable:
        raise ConfigError(
            "sources are not enabled+incremental in config/sources.json: " + ", ".join(unwatchable),
        )
    return [source for source in watchable if source.id in wanted]


def run_watch_once(
    *,
    db: Database,
    catalog: SourcesConfig,
    regions: RegionRegistry,
    sources: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    now_ms: int | None = None,
    lock_path: Path | None = None,
    on_saved: Callable[[Sequence[Listing]], None] | None = None,
    limit: int | None = None,
    offer_types: Sequence[str] = (),
    region_ids: Sequence[str] | None = None,
    mode_lane: str = "",
) -> dict[str, Any]:
    """Run one watch pass over the due sources.

    Failures are isolated per source: a broken adapter is reported as
    ``status="failed"`` and the pass continues. The caller is expected to have
    verified the schema. Returns ``{"sources": n, "results": [...]}`` where
    ``results`` contains one entry per selected source (including skipped ones).
    """
    current_ms = now_ms if now_ms is not None else _now_ms()
    lock = lock_path if lock_path is not None else default_lock_path()
    mode = watch_mode(offer_types, region_ids or (), mode_lane)
    selected_regions = tuple(region_ids) if region_ids else regions.default_regions
    results: list[dict[str, Any]] = []

    for source in _select_sources(catalog, sources):
        last_run = db.last_run_at(source.id, mode)
        if not force and not is_due(source, last_run, current_ms):
            logger.debug("watch skip %s: not due", source.id)
            results.append(
                {
                    "source_id": source.id,
                    "mode": mode,
                    "status": "skipped",
                    "reason": "not due",
                    "last_run_at": last_run,
                    "next_due_at": (
                        None
                        if last_run is None
                        else last_run + _refresh_minutes(source) * MINUTE_MS
                    ),
                },
            )
            continue

        with file_lock(lock) as acquired:
            if not acquired:
                logger.warning("watch skip %s: scraper lock is held", source.id)
                results.append(
                    {
                        "source_id": source.id,
                        "mode": mode,
                        "status": "skipped",
                        "reason": "locked",
                    },
                )
                continue

            if not dry_run:
                adapter_name = source.adapter
                assert adapter_name is not None, f"source '{source.id}' has no adapter"
                db.upsert_source(
                    source_id=source.id,
                    label=source.label,
                    homepage_url=source.urls[0],
                    adapter=adapter_name,
                    enabled=source.enabled,
                )
            logger.info("watch run source=%s", source.id)
            client: HttpClient | None = None
            try:
                adapter = build_adapter(source, regions=regions)
                client = HttpClient(
                    rps=source.rps,
                    fetch_mode=source.fetch_mode,
                    browser_headless=source.browser_headless,
                    browser_solve_cloudflare=source.browser_solve_cloudflare,
                    browser_wait_ms=source.browser_wait_ms,
                    crawl_delay_mode=source.crawl_delay_mode,
                )
                result = Runner(
                    options=RunOptions(
                        source_id=source.id,
                        mode=mode,
                        regions=selected_regions,
                        offer_types=tuple(offer_types),
                        newest_only=True,
                        incremental_pages=source.incremental_pages,
                        dry_run=dry_run,
                        limit=limit,
                    ),
                    adapter=adapter,
                    db=db,
                    client=client,
                    regions=regions,
                    source_config=source,
                    on_saved=on_saved,
                ).run()
            except Exception as exc:  # keep one broken adapter from stopping the pass
                logger.exception("watch source failed: %s", source.id)
                result = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                if client is not None:
                    client.close()

        results.append({"source_id": source.id, "mode": mode, **result})

    scoring = None
    if not dry_run:
        scoring = score_database(db, pending_only=True)
    return {"sources": len(results), "results": results, "scoring": scoring}


def plan_watch_lanes(
    catalog: SourcesConfig,
    regions: RegionRegistry,
    region_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Partition the watchable sources into independent polling lanes.

    Every region gets its own ``sale`` and ``rent`` lane, so a shared discovery
    limit or a shared last-run timestamp can never starve the second
    transaction or voivodeship. Sources whose queries are identical for every
    region (national feeds such as Gdom or Poczta Polska) run once in a
    ``national`` lane; sources with auction/lease/mixed queries run in an
    ``other`` lane. Each lane is a separate :func:`run_watch_once` call with its
    own ``watch_mode`` key.
    """
    selected = tuple(region_ids) if region_ids else regions.default_regions
    primary: dict[str, set[str]] = {
        f"{region}:{offer}": set() for offer in PRIMARY_OFFER_TYPES for region in selected
    }
    other: set[str] = set()
    national: set[str] = set()
    stealth: set[str] = set()

    for source in _watchable(catalog):
        if source.fetch_mode == "stealth":
            # Browser-backed sources get their own lane: launching Chromium must
            # not delay the normal HTTP sale/rent passes.
            stealth.add(source.id)
            continue
        try:
            adapter = build_adapter(source, regions=regions)
            by_region = {region: adapter.queries((region,)) for region in selected}
        except Exception:
            # Let the run report the broken adapter once instead of four times.
            national.add(source.id)
            continue

        url_sets = [frozenset(query.url for query in by_region[region]) for region in selected]
        non_empty = [urls for urls in url_sets if urls]
        if non_empty and len(non_empty) == len(url_sets) and len(set(url_sets)) == 1:
            national.add(source.id)
            continue

        for region in selected:
            queries = by_region[region]
            for offer in PRIMARY_OFFER_TYPES:
                if any(query.offer_type == offer for query in queries):
                    primary[f"{region}:{offer}"].add(source.id)
            if any(query.offer_type in SECONDARY_OFFER_TYPES for query in queries):
                other.add(source.id)

    plan: list[dict[str, Any]] = [
        {
            "key": key,
            "region_ids": (key.split(":", 1)[0],),
            "offer_types": (key.split(":", 1)[1],),
            "sources": sorted(ids),
        }
        for key, ids in primary.items()
    ]
    plan.append(
        {
            "key": "other",
            "region_ids": selected,
            "offer_types": SECONDARY_OFFER_TYPES,
            "sources": sorted(other),
        },
    )
    plan.append(
        {
            "key": "national",
            "region_ids": selected,
            "offer_types": (),
            "sources": sorted(national),
        },
    )
    if stealth:
        plan.append(
            {
                "key": "stealth",
                "region_ids": selected,
                "offer_types": (),
                "sources": sorted(stealth),
                "mode_lane": "stealth",
            },
        )
    return plan


def run_watch_lanes(
    *,
    db: Database,
    catalog: SourcesConfig,
    regions: RegionRegistry,
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    now_ms: int | None = None,
    lock_path: Path | None = None,
    on_saved: Callable[[Sequence[Listing]], None] | None = None,
    region_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run one pass over every independent polling lane.

    The same ``now_ms`` is used for every lane, so a slow first lane cannot make
    later lanes look more due than they are. Returns one report per lane.
    """
    current_ms = now_ms if now_ms is not None else _now_ms()
    passes: list[dict[str, Any]] = []
    for lane in plan_watch_lanes(catalog, regions, region_ids or ()):
        report = run_watch_once(
            db=db,
            catalog=catalog,
            regions=regions,
            sources=lane["sources"],
            force=force,
            dry_run=dry_run,
            now_ms=current_ms,
            lock_path=lock_path,
            on_saved=on_saved,
            limit=limit,
            offer_types=lane["offer_types"],
            region_ids=lane["region_ids"],
            mode_lane=lane.get("mode_lane", ""),
        )
        passes.append({"lane": lane["key"], **report})
    return {"lanes": len(passes), "passes": passes}


def watch_loop(
    *,
    db: Database,
    catalog: SourcesConfig,
    regions: RegionRegistry,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    max_cycles: int | None = None,
    sources: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    lock_path: Path | None = None,
    on_saved: Callable[[Sequence[Listing]], None] | None = None,
    limit: int | None = None,
    offer_types: Sequence[str] = (),
    region_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Run watch passes until interrupted or ``max_cycles`` is reached.

    The config objects are captured once; restart the process to pick up edits
    to ``config/sources.json``/``regions.json``. Returns one pass report per cycle.
    """
    if interval <= 0:
        raise ValueError("interval must be positive")
    if max_cycles is not None and max_cycles < 0:
        raise ValueError("max_cycles must be zero or positive")

    reports: list[dict[str, Any]] = []
    try:
        while max_cycles is None or len(reports) < max_cycles:
            report = run_watch_once(
                db=db,
                catalog=catalog,
                regions=regions,
                sources=sources,
                force=force,
                dry_run=dry_run,
                lock_path=lock_path,
                on_saved=on_saved,
                limit=limit,
                offer_types=offer_types,
                region_ids=region_ids,
            )
            reports.append(report)
            logger.info(
                "watch pass %s: selected=%s ran=%s",
                len(reports),
                report["sources"],
                sum(1 for item in report["results"] if item["status"] != "skipped"),
            )
            if max_cycles is not None and len(reports) >= max_cycles:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("watch interrupted after %s pass(es)", len(reports))
    return reports

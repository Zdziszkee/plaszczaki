"""Scheduled incremental runs ("watch" mode).

The watcher polls only the sources that opted into ``incremental`` in
``config/adapters.json``. Each pass runs the sources whose ``poll_minutes`` have
elapsed since their last ``incremental`` run; an incremental run reads the
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
from typing import Any

from plaszczaki_scraper.adapters.registry import build_adapter
from plaszczaki_scraper.config import AdapterConfig, AdaptersConfig, ConfigError
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.http import HttpClient
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry
from plaszczaki_scraper.robots import default_cache_dir
from plaszczaki_scraper.runner import Runner, RunOptions

logger = logging.getLogger(__name__)

# The mode recorded in ``scraper_run``; ``db.last_run_at`` defaults to it.
WATCH_MODE = "incremental"
DEFAULT_INTERVAL_SECONDS = 60.0
MINUTE_MS = 60_000


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


def is_due(source: AdapterConfig, last_run_ms: int | None, now_ms: int) -> bool:
    """Whether ``source`` should run at ``now_ms``.

    A source never run before is always due. A timestamp in the future (clock
    skew, or a run started by another process) is treated as not due.
    """
    if last_run_ms is None:
        return True
    return now_ms - last_run_ms >= source.poll_minutes * MINUTE_MS


def _watchable(adapters: AdaptersConfig) -> list[AdapterConfig]:
    """Enabled, incremental sources in a stable order."""
    return sorted(
        (source for source in adapters.sources.values() if source.enabled and source.incremental),
        key=lambda source: (source.poll_minutes, source.id),
    )


def due_sources(adapters: AdaptersConfig, db: Database, now_ms: int) -> list[AdapterConfig]:
    """Watchable sources whose poll interval has elapsed, sorted by id."""
    return [
        source
        for source in _watchable(adapters)
        if is_due(source, db.last_run_at(source.id, WATCH_MODE), now_ms)
    ]


def _select_sources(
    adapters: AdaptersConfig,
    sources: list[str] | None,
) -> list[AdapterConfig]:
    """Apply the optional ``--source`` filter to the watchable set."""
    watchable = _watchable(adapters)
    if sources is None:
        return watchable

    wanted = set(sources)
    unknown = sorted(wanted - set(adapters.sources))
    if unknown:
        raise ConfigError(f"unknown sources: {unknown}")

    watchable_ids = {source.id for source in watchable}
    unwatchable = sorted(wanted - watchable_ids)
    if unwatchable:
        raise ConfigError(
            "sources are not enabled+incremental in adapters.json: " + ", ".join(unwatchable),
        )
    return [source for source in watchable if source.id in wanted]


def run_watch_once(
    *,
    db: Database,
    adapters: AdaptersConfig,
    regions: RegionRegistry,
    sources: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    now_ms: int | None = None,
    lock_path: Path | None = None,
    on_saved: Callable[[Sequence[Listing]], None] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run one watch pass over the due sources.

    Failures are isolated per source: a broken adapter is reported as
    ``status="failed"`` and the pass continues. The caller is expected to have
    verified the schema. Returns ``{"sources": n, "results": [...]}`` where
    ``results`` contains one entry per selected source (including skipped ones).
    """
    current_ms = now_ms if now_ms is not None else _now_ms()
    lock = lock_path if lock_path is not None else default_lock_path()
    results: list[dict[str, Any]] = []

    for source in _select_sources(adapters, sources):
        last_run = db.last_run_at(source.id, WATCH_MODE)
        if not force and not is_due(source, last_run, current_ms):
            logger.debug("watch skip %s: not due", source.id)
            results.append(
                {
                    "source_id": source.id,
                    "mode": WATCH_MODE,
                    "status": "skipped",
                    "reason": "not due",
                    "last_run_at": last_run,
                    "next_due_at": (
                        None if last_run is None else last_run + source.poll_minutes * MINUTE_MS
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
                        "mode": WATCH_MODE,
                        "status": "skipped",
                        "reason": "locked",
                    },
                )
                continue

            if not dry_run:
                db.upsert_source(
                    source_id=source.id,
                    label=source.label,
                    homepage_url=source.homepage,
                    adapter=source.adapter,
                    enabled=source.enabled,
                )
            logger.info("watch run source=%s", source.id)
            try:
                adapter = build_adapter(source, regions=regions)
                client = HttpClient(rps=source.rps)
                result = Runner(
                    options=RunOptions(
                        source_id=source.id,
                        mode=WATCH_MODE,
                        regions=regions.default_regions,
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

        results.append({"source_id": source.id, "mode": WATCH_MODE, **result})

    return {"sources": len(results), "results": results}


def watch_loop(
    *,
    db: Database,
    adapters: AdaptersConfig,
    regions: RegionRegistry,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    max_cycles: int | None = None,
    sources: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    lock_path: Path | None = None,
    on_saved: Callable[[Sequence[Listing]], None] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Run watch passes until interrupted or ``max_cycles`` is reached.

    The config objects are captured once; restart the process to pick up edits
    to ``adapters.json``/``regions.json``. Returns one pass report per cycle.
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
                adapters=adapters,
                regions=regions,
                sources=sources,
                force=force,
                dry_run=dry_run,
                lock_path=lock_path,
                on_saved=on_saved,
                limit=limit,
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

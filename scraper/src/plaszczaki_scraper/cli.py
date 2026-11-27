"""Command-line interface for the scraper."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from duckdb import Error as DuckDBError

from plaszczaki_scraper.adapters.registry import build_adapter
from plaszczaki_scraper.audit import audit_sources
from plaszczaki_scraper.backfill import backfill_attributes, rehydrate_missing_districts
from plaszczaki_scraper.config import (
    ConfigError,
    RegionsConfig,
    SourceConfig,
    SourcesConfig,
    load_regions_config,
    load_sources_config,
)
from plaszczaki_scraper.db import Database, SchemaError, default_database_path
from plaszczaki_scraper.field_audit import audit_fields
from plaszczaki_scraper.geocode import GeocodeStore, GeocodeWorker
from plaszczaki_scraper.geodata import fetch_elevation, fetch_gtfs_stations, fetch_nature, fetch_poi
from plaszczaki_scraper.http import FetchError, HttpClient, user_agent
from plaszczaki_scraper.llm import LLMConfigError, check_models, models_report
from plaszczaki_scraper.llm_features import enrich_listings, rerank_top
from plaszczaki_scraper.probe import probe_sources
from plaszczaki_scraper.rcn.config import load_rcn_config
from plaszczaki_scraper.rcn.importer import import_dataset, sync_datasets
from plaszczaki_scraper.rcn.rcn_db import RcnDatabase
from plaszczaki_scraper.regions import RegionRegistry
from plaszczaki_scraper.review import review_sources
from plaszczaki_scraper.robots import default_cache_dir
from plaszczaki_scraper.runner import Runner, RunOptions
from plaszczaki_scraper.scoring import score_database
from plaszczaki_scraper.watch import run_watch_lanes, run_watch_once, watch_loop

logger = logging.getLogger(__name__)

# Transactions accepted by ``--offer-type``; the adapters emit these labels.
OFFER_FILTERS = ("sale", "rent", "lease", "auction", "other")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plaszczaki-scraper",
        description="Polite Polish real-estate data collection sidecar.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="discover and hydrate listings")
    run.add_argument("--source", help="source id from config/sources.json")
    run.add_argument(
        "--mode",
        choices=("discover", "run", "backfill"),
        default="run",
    )
    run.add_argument(
        "--region",
        help="comma-separated voivodeship ids (default: regions.json 'default')",
    )
    run.add_argument(
        "--offer-type",
        type=_offer_type_filter,
        default=(),
        help="comma-separated transactions to scrape: sale,rent,lease,auction,other",
    )
    run.add_argument("--limit", type=int, help="maximum items/details for this run")
    run.add_argument("--max-pages", type=int, help="maximum search pages per query")
    run.add_argument("--rps", type=float, help="override requests per second")
    run.add_argument("--dry-run", action="store_true", help="print records, do not write")
    run.add_argument(
        "--newest",
        action="store_true",
        help="only read the newest pages and skip listings already in the database",
    )
    run.add_argument(
        "--incremental-pages",
        type=int,
        help="newest pages per query in incremental mode (default: source config)",
    )
    run.add_argument(
        "--lock-file",
        help="override the scraper lock path (env: PLASZCZAKI_LOCK_FILE)",
    )

    run_all = subparsers.add_parser(
        "run-all",
        help="run every enabled adapter, optionally filtered by tier",
    )
    run_all.add_argument("--tier", type=int, help="only run this catalog tier")
    run_all.add_argument(
        "--mode",
        choices=("discover", "run", "backfill"),
        default="run",
    )
    run_all.add_argument(
        "--region",
        help="comma-separated voivodeship ids (default: regions.json 'default')",
    )
    run_all.add_argument(
        "--offer-type",
        type=_offer_type_filter,
        default=(),
        help="comma-separated transactions to scrape: sale,rent,lease,auction,other",
    )
    run_all.add_argument("--limit", type=int, help="maximum items/details per source")
    run_all.add_argument("--max-pages", type=int, help="maximum search pages per query")
    run_all.add_argument("--dry-run", action="store_true", help="print records, do not write")
    run_all.add_argument("--fail-fast", action="store_true", help="stop after the first failure")
    run_all.add_argument(
        "--only-non-incremental",
        action="store_true",
        help="only run enabled adapters that the incremental watch never polls",
    )
    run_all.add_argument(
        "--newest",
        action="store_true",
        help="only read the newest pages and skip listings already in the database",
    )
    run_all.add_argument(
        "--lock-file",
        help="override the scraper lock path (env: PLASZCZAKI_LOCK_FILE)",
    )

    watch = subparsers.add_parser(
        "watch",
        help="poll due sources incrementally (newest pages only)",
    )
    watch.add_argument("--once", action="store_true", help="run one pass and exit")
    watch.add_argument("--interval", type=float, default=60.0, help="seconds between passes")
    watch.add_argument("--max-cycles", type=int, help="stop after this many passes")
    watch.add_argument("--source", help="comma-separated source ids")
    watch.add_argument(
        "--region",
        help="comma-separated voivodeship ids (default: regions.json 'default')",
    )
    watch.add_argument("--force", action="store_true", help="ignore the refresh schedule")
    watch.add_argument(
        "--offer-type",
        type=_offer_type_filter,
        default=(),
        help="comma-separated transactions to poll: sale,rent,lease,auction,other",
    )
    watch.add_argument("--dry-run", action="store_true", help="print records, do not write")
    watch.add_argument("--limit", type=int, help="maximum new listings per source per pass")
    watch.add_argument(
        "--geocode-limit",
        type=int,
        default=50,
        help="street-address geocoding jobs to drain after the pass (0 disables)",
    )

    cycle = subparsers.add_parser(
        "cycle",
        help="run one pass over every polling lane (region x transaction, other, national)",
    )
    cycle.add_argument(
        "--region",
        help="comma-separated voivodeship ids (default: regions.json 'default')",
    )
    cycle.add_argument(
        "--limit",
        type=int,
        default=200,
        help="maximum new listings per source per lane (each lane has its own budget)",
    )
    cycle.add_argument("--force", action="store_true", help="ignore the refresh schedule")
    cycle.add_argument("--dry-run", action="store_true", help="print records, do not write")
    cycle.add_argument(
        "--lock-file",
        help="override the scraper lock path for every lane",
    )
    cycle.add_argument(
        "--geocode-limit",
        type=int,
        default=50,
        help="street-address geocoding jobs to drain after the pass (0 disables)",
    )

    audit = subparsers.add_parser("audit", help="check robots.txt for all source URLs")
    audit.add_argument("--limit", type=int)
    audit.add_argument("--rps", type=float, default=1.0)

    field_audit = subparsers.add_parser(
        "field-audit",
        help="report canonical field coverage and raw attributes in extra",
    )
    field_audit.add_argument("--source", help="only audit one source id")
    field_audit.add_argument("--output", type=Path, help="report path")
    field_audit.add_argument("--top", type=int, default=20, help="ranked gaps to print")

    backfill = subparsers.add_parser(
        "backfill-attributes",
        help="normalize attributes in stored listing.extra payloads",
    )
    backfill.add_argument("--source", help="only backfill one source id")
    backfill.add_argument("--limit", type=int, help="maximum stored listings to process")
    backfill.add_argument("--dry-run", action="store_true", help="report changes without writing")
    backfill.add_argument(
        "--rehydrate-district",
        action="store_true",
        help="refetch detail pages for rows still missing district (network requests)",
    )
    backfill.add_argument("--rps", type=float, help="override rehydration request rate")
    backfill.add_argument("--lock-file", help="override the scraper lock path")

    probe = subparsers.add_parser("probe", help="fetch one page per source and report structure")
    probe.add_argument("--source", help="comma-separated source ids from sources.json")
    probe.add_argument("--tier", type=int, help="probe one tier")
    probe.add_argument("--all", action="store_true", help="probe every non-excluded source")
    probe.add_argument(
        "--fields",
        action="store_true",
        help="inventory raw page fields and report parsed canonical coverage",
    )
    probe.add_argument("--limit", type=int)
    probe.add_argument("--rps", type=float, default=1.0)

    geocode = subparsers.add_parser(
        "geocode",
        help="resolve queued street addresses with Nominatim",
    )
    geocode.add_argument("--limit", type=int, default=100)
    geocode.add_argument("--rps", type=float, default=1.0)
    geocode.add_argument("--dry-run", action="store_true")
    geocode.add_argument(
        "--enqueue-existing",
        action="store_true",
        help="queue stored listings that still have no coordinates",
    )
    geocode.add_argument(
        "--requeue-rejected",
        action="store_true",
        help="re-open rejected jobs so the improved address matcher can retry them",
    )

    score = subparsers.add_parser("score", help="score stored offers for attractiveness and value")
    score.add_argument("--source", help="only score one source id")
    score.add_argument("--limit", type=int, help="maximum stored offers to process")
    score.add_argument("--pending", action="store_true", help="only new or changed offers")
    score.add_argument("--dry-run", action="store_true", help="calculate without writing")

    geodata = subparsers.add_parser("geodata", help="refresh open-data inputs for scoring")
    geodata.add_argument(
        "--part",
        choices=("all", "gtfs", "nature", "poi", "elevation"),
        default="all",
    )
    geodata.add_argument("--force", action="store_true", help="ignore cached downloads")
    geodata.add_argument("--rps", type=float, default=1.0)

    rcn = subparsers.add_parser("rcn", help="import and inspect official RCN transactions")
    rcn_sub = rcn.add_subparsers(dest="rcn_command", required=True)
    rcn_import = rcn_sub.add_parser("import", help="import one region/layer snapshot")
    rcn_import.add_argument("--region", help="voivodeship id (default: all configured regions)")
    rcn_import.add_argument(
        "--kind",
        choices=("dzialki", "budynki", "lokale"),
        help="object layer (default: all layers)",
    )
    rcn_import.add_argument("--force", action="store_true", help="ignore ETag and redownload")
    rcn_import.add_argument(
        "--dry-run", action="store_true", help="parse and print without writing"
    )
    rcn_import.add_argument("--limit", type=int, help="maximum source rows (skips reconciliation)")
    rcn_import.add_argument("--lock-file", help="override the RCN lock path")
    rcn_sync = rcn_sub.add_parser("sync", help="sync every configured RCN dataset")
    rcn_sync.add_argument("--region", help="voivodeship id (default: all configured regions)")
    rcn_sync.add_argument("--force", action="store_true", help="ignore ETag and redownload")
    rcn_sync.add_argument("--lock-file", help="override the RCN lock path")
    rcn_sub.add_parser("status", help="show RCN import freshness and row counts")

    subparsers.add_parser("verify-schema", help="check the SQLite schema")
    subparsers.add_parser("list-sources", help="show configured adapters and inventory")

    review = subparsers.add_parser(
        "review-sources",
        help="re-check blocked/planned sources for changes",
    )
    review.add_argument("--source", help="comma-separated source ids")
    review.add_argument("--limit", type=int)
    review.add_argument("--rps", type=float, default=0.2)
    review.add_argument(
        "--due",
        action="store_true",
        help="only sources past their recheck_days window",
    )
    review.add_argument("--force", action="store_true", help="ignore the due window")

    dedupe = subparsers.add_parser(
        "dedupe",
        help="recompute canonical listing keys for cross-source deduplication",
    )
    dedupe.add_argument(
        "--backfill",
        action="store_true",
        help="recompute keys for every stored listing",
    )

    llm = subparsers.add_parser("llm", help="inspect and test the OpenCode inference lane")
    llm_sub = llm.add_subparsers(dest="llm_command", required=True)
    llm_sub.add_parser("models", help="list free models and their capabilities")
    check = llm_sub.add_parser("check", help="try free models with a tiny JSON request")
    check.add_argument("--limit", type=int, default=10)
    check.add_argument("--timeout", type=float, default=60.0)
    enrich = llm_sub.add_parser("enrich", help="extract scoring facts from listing descriptions")
    enrich.add_argument("--limit", type=int, default=50)
    enrich.add_argument("--dry-run", action="store_true")
    rerank = llm_sub.add_parser("rerank", help="LLM re-rank the deterministic top-offer shortlist")
    rerank.add_argument("--limit", type=int, default=20)
    rerank.add_argument("--candidates", type=int, default=50)
    rerank.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("scrapling").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger.info("database: %s", default_database_path())

    try:
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "run-all":
            return _cmd_run_all(args)
        if args.command == "watch":
            return _cmd_watch(args)
        if args.command == "cycle":
            return _cmd_cycle(args)
        if args.command == "audit":
            return _cmd_audit(args)
        if args.command == "field-audit":
            return _cmd_field_audit(args)
        if args.command == "backfill-attributes":
            return _cmd_backfill_attributes(args)
        if args.command == "probe":
            return _cmd_probe(args)
        if args.command == "geocode":
            return _cmd_geocode(args)
        if args.command == "score":
            return _cmd_score(args)
        if args.command == "geodata":
            return _cmd_geodata(args)
        if args.command == "rcn":
            return _cmd_rcn(args)
        if args.command == "verify-schema":
            return _cmd_verify_schema()
        if args.command == "list-sources":
            return _cmd_list_sources()
        if args.command == "review-sources":
            return _cmd_review_sources(args)
        if args.command == "dedupe":
            return _cmd_dedupe(args)
        if args.command == "llm":
            return _cmd_llm(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    except SchemaError as exc:
        print(f"schema error: {exc}", file=sys.stderr)
        return 1
    except (FetchError, ValueError, LLMConfigError, httpx.HTTPError, DuckDBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    catalog = load_sources_config()
    regions_config = load_regions_config()
    source = _select_source(catalog, args.source)
    regions = _select_regions(regions_config, args.region)
    _warn_limit_without_region(args.limit, regions)
    registry = RegionRegistry(regions_config)
    adapter = build_adapter(source, regions=registry)

    rps = args.rps if args.rps is not None else source.rps
    client = HttpClient(
        rps=rps,
        robots=None,
        ua=user_agent(),
        fetch_mode=source.fetch_mode,
        browser_headless=source.browser_headless,
        browser_solve_cloudflare=source.browser_solve_cloudflare,
        browser_wait_ms=source.browser_wait_ms,
        crawl_delay_mode=source.crawl_delay_mode,
    )
    options = RunOptions(
        source_id=source.id,
        mode=args.mode,
        regions=regions,
        offer_types=args.offer_type,
        limit=args.limit,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
        newest_only=args.newest,
        incremental_pages=args.incremental_pages or source.incremental_pages,
    )

    db = Database(default_database_path())
    store = GeocodeStore()

    def hook(batch: Any) -> None:
        for item in batch:
            store.enqueue(item)

    try:
        db.verify_schema()
        if not args.dry_run:
            db.upsert_source(
                source_id=source.id,
                label=source.label,
                homepage_url=source.urls[0],
                adapter=_adapter_name(source),
                enabled=source.enabled,
            )
        with file_lock(_lock_path(args.lock_file)) as acquired:
            if not acquired:
                print("another scraper run is already active; skipping", file=sys.stderr)
                return 0
            result = Runner(
                options=options,
                adapter=adapter,
                db=db,
                client=client,
                regions=registry,
                source_config=source,
                on_saved=hook,
            ).run()
            if not args.dry_run:
                result["scoring"] = score_database(db, source_id=source.id, pending_only=True)
    finally:
        client.close()
        store.close()
        db.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "failed":
        return 1
    if result["status"] == "partial":
        return 2
    return 0


def _cmd_field_audit(args: argparse.Namespace) -> int:
    catalog = load_sources_config()
    if args.source is not None and args.source not in catalog.sources:
        raise ConfigError(f"unknown source: {args.source}")
    db = Database(default_database_path())
    try:
        db.verify_schema()
        report = audit_fields(db, source_id=args.source, output_path=args.output)
    finally:
        db.close()
    print(
        json.dumps(
            {
                "total": report["total"],
                "sources": len(report["sources"]),
                "gaps": len(report["gaps"]),
                "output": report["output"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    for gap in report["gaps"][: args.top]:
        coverage = gap["coverage"]
        percentage = "n/a" if coverage is None else f"{coverage:.0%}"
        print(
            f"{gap['source_id']:<28} {gap['field']:<26} "
            f"{gap['filled']:>6}/{gap['applicable']:<6} {percentage:>4} "
            f"missing={gap['missing']} impact={gap['impact']}"
        )
    return 0


def _cmd_backfill_attributes(args: argparse.Namespace) -> int:
    catalog = load_sources_config()
    if args.source is not None and args.source not in catalog.sources:
        raise ConfigError(f"unknown source: {args.source}")
    db = Database(default_database_path())
    try:
        db.verify_schema()
        if args.rehydrate_district:
            with file_lock(_lock_path(args.lock_file)) as acquired:
                if not acquired:
                    print("another scraper run is already active; skipping", file=sys.stderr)
                    return 0
                attributes = backfill_attributes(
                    db,
                    source_id=args.source,
                    limit=args.limit,
                    dry_run=args.dry_run,
                )
                rehydration = rehydrate_missing_districts(
                    db,
                    source_id=args.source,
                    limit=args.limit,
                    rps=args.rps,
                    dry_run=args.dry_run,
                )
        else:
            attributes = backfill_attributes(
                db,
                source_id=args.source,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            rehydration = None
    finally:
        db.close()
    report: dict[str, Any] = {"attributes": attributes}
    if rehydration is not None:
        report["rehydration"] = rehydration
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    catalog = load_sources_config()
    sources = _select_probe_sources(catalog, args)
    if args.fields:
        sources = [source for source in sources if source.status == "implemented"]
    if not sources:
        raise ConfigError("no sources selected for probe")

    report = probe_sources(sources, rps=args.rps, inspect_fields=args.fields)
    for entry in report["entries"]:
        status = entry["http_status"] if entry["http_status"] is not None else "---"
        flags = []
        if entry["challenge"]:
            flags.append("challenge")
        if entry["has_next_data"]:
            flags.append("next-data")
        if entry["has_ld_json"]:
            flags.append("ld+json")
        if entry["error"]:
            flags.append(f"error={entry['error']}")
        if entry["field_error"]:
            flags.append(f"fields-error={entry['field_error']}")
        if entry["listings_found"] is not None:
            flags.append(f"listings={entry['listings_found']}")
            flags.append(f"raw-fields={len(entry['available_field_keys'] or [])}")
        print(
            f"{entry['source_id']:<28} {status!s:>3} "
            f"{entry['bytes']:>7}B links={entry['link_count']:<4} "
            f"{' '.join(flags)}",
        )
    print(
        json.dumps(
            {
                "total": report["total"],
                "ok": report["ok"],
                "output": report["output"],
            },
            ensure_ascii=False,
        ),
    )
    return 0


def _select_probe_sources(catalog: SourcesConfig, args: argparse.Namespace) -> list[SourceConfig]:
    if args.source:
        ids = [item.strip() for item in args.source.split(",") if item.strip()]
        unknown = [source_id for source_id in ids if source_id not in catalog.sources]
        if unknown:
            raise ConfigError(f"unknown sources: {unknown}")
        return [catalog.sources[source_id] for source_id in ids]

    if args.tier is not None:
        return [
            source
            for source in catalog.by_tier(args.tier)
            if source.status not in {"excluded", "blocked"}
        ]

    if args.all:
        return [
            source
            for source in catalog.sources.values()
            if source.status not in {"excluded", "blocked"}
        ]

    return list(catalog.implemented())


def _cmd_run_all(args: argparse.Namespace) -> int:
    catalog = load_sources_config()
    regions_config = load_regions_config()
    regions = _select_regions(regions_config, args.region)
    _warn_limit_without_region(args.limit, regions)
    registry = RegionRegistry(regions_config)

    selected = [
        source
        for source in catalog.enabled_adapters()
        if (args.tier is None or source.tier == args.tier)
        and (not args.only_non_incremental or not source.incremental)
    ]
    if not selected:
        raise ConfigError("no enabled adapters match the requested tier")

    db = Database(default_database_path())
    store = GeocodeStore()
    results: list[dict[str, Any]] = []
    failed = False
    scoring: dict[str, Any] | None = None

    def hook(batch: Any) -> None:
        for item in batch:
            store.enqueue(item)

    try:
        db.verify_schema()
        if args.only_non_incremental:
            # A short nightly pass must not always stop at the same cheap
            # sources: run the least recently attempted first, then the cheapest
            # queries, so every source gets a turn across a few nights.
            last_runs = {source.id: db.last_run_at(source.id, args.mode) for source in selected}
            query_counts = {
                source.id: _query_count(source, registry, regions) for source in selected
            }
            selected = _full_pass_order(selected, last_runs, query_counts)
        for source in selected:
            if not args.dry_run:
                db.upsert_source(
                    source_id=source.id,
                    label=source.label,
                    homepage_url=source.urls[0],
                    adapter=_adapter_name(source),
                    enabled=source.enabled,
                )

        with file_lock(_lock_path(args.lock_file)) as acquired:
            if not acquired:
                print("another scraper run is already active; skipping", file=sys.stderr)
                return 0

            for source in selected:
                logger.info("run-all source=%s", source.id)
                client: HttpClient | None = None
                result: dict[str, Any]
                try:
                    adapter = build_adapter(source, regions=registry)
                    client = HttpClient(
                        rps=source.rps,
                        robots=None,
                        ua=user_agent(),
                        fetch_mode=source.fetch_mode,
                        browser_headless=source.browser_headless,
                        browser_solve_cloudflare=source.browser_solve_cloudflare,
                        browser_wait_ms=source.browser_wait_ms,
                        crawl_delay_mode=source.crawl_delay_mode,
                    )
                    options = RunOptions(
                        source_id=source.id,
                        mode=args.mode,
                        regions=regions,
                        offer_types=args.offer_type,
                        limit=args.limit,
                        max_pages=args.max_pages,
                        dry_run=args.dry_run,
                        newest_only=args.newest,
                        incremental_pages=source.incremental_pages,
                    )
                    result = Runner(
                        options=options,
                        adapter=adapter,
                        db=db,
                        client=client,
                        regions=registry,
                        source_config=source,
                        on_saved=hook,
                    ).run()
                except Exception as exc:  # keep one broken adapter from stopping the batch
                    failed = True
                    result = {
                        "source_id": source.id,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    logger.exception("run-all source failed: %s", source.id)
                    if args.fail_fast:
                        results.append(result)
                        break
                finally:
                    if client is not None:
                        client.close()
                results.append(result)
                if result["status"] == "failed":
                    failed = True
                    if args.fail_fast:
                        break
            if not args.dry_run:
                scoring = score_database(db, pending_only=True)
    finally:
        store.close()
        db.close()

    print(
        json.dumps(
            {"sources": len(results), "results": results, "scoring": scoring},
            ensure_ascii=False,
            indent=2,
        ),
    )
    if failed:
        return 1
    if any(result["status"] == "partial" for result in results):
        return 2
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    catalog = load_sources_config()
    regions_config = load_regions_config()
    registry = RegionRegistry(regions_config)
    regions = _select_regions(regions_config, args.region) if args.region else None
    _warn_limit_without_region(args.limit, regions or regions_config.default)
    sources = (
        [item.strip() for item in args.source.split(",") if item.strip()] if args.source else None
    )

    db = Database(default_database_path())
    store = GeocodeStore()

    def hook(batch: Any) -> None:
        for item in batch:
            store.enqueue(item)

    try:
        db.verify_schema()
        report: dict[str, Any]
        if args.once:
            report = run_watch_once(
                db=db,
                catalog=catalog,
                regions=registry,
                sources=sources,
                force=args.force,
                dry_run=args.dry_run,
                on_saved=hook,
                limit=args.limit,
                offer_types=args.offer_type,
                region_ids=regions,
            )
        else:
            report = {
                "cycles": watch_loop(
                    db=db,
                    catalog=catalog,
                    regions=registry,
                    interval=args.interval,
                    max_cycles=args.max_cycles,
                    sources=sources,
                    force=args.force,
                    dry_run=args.dry_run,
                    on_saved=hook,
                    limit=args.limit,
                    offer_types=args.offer_type,
                    region_ids=regions,
                ),
            }
        if args.geocode_limit > 0 and not args.dry_run:
            report["geocode"] = GeocodeWorker(
                db=db,
                store=store,
                regions=registry,
                rps=1.0,
            ).run(limit=args.geocode_limit)
    finally:
        store.close()
        db.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    results = (
        report["results"]
        if "results" in report
        else [result for cycle in report["cycles"] for result in cycle["results"]]
    )
    if any(result.get("status") == "failed" for result in results):
        return 1
    return 0


def _cmd_cycle(args: argparse.Namespace) -> int:
    """One pass over every independent polling lane."""
    catalog = load_sources_config()
    regions_config = load_regions_config()
    registry = RegionRegistry(regions_config)
    region_ids = _select_regions(regions_config, args.region) if args.region else None

    db = Database(default_database_path())
    store = GeocodeStore()

    def hook(batch: Any) -> None:
        for item in batch:
            store.enqueue(item)

    try:
        db.verify_schema()
        report: dict[str, Any] = run_watch_lanes(
            db=db,
            catalog=catalog,
            regions=registry,
            limit=args.limit,
            force=args.force,
            dry_run=args.dry_run,
            lock_path=_lock_path(args.lock_file),
            on_saved=hook,
            region_ids=region_ids,
        )
        if args.geocode_limit > 0 and not args.dry_run:
            report["geocode"] = GeocodeWorker(
                db=db,
                store=store,
                regions=registry,
                rps=1.0,
            ).run(limit=args.geocode_limit)
    finally:
        store.close()
        db.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = any(
        result.get("status") == "failed" for lane in report["passes"] for result in lane["results"]
    )
    return 1 if failed else 0


def _cmd_geocode(args: argparse.Namespace) -> int:
    regions_config = load_regions_config()
    registry = RegionRegistry(regions_config)
    db = Database(default_database_path())
    store = GeocodeStore()
    try:
        db.verify_schema()
        requeued = 0
        if args.requeue_rejected and not args.dry_run:
            requeued = store.requeue_rejected()
        if args.enqueue_existing and not args.dry_run:
            for listing in db.iter_listings_missing_geo(limit=args.limit * 5):
                store.enqueue(listing)
        result = GeocodeWorker(
            db=db,
            store=store,
            regions=registry,
            rps=args.rps,
        ).run(limit=args.limit, dry_run=args.dry_run)
        result["requeued"] = requeued
    finally:
        store.close()
        db.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 2


def _cmd_score(args: argparse.Namespace) -> int:
    db = Database(default_database_path())
    try:
        db.verify_schema()
        report = score_database(
            db,
            source_id=args.source,
            limit=args.limit,
            pending_only=args.pending,
            dry_run=args.dry_run,
        )
    finally:
        db.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_geodata(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {}
    if args.part in {"all", "gtfs"}:
        report["gtfs"] = fetch_gtfs_stations(force=args.force)
    if args.part in {"all", "nature"}:
        report["nature"] = fetch_nature(force=args.force)
    if args.part in {"all", "poi"}:
        report["poi"] = fetch_poi(force=args.force)
    if args.part in {"all", "elevation"}:
        db = Database(default_database_path())
        try:
            points = [
                (float(row["lat"]), float(row["lng"]))
                for row in db.conn.execute(
                    """
                    SELECT lat, lng FROM listing
                    WHERE property_type IN ('house', 'plot')
                      AND lat IS NOT NULL AND lng IS NOT NULL
                    """
                ).fetchall()
            ]
        finally:
            db.close()
        report["elevation"] = fetch_elevation(points, rps=args.rps)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    report = audit_sources(limit=args.limit, rps=args.rps)
    summary = {
        "total": report["total"],
        "allowed": report["allowed"],
        "disallowed": report["disallowed"],
        "output": report["output"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_review_sources(args: argparse.Namespace) -> int:
    ids = [item.strip() for item in args.source.split(",")] if args.source else None
    report = review_sources(
        source_ids=ids,
        limit=args.limit,
        rps=args.rps,
        due_only=args.due,
        force=args.force,
    )
    changed = [
        entry
        for entry in report["sources"].values()
        if entry["changes"] not in ([], ["first observation"])
    ]
    for entry in changed:
        print(f"{entry['source_id']}: {'; '.join(entry['changes'])}")
    for entry in report["sources"].values():
        if entry["error"]:
            print(f"{entry['source_id']}: {entry['error']}")
    print(
        json.dumps(
            {
                "total": report["total"],
                "changed": report["changed"],
                "output": report["output"],
            },
            ensure_ascii=False,
        ),
    )
    return 0


def _cmd_dedupe(args: argparse.Namespace) -> int:
    if not args.backfill:
        raise ConfigError("dedupe requires --backfill")
    db = Database(default_database_path())
    try:
        db.verify_schema()
        updated = db.backfill_canonical_keys()
        stats = db.canonical_stats()
    finally:
        db.close()
    print(json.dumps({"updated": updated, **stats}, ensure_ascii=False, indent=2))
    return 0


def _cmd_rcn(args: argparse.Namespace) -> int:
    db = Database(default_database_path())
    try:
        db.verify_schema()
        if args.rcn_command == "status":
            print(
                json.dumps(RcnDatabase(db.conn).status(), ensure_ascii=False, indent=2, default=str)
            )
            return 0

        regions_config = load_regions_config()
        selected = _select_regions(regions_config, args.region)
        region_pairs = [
            (region_id, regions_config.regions[region_id].teryt) for region_id in selected
        ]
        config = load_rcn_config()
        lock_path = (
            Path(args.lock_file).expanduser()
            if args.lock_file
            else default_cache_dir() / "rcn.lock"
        )
        with file_lock(lock_path) as acquired:
            if not acquired:
                print("another RCN import is already active; skipping", file=sys.stderr)
                return 0
            if args.rcn_command == "sync":
                report = sync_datasets(db, regions=region_pairs, config=config, force=args.force)
            else:
                if args.limit is not None and args.limit < 1:
                    raise ValueError("--limit must be positive")
                kinds = (args.kind,) if args.kind else config.object_kinds
                report = []
                for region_id, region_teryt in region_pairs:
                    for kind in kinds:
                        report.append(
                            import_dataset(
                                db,
                                region_id=region_id,
                                region_teryt=region_teryt,
                                source_layer=kind,
                                config=config,
                                force=args.force,
                                dry_run=args.dry_run,
                                limit=args.limit,
                                refresh_comparables=False,
                            )
                        )
                if not args.dry_run and any(item["status"] == "ok" for item in report):
                    RcnDatabase(db.conn).refresh_comparables()
        print(json.dumps({"results": report}, ensure_ascii=False, indent=2, default=str))
        return 1 if any(item["status"] == "failed" for item in report) else 0
    finally:
        db.close()


def _cmd_verify_schema() -> int:
    db = Database(default_database_path())
    try:
        db.verify_schema()
        print(f"schema ok: {db.path}")
    finally:
        db.close()
    return 0


def _cmd_list_sources() -> int:
    catalog = load_sources_config()
    inventory = catalog.urls()
    counts = {status: 0 for status in ("implemented", "planned", "blocked", "excluded")}
    for source in catalog.sources.values():
        counts[source.status] = counts.get(source.status, 0) + 1

    print(
        f"inventory: {len(catalog.sources)} sources / {len(inventory)} URLs "
        f"(implemented {counts['implemented']}, planned {counts['planned']}, "
        f"blocked {counts['blocked']}, excluded {counts['excluded']})",
    )

    for source in sorted(
        catalog.sources.values(),
        key=lambda item: (item.tier, item.status != "implemented", item.id),
    ):
        if source.adapter is not None:
            state = "enabled" if source.enabled else "disabled"
            mode = "incremental" if source.incremental else "manual"
            refresh = f"{source.refresh_minutes}m" if source.refresh_minutes is not None else "-"
            print(
                f"- {source.id}: tier={source.tier} {source.status} {state} "
                f"adapter={source.adapter} rps={source.rps:g} "
                f"refresh={refresh} {mode} ({source.label})",
            )
        else:
            print(
                f"- {source.id}: tier={source.tier} {source.status} "
                f"kind={source.kind} ({source.label})",
            )
        if source.notes:
            print(f"  note: {source.notes}")
    return 0


def _cmd_llm(args: argparse.Namespace) -> int:
    if args.llm_command == "models":
        print(json.dumps(models_report(), ensure_ascii=False, indent=2))
        return 0
    if args.llm_command == "enrich":
        db = Database(default_database_path())
        try:
            db.verify_schema()
            report = enrich_listings(db, limit=args.limit, dry_run=args.dry_run)
        finally:
            db.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["failed"] == 0 else 2
    if args.llm_command == "rerank":
        db = Database(default_database_path())
        try:
            db.verify_schema()
            report = rerank_top(
                db,
                limit=args.limit,
                candidate_limit=args.candidates,
                dry_run=args.dry_run,
            )
        finally:
            db.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("recommendations") or report.get("dry_run") else 2

    report = check_models(limit=args.limit, timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["key_present"]:
        return 1
    return 0 if report["working"] else 2


def _select_source(catalog: SourcesConfig, source_id: str | None) -> SourceConfig:
    if source_id is not None:
        source = catalog.sources.get(source_id)
        if source is None:
            available = ", ".join(sorted(catalog.sources))
            raise ConfigError(f"unknown source '{source_id}'; available: {available}")
        return source

    enabled = catalog.enabled_adapters()
    if len(enabled) == 1:
        return enabled[0]
    available = ", ".join(source.id for source in enabled) or "(none)"
    raise ConfigError(f"--source is required; enabled sources: {available}")


def _adapter_name(source: SourceConfig) -> str:
    """The registered adapter name; sources without one cannot be run."""
    if source.adapter is None:
        raise ConfigError(f"source '{source.id}' has no adapter in config/sources.json")
    return source.adapter


def _full_pass_order(
    sources: list[SourceConfig],
    last_runs: dict[str, int | None],
    query_counts: dict[str, int],
) -> list[SourceConfig]:
    """Never-run first, then least recently attempted, then cheapest queries."""
    return sorted(
        sources,
        key=lambda source: (
            last_runs.get(source.id) is not None,
            last_runs.get(source.id) or 0,
            query_counts.get(source.id, 0),
            source.id,
        ),
    )


def _query_count(
    source: SourceConfig,
    registry: RegionRegistry,
    regions: tuple[str, ...],
) -> int:
    """Number of search queries a source will crawl; broken adapters run last."""
    try:
        return len(build_adapter(source, regions=registry).queries(regions))
    except Exception:
        return 10**6


def _select_regions(config: RegionsConfig, value: str | None) -> tuple[str, ...]:
    raw = value.split(",") if value else list(config.default)
    regions = tuple(item.strip() for item in raw if item.strip())
    unknown = [region for region in regions if region not in config.regions]
    if unknown:
        raise ConfigError(f"unknown regions: {unknown}")
    return regions


def _offer_type_filter(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("expected at least one transaction")
    unknown = [item for item in items if item not in OFFER_FILTERS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown transactions {unknown}; expected {list(OFFER_FILTERS)}",
        )
    return items


def _warn_limit_without_region(limit: int | None, region_ids: tuple[str, ...]) -> None:
    """A global limit starves the second voivodeship; queries run in scope order."""
    if limit is not None and len(region_ids) > 1:
        logger.warning(
            "--limit applies across %s in this order; run each --region as its own job "
            "to avoid leaving later voivodeships unscraped",
            ",".join(region_ids),
        )


def _lock_path(override: str | None) -> Path:
    """Return the lock path, allowing a per-job override for slow crawls.

    ``gdom`` runs for hours because of its 20-minute crawl delay; a shared lock
    would skip every watch cycle meanwhile, so the slow lane gets its own file.
    """
    raw = override or os.environ.get("PLASZCZAKI_LOCK_FILE")
    if raw:
        return Path(raw).expanduser()
    return default_cache_dir().parent / "scraper.lock"


@contextmanager
def file_lock(path: Path) -> Generator[bool]:
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

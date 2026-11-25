"""Tests for the watch scheduler. No network access is used."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from plaszczaki_scraper import watch
from plaszczaki_scraper.adapters.base import Query, SearchPage
from plaszczaki_scraper.config import (
    ConfigError,
    SourceConfig,
    SourcesConfig,
    load_regions_config,
    repo_root,
)
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.models import Listing, OfferType
from plaszczaki_scraper.regions import RegionRegistry

NOW = 1_700_000_000_000
MINUTE_MS = 60_000


def _database(tmp_path: Path) -> Database:
    migrations = sorted((repo_root() / "web" / "drizzle").glob("000[1-9]_*.sql"))
    assert migrations, "Drizzle migrations are missing"
    sql = "\n".join(migration.read_text(encoding="utf-8") for migration in migrations)
    path = tmp_path / "watch.db"
    connection = sqlite3.connect(path)
    connection.executescript(sql)
    connection.commit()
    connection.close()
    return Database(path)


def _regions() -> RegionRegistry:
    return RegionRegistry(load_regions_config())


def _source(
    source_id: str,
    *,
    enabled: bool = True,
    incremental: bool = True,
    refresh_minutes: int = 10,
) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        label=source_id,
        kind="portal",
        tier=1,
        status="implemented",
        enabled=enabled,
        urls=("https://example.test",),
        adapter="fake",
        incremental=incremental,
        refresh_minutes=refresh_minutes,
        incremental_pages=2,
    )


def _catalog(*sources: SourceConfig) -> SourcesConfig:
    return SourcesConfig(defaults_rps=1.0, sources={source.id: source for source in sources})


def _record_run(db: Database, source_id: str, started_at: int, mode: str = "incremental") -> None:
    db.conn.execute(
        """
        INSERT INTO scraper_run (source_id, mode, started_at, status, stats)
        VALUES (?, ?, ?, 'ok', '{}')
        """,
        (source_id, mode, started_at),
    )
    db.conn.commit()


class NoopAdapter:
    """Adapter without queries: never touches the network."""

    def __init__(self, source_id: str) -> None:
        self.id = source_id
        self.calls = 0

    def queries(self, regions: tuple[str, ...]) -> list[Query]:
        self.calls += 1
        return []

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        raise AssertionError("no search page should be parsed")

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        raise AssertionError("no detail page should be parsed")


def _patch_build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: frozenset[str] = frozenset(),
) -> list[str]:
    built: list[str] = []

    def fake_build(config: SourceConfig, *, regions: RegionRegistry) -> NoopAdapter:
        built.append(config.id)
        if config.id in fail:
            raise ValueError(f"adapter {config.id} is broken")
        return NoopAdapter(config.id)

    monkeypatch.setattr(watch, "build_adapter", fake_build)
    return built


def test_is_due_without_history() -> None:
    assert watch.is_due(_source("fake"), None, NOW) is True


def test_is_due_uses_poll_interval() -> None:
    source = _source("fake", refresh_minutes=10)
    interval = 10 * MINUTE_MS

    assert watch.is_due(source, NOW - interval, NOW) is True
    assert watch.is_due(source, NOW - interval + 1, NOW) is False
    # A timestamp ahead of ``now`` (clock skew) is not due.
    assert watch.is_due(source, NOW + 1, NOW) is False


def test_due_sources_filters_and_sorts_by_id(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        adapters = _catalog(
            _source("zeta-due"),
            _source("alpha-due"),
            _source("beta-fresh"),
            _source("gamma-disabled", enabled=False),
            _source("delta-full", incremental=False),
            _source("epsilon-other-mode"),
        )
        _record_run(db, "alpha-due", NOW - 11 * MINUTE_MS)
        _record_run(db, "beta-fresh", NOW - MINUTE_MS)
        _record_run(db, "epsilon-other-mode", NOW, mode="run")

        due = watch.due_sources(adapters, db, NOW)

        assert [source.id for source in due] == [
            "alpha-due",
            "epsilon-other-mode",
            "zeta-due",
        ]


def test_run_watch_once_runs_due_source_then_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _patch_build(monkeypatch)
    lock = tmp_path / "watch.lock"

    with _database(tmp_path) as db:
        adapters = _catalog(_source("fake", refresh_minutes=10))

        first = watch.run_watch_once(
            db=db,
            catalog=adapters,
            regions=_regions(),
            now_ms=NOW,
            lock_path=lock,
        )

        assert first["sources"] == 1
        result = first["results"][0]
        assert result["source_id"] == "fake"
        assert result["mode"] == "incremental"
        assert result["status"] == "ok"
        assert built == ["fake"]
        assert db.count_listings("fake") == 0
        source_row = db.conn.execute("SELECT adapter FROM source WHERE id = 'fake'").fetchone()
        assert source_row is not None
        assert source_row["adapter"] == "fake"

        last = db.last_run_at("fake")
        assert last is not None

        second = watch.run_watch_once(
            db=db,
            catalog=adapters,
            regions=_regions(),
            now_ms=last + 9 * MINUTE_MS,
            lock_path=lock,
        )
        assert built == ["fake"]
        assert second["results"][0]["status"] == "skipped"
        assert second["results"][0]["reason"] == "not due"

        forced = watch.run_watch_once(
            db=db,
            catalog=adapters,
            regions=_regions(),
            now_ms=last + 9 * MINUTE_MS,
            force=True,
            lock_path=lock,
        )
        assert built == ["fake", "fake"]
        assert forced["results"][0]["status"] == "ok"

        modes = [
            row["mode"]
            for row in db.conn.execute("SELECT mode FROM scraper_run ORDER BY id").fetchall()
        ]
        assert modes == ["incremental", "incremental"]


def test_run_watch_once_dry_run_writes_no_listings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _patch_build(monkeypatch)

    with _database(tmp_path) as db:
        report = watch.run_watch_once(
            db=db,
            catalog=_catalog(_source("fake")),
            regions=_regions(),
            dry_run=True,
            now_ms=NOW,
            lock_path=tmp_path / "watch.lock",
        )

        assert built == ["fake"]
        assert report["results"][0]["status"] == "ok"
        assert db.count_listings("fake") == 0
        assert db.last_run_at("fake") is None


def test_run_watch_once_isolates_source_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _patch_build(monkeypatch, fail=frozenset({"broken"}))

    with _database(tmp_path) as db:
        report = watch.run_watch_once(
            db=db,
            catalog=_catalog(_source("broken"), _source("fine")),
            regions=_regions(),
            now_ms=NOW,
            lock_path=tmp_path / "watch.lock",
        )

        results = {item["source_id"]: item for item in report["results"]}
        assert results["broken"]["status"] == "failed"
        assert "broken" in results["broken"]["error"]
        assert results["fine"]["status"] == "ok"
        assert built == ["broken", "fine"]
        assert db.last_run_at("broken") is None
        assert db.last_run_at("fine") is not None


def test_run_watch_once_skips_when_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _patch_build(monkeypatch)
    lock = tmp_path / "watch.lock"

    with _database(tmp_path) as db, watch.file_lock(lock) as acquired:
        assert acquired
        report = watch.run_watch_once(
            db=db,
            catalog=_catalog(_source("fake")),
            regions=_regions(),
            now_ms=NOW,
            lock_path=lock,
        )

    assert report["results"][0]["status"] == "skipped"
    assert report["results"][0]["reason"] == "locked"
    assert built == []


def test_run_watch_once_rejects_unknown_or_unwatchable_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build(monkeypatch)

    with _database(tmp_path) as db:
        with pytest.raises(ConfigError, match="unknown sources"):
            watch.run_watch_once(
                db=db,
                catalog=_catalog(_source("fake")),
                regions=_regions(),
                sources=["nope"],
                now_ms=NOW,
                lock_path=tmp_path / "watch.lock",
            )

        with pytest.raises(ConfigError, match=r"not enabled\+incremental"):
            watch.run_watch_once(
                db=db,
                catalog=_catalog(_source("full", incremental=False)),
                regions=_regions(),
                sources=["full"],
                now_ms=NOW,
                lock_path=tmp_path / "watch.lock",
            )


def test_watch_loop_stops_after_max_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr(watch.time, "sleep", sleeps.append)

    with _database(tmp_path) as db:
        reports = watch.watch_loop(
            db=db,
            catalog=_catalog(),
            regions=_regions(),
            interval=30,
            max_cycles=2,
            lock_path=tmp_path / "watch.lock",
        )
        empty = watch.watch_loop(
            db=db,
            catalog=_catalog(),
            regions=_regions(),
            max_cycles=0,
            lock_path=tmp_path / "watch.lock",
        )

    assert len(reports) == 2
    assert sleeps == [30]
    assert empty == []


def test_watch_loop_exits_cleanly_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build(monkeypatch)

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(watch.time, "sleep", interrupt)

    with _database(tmp_path) as db:
        reports = watch.watch_loop(
            db=db,
            catalog=_catalog(),
            regions=_regions(),
            interval=5,
            lock_path=tmp_path / "watch.lock",
        )

    assert len(reports) == 1


class LaneAdapter:
    """Queries per region so the lane planner can classify the source."""

    def __init__(self, per_region: dict[str, list[Query]]) -> None:
        self.per_region = per_region

    def queries(self, regions: tuple[str, ...]) -> list[Query]:
        return [query for region in regions for query in self.per_region.get(region, [])]


def _lane_query(region: str, offer: OfferType, suffix: str) -> Query:
    return Query(
        key=f"{region}:{offer}:{suffix}",
        url=f"https://example.test/{suffix}",
        region=region,
        offer_type=offer,
        property_type="flat",
    )


def _lane_catalog() -> SourcesConfig:
    return _catalog(
        _source("regional"),
        _source("national"),
        _source("auction"),
    )


def _lane_adapters() -> dict[str, LaneAdapter]:
    return {
        "regional": LaneAdapter(
            {
                "malopolskie": [
                    _lane_query("malopolskie", "sale", "mal-sale"),
                    _lane_query("malopolskie", "rent", "mal-rent"),
                ],
                "slaskie": [
                    _lane_query("slaskie", "sale", "sla-sale"),
                    _lane_query("slaskie", "rent", "sla-rent"),
                ],
            },
        ),
        "national": LaneAdapter(
            {
                "malopolskie": [_lane_query("malopolskie", "sale", "national")],
                "slaskie": [_lane_query("slaskie", "sale", "national")],
            },
        ),
        "auction": LaneAdapter(
            {
                "malopolskie": [_lane_query("malopolskie", "auction", "mal-auction")],
                "slaskie": [_lane_query("slaskie", "auction", "sla-auction")],
            },
        ),
    }


def _patch_lane_build(monkeypatch: pytest.MonkeyPatch) -> None:
    adapters = _lane_adapters()

    def fake_build(config: SourceConfig, *, regions: RegionRegistry) -> LaneAdapter:
        return adapters[config.id]

    monkeypatch.setattr(watch, "build_adapter", fake_build)


def test_plan_watch_lanes_splits_regions_types_and_national_feeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_lane_build(monkeypatch)

    plan = watch.plan_watch_lanes(_lane_catalog(), _regions())

    assert [lane["key"] for lane in plan] == [
        "malopolskie:sale",
        "slaskie:sale",
        "malopolskie:rent",
        "slaskie:rent",
        "other",
        "national",
    ]
    by_key = {lane["key"]: lane for lane in plan}
    assert by_key["malopolskie:sale"]["sources"] == ["regional"]
    assert by_key["slaskie:sale"]["sources"] == ["regional"]
    assert by_key["malopolskie:rent"]["sources"] == ["regional"]
    assert by_key["other"]["sources"] == ["auction"]
    assert by_key["other"]["offer_types"] == ("lease", "auction", "other")
    assert by_key["national"]["sources"] == ["national"]
    assert by_key["national"]["offer_types"] == ()


def test_run_watch_lanes_uses_one_pass_per_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_lane_build(monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_run_watch_once(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"sources": len(kwargs["sources"]), "results": []}

    monkeypatch.setattr(watch, "run_watch_once", fake_run_watch_once)

    with _database(tmp_path) as db:
        report = watch.run_watch_lanes(
            db=db,
            catalog=_lane_catalog(),
            regions=_regions(),
            now_ms=NOW,
        )

    assert report["lanes"] == 6
    assert [call["offer_types"] for call in calls] == [
        ("sale",),
        ("sale",),
        ("rent",),
        ("rent",),
        watch.SECONDARY_OFFER_TYPES,
        (),
    ]
    assert [tuple(call["region_ids"] or ()) for call in calls] == [
        ("malopolskie",),
        ("slaskie",),
        ("malopolskie",),
        ("slaskie",),
        ("malopolskie", "slaskie"),
        ("malopolskie", "slaskie"),
    ]
    assert {call["now_ms"] for call in calls} == {NOW}

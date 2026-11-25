from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from plaszczaki_scraper.adapters.base import Adapter, Query, SearchPage
from plaszczaki_scraper.adapters.registry import build_adapter
from plaszczaki_scraper.config import (
    AdapterConfig,
    AdaptersConfig,
    ConfigError,
    CoverageConfig,
    SourceCoverage,
    load_adapters_config,
    load_coverage_config,
    load_regions_config,
    repo_root,
)
from plaszczaki_scraper.coverage import (
    Cell,
    adapter_cells,
    collect_cells,
    filter_queries,
    matrix,
    missing_cells,
    render_cells,
    render_matrix,
    summarize,
    validate_coverage,
)
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.http import HttpClient
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry
from plaszczaki_scraper.runner import Runner, RunOptions
from plaszczaki_scraper.watch import (
    MINUTE_MS,
    WATCH_MODE,
    due_sources,
    watch_mode,
)


def test_every_adapter_has_coverage() -> None:
    adapters = load_adapters_config()
    coverage = load_coverage_config()

    missing = sorted(set(adapters.sources) - set(coverage.sources))
    extra = sorted(set(coverage.sources) - set(adapters.sources))
    assert missing == [], f"adapters without coverage: {missing}"
    assert extra == [], f"coverage entries without adapter: {extra}"


def test_declared_coverage_is_emitted_by_adapters() -> None:
    problems = validate_coverage(
        coverage=load_coverage_config(),
        adapters=load_adapters_config(),
        regions=load_regions_config(),
    )
    assert problems == []


def test_rent_and_sale_are_declared_for_the_big_portals() -> None:
    coverage = load_coverage_config()
    for source_id in ("otodom", "olx", "gratka", "morizon", "domiporta", "sprzedajemy"):
        offers = coverage.sources[source_id].offers
        assert "sale" in offers, f"{source_id} declares no sale coverage"
        assert "rent" in offers, f"{source_id} declares no rent coverage"


def test_srk_is_slaskie_only() -> None:
    coverage = load_coverage_config()
    assert coverage.sources["srk"].regions == ("slaskie",)


def test_adapter_cells_are_per_region() -> None:
    regions = RegionRegistry.load()
    adapter = build_adapter(load_adapters_config().sources["otodom"], regions=regions)

    cells = adapter_cells(adapter, ["slaskie"])

    assert cells[("slaskie", "rent", "flat")] == 1
    assert cells[("slaskie", "sale", "flat")] == 1
    assert not any(region == "malopolskie" for region, _, _ in cells)


def test_offer_type_filter() -> None:
    queries = [
        Query(key="a", url="https://a", region="slaskie", offer_type="sale", property_type="flat"),
        Query(key="b", url="https://b", region="slaskie", offer_type="rent", property_type="flat"),
        Query(key="c", url="https://c", region="slaskie", offer_type="lease", property_type="plot"),
    ]

    assert [q.key for q in filter_queries(queries, ())] == ["a", "b", "c"]
    assert [q.key for q in filter_queries(queries, ("rent",))] == ["b"]
    assert [q.key for q in filter_queries(queries, ("sale", "lease"))] == ["a", "c"]
    assert filter_queries(queries, ("auction",)) == []


def test_watch_mode_is_separate_per_transaction() -> None:
    assert watch_mode() == WATCH_MODE
    assert watch_mode(("rent",)) != watch_mode(("sale",))
    assert watch_mode(("rent", "lease")) == "incremental:lease,rent"
    assert watch_mode(("rent",), ("slaskie",)) == "incremental:rent:slaskie"
    assert watch_mode((), ("slaskie",)) != watch_mode((), ("malopolskie",))


def test_matrix_and_missing_cells() -> None:
    rows = [
        Cell("otodom", "malopolskie", "sale", "flat", True, 1, 10),
        Cell("otodom", "slaskie", "rent", "flat", True, 1, 0),
        Cell("otodom", "slaskie", "rent", "house", True, 0, 0),
        Cell("otodom", "slaskie", "sale", "flat", False, 1, 3),
    ]

    data = matrix(rows)
    assert data[0]["source_id"] == "otodom"
    assert data[1]["offers"]["rent"]["empty"] == 1
    assert data[1]["offers"]["sale"]["undeclared"] == 1

    text = render_matrix(rows)
    assert "otodom" in text
    assert "!" in text
    assert "+" in text

    cells = render_cells(rows)
    assert "empty" in cells
    assert "missing-query" in cells

    assert {cell.state for cell in missing_cells(rows)} == {
        "empty",
        "missing-query",
        "undeclared",
    }
    summary = summarize(rows)
    assert summary["cells"] == 4
    assert summary["ok"] == 1
    assert summary["missing_query"] == 1
    assert "missing-query" not in summary
    assert summary["stored"] == 13


def test_wildcard_does_not_double_count_concrete_cells() -> None:
    rows = [
        Cell("domiporta", "malopolskie", "sale", "flat", True, 1, 3),
        Cell("domiporta", "malopolskie", "sale", "house", True, 1, 2),
        Cell("domiporta", "malopolskie", "sale", "other", True, 1, 6),
    ]

    data = matrix(rows)
    assert data[0]["offers"]["sale"]["stored"] == 6
    assert summarize(rows)["stored"] == 6


def test_collect_cells_marks_rows_without_queries() -> None:
    coverage = CoverageConfig(
        defaults_regions=("slaskie",),
        sources={
            "otodom": SourceCoverage(
                id="otodom",
                regions=("slaskie",),
                offers={"rent": ("flat",), "sale": ("flat",)},
            ),
        },
    )
    adapters = AdaptersConfig(
        defaults_rps=1.0,
        sources={"otodom": load_adapters_config().sources["otodom"]},
    )

    rows = collect_cells(
        coverage=coverage,
        adapters=adapters,
        regions=["slaskie"],
        stored={("otodom", "slaskie", "sale", "flat"): 2},
    )

    by_cell = {(cell.offer_type, cell.property_type): cell for cell in rows}
    assert by_cell[("sale", "flat")].state == "ok"
    assert by_cell[("rent", "flat")].state == "empty"
    assert by_cell[("rent", "house")].state == "undeclared"


def test_aggregate_other_feed_counts_rows(tmp_path: Path) -> None:
    coverage = CoverageConfig(
        defaults_regions=("malopolskie",),
        sources={
            "amw-nieruchomosci": SourceCoverage(
                id="amw-nieruchomosci",
                regions=("malopolskie",),
                offers={"auction": ("other",)},
            ),
        },
    )
    adapters = AdaptersConfig(
        defaults_rps=1.0,
        sources={"amw-nieruchomosci": load_adapters_config().sources["amw-nieruchomosci"]},
    )

    rows = collect_cells(
        coverage=coverage,
        adapters=adapters,
        regions=["malopolskie"],
        stored={("amw-nieruchomosci", "malopolskie", "auction", "flat"): 4},
    )

    assert rows[0].state == "ok"
    assert rows[0].stored == 4


def test_coverage_config_rejects_unknown_offer_type(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "defaults": {"regions": ["slaskie"]},
                "sources": {"otodom": {"offers": {"barter": ["flat"]}}},
            },
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must be one of"):
        load_coverage_config(path)


def test_coverage_config_rejects_unknown_property_type(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "defaults": {"regions": ["slaskie"]},
                "sources": {"otodom": {"offers": {"sale": ["castle"]}}},
            },
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown property type"):
        load_coverage_config(path)


def test_coverage_config_accepts_region_override(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "defaults": {"regions": ["malopolskie", "slaskie"]},
                "sources": {
                    "srk": {"regions": ["slaskie"], "offers": {"sale": ["flat"]}},
                },
            },
        ),
        encoding="utf-8",
    )

    config = load_coverage_config(path)
    assert config.cells("srk") == [("slaskie", "sale", "flat")]


class _RecordingClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **_: object) -> object:
        self.urls.append(url)

        class _Response:
            status = 200
            html_content = "<html></html>"

        return _Response()


class _TwoTransactionAdapter:
    id = "fake"

    def queries(self, regions: tuple[str, ...]) -> list[Query]:
        return [
            Query(
                key="sale",
                url="https://example.test/sale",
                region="slaskie",
                offer_type="sale",
                property_type="flat",
            ),
            Query(
                key="rent",
                url="https://example.test/rent",
                region="slaskie",
                offer_type="rent",
                property_type="flat",
            ),
        ]

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        return SearchPage(items=[], current_page=query.page, total_pages=1)


def _database(tmp_path: Path) -> Database:
    migrations = sorted((repo_root() / "web" / "drizzle").glob("0*_*.sql"))
    sql = "\n".join(migration.read_text(encoding="utf-8") for migration in migrations)
    path = tmp_path / "test.db"
    connection = sqlite3.connect(path)
    connection.executescript(sql)
    connection.commit()
    connection.close()
    return Database(path)


def test_runner_only_fetches_selected_transactions(tmp_path: Path) -> None:
    client = _RecordingClient()
    with _database(tmp_path) as db:
        result = Runner(
            options=RunOptions(
                source_id="fake",
                regions=("slaskie",),
                offer_types=("rent",),
                dry_run=True,
            ),
            adapter=cast(Adapter, _TwoTransactionAdapter()),
            db=db,
            client=cast(HttpClient, client),
            regions=RegionRegistry(load_regions_config()),
        ).run()

    assert result["status"] == "ok"
    assert [url.split("?")[0] for url in client.urls] == ["https://example.test/rent"]


def test_count_by_axes_groups_stored_rows(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="fake",
            label="Fake",
            homepage_url="https://example.test",
            adapter="fake",
        )
        for external_id, region, offer_type, property_type in (
            ("1", "malopolskie", "sale", "flat"),
            ("2", "malopolskie", "sale", "flat"),
            ("3", "slaskie", "rent", "flat"),
        ):
            db.upsert_listing(
                Listing(
                    source_id="fake",
                    external_id=external_id,
                    url=f"https://example.test/{external_id}",
                    offer_type=offer_type,
                    property_type=property_type,
                    voivodeship=region,
                    lat=50.0,
                    lng=19.9,
                ),
            )

        counts = db.count_by_axes()

    assert counts[("fake", "malopolskie", "sale", "flat")] == 2
    assert counts[("fake", "slaskie", "rent", "flat")] == 1


class _StubRuns:
    def __init__(self, runs: dict[tuple[str, str], int]) -> None:
        self.runs = runs

    def last_run_at(self, source_id: str, mode: str = "incremental") -> int | None:
        return self.runs.get((source_id, mode))


def test_due_sources_uses_the_filtered_mode() -> None:
    source = AdapterConfig(
        id="fake",
        adapter="fake",
        label="Fake",
        homepage="https://example.test",
        incremental=True,
        poll_minutes=10,
    )
    adapters = AdaptersConfig(defaults_rps=1.0, sources={"fake": source})
    now = 2000 + 9 * MINUTE_MS
    db = cast(Database, _StubRuns({("fake", "incremental:rent:slaskie"): 2000}))

    assert due_sources(adapters, db, now, offer_types=("rent",), region_ids=("slaskie",)) == []
    assert due_sources(adapters, db, now, offer_types=("sale",), region_ids=("slaskie",)) == [
        source,
    ]
    assert due_sources(adapters, db, now) == [source]


def test_validate_coverage_flags_unknown_region() -> None:
    adapters = AdaptersConfig(
        defaults_rps=1.0,
        sources={"otodom": load_adapters_config().sources["otodom"]},
    )
    coverage = CoverageConfig(
        defaults_regions=("slask",),
        sources={
            "otodom": SourceCoverage(
                id="otodom",
                regions=("slask",),
                offers={"sale": ("flat",)},
            ),
        },
    )

    problems = validate_coverage(
        coverage=coverage,
        adapters=adapters,
        regions=load_regions_config(),
    )

    assert any("unknown region" in problem for problem in problems)

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from plaszczaki_scraper.adapters.base import Adapter, Query, SearchPage
from plaszczaki_scraper.config import AdapterConfig, load_regions_config, repo_root
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.http import HttpClient
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry
from plaszczaki_scraper.runner import Runner, RunOptions


class FakeResponse:
    status = 200
    html_content = "<html></html>"


class FakeClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **_: Any) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse()


class FakeAdapter:
    id = "fake"

    def __init__(self) -> None:
        self.detail_calls: list[str] = []

    def queries(self, regions: tuple[str, ...]) -> list[Query]:
        return [
            Query(
                key="malopolskie:test",
                url="https://example.test/search",
                region="malopolskie",
                offer_type="sale",
                property_type="flat",
            ),
        ]

    def with_page(self, query: Query, page: int) -> Query:
        return query.with_page(page)

    def parse_search(self, html: str, query: Query) -> SearchPage:
        page = query.page
        ids = {
            1: ["1", "2", "3"],
            2: ["4", "5"],
        }.get(page, [])
        return SearchPage(
            items=[self._summary(external_id) for external_id in ids],
            current_page=page,
            total_pages=5,
        )

    def parse_detail(self, html: str, summary: Listing) -> Listing:
        self.detail_calls.append(summary.external_id)
        return replace(
            summary,
            description=f"description {summary.external_id}",
            lat=50.06,
            lng=19.94,
            geo_source="source",
        )

    def _summary(self, external_id: str) -> Listing:
        return Listing(
            source_id=self.id,
            external_id=external_id,
            url=f"https://example.test/{external_id}",
            title=f"Listing {external_id}",
            offer_type="sale",
            property_type="flat",
            voivodeship="malopolskie",
        )


def _database(tmp_path: Path) -> Database:
    migrations = sorted((repo_root() / "web" / "drizzle").glob("000[1-9]_*.sql"))
    sql = "\n".join(migration.read_text(encoding="utf-8") for migration in migrations)
    path = tmp_path / "test.db"
    connection = sqlite3.connect(path)
    connection.executescript(sql)
    connection.commit()
    connection.close()
    return Database(path)


def _source_config() -> AdapterConfig:
    return AdapterConfig(
        id="fake",
        adapter="fake",
        label="Fake",
        homepage="https://example.test",
        incremental=True,
        newest_param="sort",
        newest_value="newest",
        incremental_pages=2,
    )


def test_incremental_reads_two_newest_pages_and_skips_known(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    client = FakeClient()
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="fake",
            label="Fake",
            homepage_url="https://example.test",
            adapter="fake",
        )
        db.upsert_listing(
            Listing(
                source_id="fake",
                external_id="2",
                url="https://example.test/2",
                offer_type="sale",
                property_type="flat",
                voivodeship="malopolskie",
                lat=50.0,
                lng=19.9,
            ),
        )

        result = Runner(
            options=RunOptions(
                source_id="fake",
                mode="incremental",
                newest_only=True,
                incremental_pages=2,
            ),
            adapter=cast(Adapter, adapter),
            db=db,
            client=cast(HttpClient, client),
            regions=RegionRegistry(load_regions_config()),
            source_config=_source_config(),
        ).run()

        assert result["status"] == "ok"
        assert result["pages_scanned"] == 2
        assert result["known"] == 1
        assert adapter.detail_calls == ["1", "3", "4", "5"]
        search_urls = [url for url in client.urls if "/search" in url]
        assert len(search_urls) == 2

        first = urlsplit(search_urls[0])
        second = urlsplit(search_urls[1])
        assert parse_qs(first.query)["sort"] == ["newest"]
        assert parse_qs(first.query)["page"] == ["1"]
        assert parse_qs(second.query)["page"] == ["2"]
        assert parse_qs(second.query)["sort"] == ["newest"]

        assert db.count_listings("fake") == 5
        checkpoints = db.conn.execute("SELECT COUNT(*) AS count FROM scraper_checkpoint")
        assert checkpoints.fetchone()["count"] == 0

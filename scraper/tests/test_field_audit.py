from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from plaszczaki_scraper import backfill as backfill_module
from plaszczaki_scraper.backfill import backfill_attributes, rehydrate_missing_districts
from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.field_audit import audit_fields
from plaszczaki_scraper.models import Listing


def _database(tmp_path: Path) -> Database:
    migrations = sorted((repo_root() / "web" / "drizzle").glob("000[1-9]_*.sql"))
    assert migrations
    path = tmp_path / "audit.db"
    connection = sqlite3.connect(path)
    connection.executescript("\n".join(item.read_text(encoding="utf-8") for item in migrations))
    columns = {row[1] for row in connection.execute("PRAGMA table_info(listing)").fetchall()}
    for name, declaration in (
        ("score_signals", "text"),
        ("score_signals_version", "text"),
        ("score_signals_at", "integer"),
    ):
        if name not in columns:
            connection.execute(f"ALTER TABLE listing ADD COLUMN {name} {declaration}")
    connection.commit()
    connection.close()
    db = Database(path)
    db.upsert_source(
        source_id="otodom",
        label="Otodom",
        homepage_url="https://www.otodom.pl",
        adapter="otodom",
    )
    return db


def _listing(external_id: str, *, district: str | None = "Stare Miasto") -> Listing:
    return Listing(
        source_id="otodom",
        external_id=external_id,
        url=f"https://example.test/{external_id}",
        offer_type="rent",
        property_type="flat",
        price_pln=3000,
        rent_pln=3000,
        area_m2=50,
        rooms=2,
        city="Kraków",
        district=district,
        extra={
            "otodom": {
                "characteristics": {
                    "heating": {"value": "urban"},
                    "building_type": {"value": "block"},
                    "rent": {"value": "700"},
                    "deposit": {"value": "3000"},
                    "free_from": {"value": "2026-12-01"},
                },
                "target": {"MarketType": "secondary"},
            }
        },
    )


def test_field_audit_reports_applicable_coverage_and_extra_keys(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_listings([_listing("1"), _listing("2", district=None)])

        report = audit_fields(db)

        assert report["total"] == 2
        source = report["sources"][0]
        assert source["source_id"] == "otodom"
        assert source["fields"]["administrative_rent_pln"]["filled"] == 2
        assert source["fields"]["deposit_pln"]["filled"] == 2
        assert source["fields"]["district"] == {
            "group": "core",
            "impact": 4,
            "applicable": 2,
            "filled": 1,
            "missing": 1,
            "coverage": 0.5,
        }
        assert any(
            entry["path"] == "otodom.characteristics.heating"
            for entry in report["extra_keys"]["otodom"]["keys"]
        )
        assert Path(report["output"]).exists()


def test_attribute_backfill_is_idempotent_and_supports_dry_run(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_listing(_listing("1"))
        with db.conn:
            db.conn.execute(
                """
                UPDATE listing
                SET condition = NULL, building_type = NULL, heating = NULL,
                    administrative_rent_pln = NULL, deposit_pln = NULL,
                    available_from = NULL, market = 'unknown', price_per_m2_pln = NULL,
                    rent_pln = NULL
                """
            )

        dry_run = backfill_attributes(db, dry_run=True)
        assert dry_run["processed"] == 1
        assert dry_run["updated"] == 1
        row = db.conn.execute("SELECT heating FROM listing").fetchone()
        assert row["heating"] is None

        applied = backfill_attributes(db)
        assert applied["updated"] == 1
        row = db.conn.execute(
            """
            SELECT heating, administrative_rent_pln, deposit_pln, market,
                   price_per_m2_pln, available_from, rent_pln
            FROM listing
            """
        ).fetchone()
        assert row["heating"] == "district"
        assert row["administrative_rent_pln"] == 700
        assert row["deposit_pln"] == 3000
        assert row["market"] == "secondary"
        assert row["price_per_m2_pln"] == 60
        assert row["available_from"] is not None
        assert row["rent_pln"] == 3000

        repeated = backfill_attributes(db)
        assert repeated["updated"] == 0
        assert repeated["unchanged"] == 1


def test_district_rehydration_parses_and_persists_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Adapter:
        def parse_detail(self, html: str, summary: Listing) -> Listing:
            return replace(
                summary,
                district="Stare Miasto",
                extra={
                    **summary.extra,
                    "otodom": {"location_levels": {"district": "Stare Miasto"}},
                },
            )

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def get(self, url: str) -> SimpleNamespace:
            return SimpleNamespace(status=200, html_content="<html></html>")

        def close(self) -> None:
            pass

    monkeypatch.setattr(backfill_module, "build_adapter", lambda *args, **kwargs: _Adapter())
    monkeypatch.setattr(backfill_module, "HttpClient", _Client)

    with _database(tmp_path) as db:
        db.upsert_listing(_listing("1", district=None))

        report = rehydrate_missing_districts(db, source_id="otodom")

        assert report["attempted"] == 1
        assert report["district_filled"] == 1
        assert report["updated"] == 1
        assert report["failed"] == 0
        row = db.conn.execute("SELECT district FROM listing").fetchone()
        assert row["district"] == "Stare Miasto"


def test_district_rehydration_skips_rows_that_already_have_district(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_listing(_listing("1"))

        report = rehydrate_missing_districts(db, source_id="otodom")

        assert report["attempted"] == 0
        assert report["district_filled"] == 0
        assert report["failed"] == 0

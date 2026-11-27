from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from plaszczaki_scraper import signals
from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.db import Database, SchemaError, UpsertResult
from plaszczaki_scraper.geodata import GeoContext
from plaszczaki_scraper.models import Listing


def _migration_sql() -> str:
    migrations = sorted((repo_root() / "web" / "drizzle").glob("000[1-9]_*.sql"))
    assert migrations, "Drizzle listing migrations are missing"
    return "\n".join(migration.read_text(encoding="utf-8") for migration in migrations)


def _database(tmp_path: Path) -> Database:
    path = tmp_path / "test.db"
    connection = sqlite3.connect(path)
    connection.executescript(_migration_sql())
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
    return Database(path)


def _listing(*, price: int | None = 500_000, external_id: str = "123") -> Listing:
    return Listing(
        source_id="otodom",
        external_id=external_id,
        url=f"https://example.test/{external_id}",
        title="Test flat",
        offer_type="sale",
        property_type="flat",
        price_pln=price,
        area_m2=50.0,
        rooms=2,
        voivodeship="malopolskie",
        city="Kraków",
        lat=50.06,
        lng=19.94,
    )


def test_schema_and_upsert_round_trip(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.verify_schema()
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )

        assert db.upsert_listing(_listing()) is UpsertResult.INSERTED
        assert db.count_listings("otodom") == 1
        row = db.conn.execute("SELECT first_seen_at FROM listing").fetchone()
        first_seen = row["first_seen_at"] if row is not None else None
        assert first_seen is not None

        assert db.upsert_listing(_listing()) is UpsertResult.UNCHANGED
        row = db.conn.execute("SELECT first_seen_at FROM listing").fetchone()
        assert row is not None
        assert row["first_seen_at"] == first_seen

        updated = _listing(price=480_000)
        assert db.upsert_listing(updated) is UpsertResult.UPDATED
        snapshots = db.conn.execute("SELECT * FROM listing_snapshot").fetchall()
        assert len(snapshots) == 1
        assert snapshots[0]["price_pln"] == 480_000
        assert "price_pln" in snapshots[0]["changed"]


def test_batch_upsert(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        counts = db.upsert_listings(
            [_listing(external_id="1"), _listing(external_id="2")],
        )
        assert counts == {"inserted": 2, "updated": 0, "unchanged": 0}
        assert db.count_listings("otodom") == 2

        counts = db.upsert_listings(
            [_listing(external_id="1"), _listing(external_id="3")],
        )
        assert counts == {"inserted": 1, "updated": 0, "unchanged": 1}


def test_start_run_interrupts_stale_runs(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.conn.execute(
            """
            INSERT INTO scraper_run (source_id, mode, started_at, status, stats)
            VALUES ('otodom', 'run', 1, 'running', '{}')
            """,
        )
        db.conn.commit()

        run_id = db.start_run(source_id="otodom", mode="run")
        old = db.conn.execute(
            "SELECT status FROM scraper_run WHERE id = 1",
        ).fetchone()
        assert old is not None
        assert old["status"] == "interrupted"
        assert run_id != 1


def test_missing_schema_is_reported(tmp_path: Path) -> None:
    with (
        Database(tmp_path / "empty.db") as db,
        pytest.raises(
            SchemaError,
            match="bun run db:migrate",
        ),
    ):
        db.verify_schema()


def test_existing_external_ids(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        db.upsert_listings(
            [_listing(external_id="1"), _listing(external_id="2")],
        )

        assert db.existing_external_ids("otodom", ["1", "2", "3"]) == {"1", "2"}
        assert db.existing_external_ids("other", ["1"]) == set()
        assert db.existing_external_ids("otodom", []) == set()


def test_iter_listings_missing_geo(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        no_geo = _listing(external_id="no-geo")
        no_geo.lat = None
        no_geo.lng = None
        db.upsert_listings([_listing(external_id="has-geo"), no_geo])

        missing = list(db.iter_listings_missing_geo())
        assert [listing.external_id for listing in missing] == ["no-geo"]


def test_canonical_keys_are_set_and_backfilled(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        for source_id in ("otodom", "gratka"):
            db.upsert_source(
                source_id=source_id,
                label=source_id,
                homepage_url=f"https://{source_id}.example",
                adapter=source_id,
            )
        db.upsert_listing(_listing(external_id="1"))
        duplicate = _listing(external_id="2")
        duplicate.source_id = "gratka"
        db.upsert_listing(duplicate)

        keys = {
            row["canonical_key"]
            for row in db.conn.execute("SELECT canonical_key FROM listing").fetchall()
        }
        assert keys == {"geo:50.0600:19.9400:50:2:sale:flat"}

        stats = db.canonical_stats()
        assert stats["rows"] == 2
        assert stats["keyed"] == 2
        assert stats["clusters"] == 1
        assert stats["duplicate_rows"] == 1

        with db.conn:
            db.conn.execute("UPDATE listing SET canonical_key = NULL")
        assert db.backfill_canonical_keys() == 2


def test_pending_signal_rows_include_older_versions(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        db.upsert_listing(_listing())
        row = db.conn.execute("SELECT id FROM listing").fetchone()
        assert row is not None
        db.update_signals([("{}", "signals-v1", 1, row["id"])])

        assert list(db.iter_signal_rows(pending_only=True)) == []
        pending = list(db.iter_signal_rows(pending_only=True, signals_version="signals-v2"))
        assert [item["id"] for item in pending] == [row["id"]]


def test_signal_writes_leave_legacy_score_columns_untouched(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        db.upsert_listing(_listing())
        row = db.conn.execute("SELECT id FROM listing").fetchone()
        assert row is not None
        with db.conn:
            db.conn.execute(
                """
                UPDATE listing
                SET score = 77, score_profile = 'legacy', scores = '{}',
                    score_reasons = '[]', score_breakdown = '{}',
                    scored_at = 10, score_version = 'legacy'
                WHERE id = ?
                """,
                (row["id"],),
            )
        db.update_signals([('{"version":"signals-v1"}', "signals-v1", 20, row["id"])])
        stored = db.conn.execute(
            """
            SELECT score, score_profile, scores, score_reasons, score_breakdown,
                   scored_at, score_version, score_signals, score_signals_version,
                   score_signals_at
            FROM listing
            """
        ).fetchone()
        assert stored is not None
        assert stored["score"] == 77
        assert stored["score_profile"] == "legacy"
        assert stored["scores"] == "{}"
        assert stored["score_reasons"] == "[]"
        assert stored["score_breakdown"] == "{}"
        assert stored["scored_at"] == 10
        assert stored["score_version"] == "legacy"
        assert stored["score_signals_version"] == "signals-v1"
        assert stored["score_signals_at"] == 20

        assert db.invalidate_signals([row["id"]]) == 1
        assert db.conn.execute("SELECT score_signals_at FROM listing").fetchone()[0] is None


def test_listing_updates_invalidate_signals(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        db.upsert_listing(_listing())
        row = db.conn.execute("SELECT id FROM listing").fetchone()
        assert row is not None
        db.update_signals([("{}", "signals-v1", 20, row["id"])])
        assert db.upsert_listing(_listing(price=450_000)) is UpsertResult.UPDATED
        assert db.conn.execute("SELECT score_signals_at FROM listing").fetchone()[0] is None


def test_extract_signals_database_supports_pending_limit_source_and_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signals.GeoContext, "load", classmethod(lambda cls: GeoContext()))
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        db.upsert_listings([_listing(external_id="1"), _listing(external_id="2", price=450_000)])

        dry_run = signals.extract_signals_database(db, limit=1, dry_run=True)
        assert dry_run["processed"] == 1
        assert dry_run["updated"] == 0
        assert db.conn.execute("SELECT count(score_signals) FROM listing").fetchone()[0] == 0

        first = signals.extract_signals_database(db, limit=1)
        assert first["processed"] == 1
        assert first["updated"] == 1
        rows = db.conn.execute(
            "SELECT score_signals, score_signals_version, score_signals_at FROM listing ORDER BY id"
        ).fetchall()
        payload = json.loads(rows[0]["score_signals"])
        assert payload["version"] == "signals-v2"
        assert set(payload["profiles"]) == {"kamienica", "nowe_mieszkanie"}
        assert all(
            value is not None
            for profile in payload["profiles"].values()
            for group in ("signals", "penalties")
            for value in profile[group].values()
        )
        assert rows[0]["score_signals_version"] == "signals-v2"
        assert rows[0]["score_signals_at"] is not None
        assert rows[1]["score_signals"] is None

        pending = signals.extract_signals_database(db, pending_only=True)
        assert pending["processed"] == 1
        assert pending["updated"] == 1
        assert signals.extract_signals_database(db, pending_only=True)["processed"] == 0

        assert signals.extract_signals_database(db, source_id="other")["processed"] == 0
        assert signals.extract_signals_database(db, pending_only=False, limit=1)["processed"] == 1


def test_update_listing_geo_sets_canonical_key(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        no_geo = _listing()
        no_geo.lat = None
        no_geo.lng = None
        db.upsert_listing(no_geo)

        row = db.conn.execute("SELECT canonical_key FROM listing").fetchone()
        assert row is not None
        before = row["canonical_key"]

        assert db.update_listing_geo(
            source_id="otodom",
            external_id="123",
            lat=50.1,
            lng=19.9,
            geo_source="geocoded_address",
            canonical_key="geo:50.1000:19.9000:50:2:sale:flat",
        )

        row = db.conn.execute("SELECT canonical_key FROM listing").fetchone()
        assert row is not None
        assert row["canonical_key"] == "geo:50.1000:19.9000:50:2:sale:flat"
        assert row["canonical_key"] != before


def test_update_listing_geo_keeps_canonical_key_by_default(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.upsert_source(
            source_id="otodom",
            label="Otodom",
            homepage_url="https://www.otodom.pl",
            adapter="otodom",
        )
        no_geo = _listing()
        no_geo.lat = None
        no_geo.lng = None
        db.upsert_listing(no_geo)
        row = db.conn.execute("SELECT canonical_key FROM listing").fetchone()
        assert row is not None
        before = row["canonical_key"]

        assert db.update_listing_geo(
            source_id="otodom",
            external_id="123",
            lat=50.1,
            lng=19.9,
            geo_source="geocoded_address",
        )

        row = db.conn.execute("SELECT canonical_key FROM listing").fetchone()
        assert row is not None
        assert row["canonical_key"] == before

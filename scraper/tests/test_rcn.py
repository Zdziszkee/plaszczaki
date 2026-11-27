from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.geodata import GeoContext
from plaszczaki_scraper.rcn.config import load_rcn_config
from plaszczaki_scraper.rcn.normalize import is_residential_building, normalize_row
from plaszczaki_scraper.rcn.rcn_db import RcnDatabase
from plaszczaki_scraper.scoring import load_scoring_config, score_database


def _migration_sql() -> str:
    migrations = sorted((repo_root() / "web" / "drizzle").glob("*.sql"))
    assert migrations
    return "\n".join(migration.read_text(encoding="utf-8") for migration in migrations)


def _database(tmp_path: Path) -> Database:
    path = tmp_path / "test.db"
    connection = sqlite3.connect(path)
    connection.executescript(_migration_sql())
    connection.commit()
    connection.close()
    return Database(path)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "gid": 10,
        "TERYT": "1261",
        "TRAN_PRZESTRZEN_NAZW": "PL.PZGiK.4094.RCN",
        "TRAN_LOKALNY_ID_IIP": "transaction-1",
        "TRAN_WERSJA_ID": "2026-01-02T10:00:00Z",
        "TRAN_OZNACZENIE_TRANS": "transaction-1",
        "TRAN_RODZAJ_TRANS": "wolnyRynek",
        "TRAN_RODZAJ_RYNKU": "rynekWtorny",
        "TRAN_SPRZEDAJACY": "osobaFizyczna",
        "TRAN_KUPUJACY": "osobaFizyczna",
        "TRAN_CENA_BRUTTO": 500000,
        "DOK_OZNACZENIE": "Rep. A 1/2026",
        "DOK_DATA": "2026-01-01",
        "NIER_RODZAJ": "nieruchomoscLokalowa",
        "NIER_PRAWO": "wlasnoscLokaluWrazZPrawemZwiazanym",
        "NIER_UDZIAL": "1/1",
        "NIER_CENA_BRUTTO": 500000,
        "LOK_ID_LOKALU": "126105_9.0145.124/1.1_BUD.9_LOK",
        "LOK_FUNKCJA": "mieszkalna",
        "LOK_LICZBA_IZB": 2,
        "LOK_NR_KOND": 2,
        "LOK_POW_UZYT": 50,
        "LOK_CENA_BRUTTO": None,
        "LOK_ADRES": "MSC:KRAKÓW;UL:PLAC JULIUSZA KOSSAKA;NR_PORZ:5",
        "lat": 50.06,
        "lng": 19.94,
    }
    row.update(overrides)
    return row


def _normalize(row: dict[str, object], *, layer: str = "lokale"):
    result = normalize_row(
        row,
        region_id="malopolskie",
        region_teryt="12",
        source_layer=layer,
        source_url="https://example.test/rcn.parquet",
        grid_degrees=0.02,
        now_ms=1,
    )
    assert result is not None
    return result


def test_normalize_rcn_flat() -> None:
    transaction, obj = _normalize(_row())

    assert transaction["transaction_key"] == "1261|PL.PZGiK.4094.RCN|transaction-1"
    assert transaction["market"] == "secondary"
    assert transaction["share_fraction"] == 1
    assert transaction["document_date"] == "2026-01-01"
    assert obj["object_kind"] == "flat"
    assert obj["city"] == "KRAKÓW"
    assert obj["street"] == "PLAC JULIUSZA KOSSAKA 5"
    assert obj["geo_cell_lat"] == pytest.approx(50.06)
    assert obj["geo_cell_lng"] == pytest.approx(19.94)


def test_normalize_projects_epsg2180_geometry_with_pyproj() -> None:
    transaction, obj = _normalize(
        _row(
            lat=None,
            lng=None,
            source_x=567046.3950674619,
            source_y=244169.06814420596,
            source_crs="EPSG:2180",
        )
    )

    assert transaction["transaction_key"]
    assert obj["lat"] == pytest.approx(50.061)
    assert obj["lng"] == pytest.approx(19.937)


def test_normalize_rejects_coordinates_outside_poland() -> None:
    _, obj = _normalize(
        _row(
            lat=None,
            lng=None,
            source_x=541055.6433859246,
            source_y=268685.6235578619,
            source_crs="OGC:CRS84",
        )
    )

    assert obj["lat"] is None
    assert obj["lng"] is None


def test_normalize_share_and_non_residential_premises() -> None:
    transaction, obj = _normalize(
        _row(NIER_UDZIAL="1/2", LOK_FUNKCJA="garaz", LOK_ID_LOKALU="garage-1")
    )

    assert transaction["share_fraction"] == 0.5
    assert obj["object_kind"] == "premises"


def test_normalize_plot_and_building_codes() -> None:
    plot_transaction, plot = _normalize(
        _row(
            DZI_ID_DZIALKI="126105_9.0145",
            DZI_NR_DZIALKI="124",
            DZI_POW_EWID=1200,
            DZI_CENA_BRUTTO=300000,
            DZI_ADRES="MSC:KRAKÓW",
        ),
        layer="dzialki",
    )
    building_transaction, building = _normalize(
        _row(
            BUD_ID_BUDYNKU="building-1",
            BUD_RODZAJ="budynekMieszkalnyJednorodzinny",
            BUD_POW_UZYT=140,
            BUD_CENA_BRUTTO=900000,
        ),
        layer="budynki",
    )

    assert plot_transaction["transaction_key"] == building_transaction["transaction_key"]
    assert plot["object_kind"] == "plot"
    assert plot["plot_area_m2"] == 1200
    assert building["object_kind"] == "building"
    assert is_residential_building(building["building_type_raw"])


def test_normalize_missing_identity_is_stable() -> None:
    row = _row(TRAN_LOKALNY_ID_IIP=None, TRAN_PRZESTRZEN_NAZW=None)
    first_transaction, first_obj = _normalize(row)
    second_transaction, second_obj = _normalize(row)

    assert first_transaction["transaction_key"] == second_transaction["transaction_key"]
    assert first_obj["object_key"] == second_obj["object_key"]
    assert first_transaction["local_id"].startswith("fallback:")


def test_missing_object_iip_does_not_use_volatile_gid_as_identity() -> None:
    first = _row(gid=1, LOK_ID_LOKALU=None)
    second = _row(gid=2, LOK_ID_LOKALU=None)

    _, first_obj = _normalize(first)
    _, second_obj = _normalize(second)

    assert first_obj["object_key"] == second_obj["object_key"]
    assert first_obj["source_gid"] == 1
    assert second_obj["source_gid"] == 2


def test_rcn_database_upsert_comparables_and_reconcile(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        rcn = RcnDatabase(db.conn)
        import_id = rcn.start_import(
            region_id="malopolskie",
            object_kind="lokale",
            source_url="https://example.test/rcn.parquet",
            etag="etag-1",
            last_modified=None,
        )
        transaction, obj = _normalize(_row())
        obj["_transaction_key"] = transaction["transaction_key"]
        rcn.upsert_transactions([transaction])
        ids = rcn.transaction_ids([transaction["transaction_key"]])
        assert rcn.upsert_objects([obj], transaction_ids=ids, import_id=import_id) == 1
        rcn.refresh_comparables()

        row = db.conn.execute(
            "SELECT comparable_price_pln, comparable_area_m2, price_per_m2_pln, exclusion_reason "
            "FROM rcn_transaction_object"
        ).fetchone()
        assert row is not None
        assert row["comparable_price_pln"] == 500000
        assert row["comparable_area_m2"] == 50
        assert row["price_per_m2_pln"] == 10000
        assert row["exclusion_reason"] is None

        rcn.finish_import(
            import_id,
            status="ok",
            source_rows=1,
            imported_rows=1,
            skipped_rows=0,
        )
        assert rcn.latest_successful_import(region_id="malopolskie", object_kind="lokale")

        stale_transaction, stale_obj = _normalize(_row(TRAN_LOKALNY_ID_IIP="stale"))
        stale_obj["_transaction_key"] = stale_transaction["transaction_key"]
        rcn.upsert_transactions([stale_transaction])
        stale_ids = rcn.transaction_ids([stale_transaction["transaction_key"]])
        rcn.upsert_objects([stale_obj], transaction_ids=stale_ids, import_id=import_id - 1)
        assert (
            rcn.reconcile(region_id="malopolskie", source_layer="lokale", import_id=import_id) == 1
        )
        assert db.conn.execute("SELECT count(*) FROM rcn_transaction_object").fetchone()[0] == 1
        assert db.conn.execute("SELECT count(*) FROM rcn_transaction").fetchone()[0] == 1


def test_score_rows_use_only_the_latest_successful_rcn_snapshot(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        rcn = RcnDatabase(db.conn)
        successful = rcn.start_import(
            region_id="malopolskie",
            object_kind="lokale",
            source_url="https://example.test/successful.parquet",
            etag=None,
            last_modified=None,
        )
        failed = rcn.start_import(
            region_id="malopolskie",
            object_kind="lokale",
            source_url="https://example.test/failed.parquet",
            etag=None,
            last_modified=None,
        )
        old_transaction, old_object = _normalize(
            _row(TRAN_LOKALNY_ID_IIP="successful", LOK_ID_LOKALU="successful-flat")
        )
        failed_transaction, failed_object = _normalize(
            _row(TRAN_LOKALNY_ID_IIP="failed", LOK_ID_LOKALU="failed-flat")
        )
        old_object["_transaction_key"] = old_transaction["transaction_key"]
        failed_object["_transaction_key"] = failed_transaction["transaction_key"]
        rcn.upsert_transactions([old_transaction, failed_transaction])
        ids = rcn.transaction_ids(
            [old_transaction["transaction_key"], failed_transaction["transaction_key"]]
        )
        rcn.upsert_objects([old_object], transaction_ids=ids, import_id=successful)
        rcn.upsert_objects([failed_object], transaction_ids=ids, import_id=failed)
        rcn.refresh_comparables()
        rcn.finish_import(
            successful,
            status="ok",
            source_rows=1,
            imported_rows=1,
            skipped_rows=0,
        )
        rcn.finish_import(
            failed,
            status="failed",
            source_rows=1,
            imported_rows=1,
            skipped_rows=0,
            error="failed after upsert",
        )

        rows = list(
            db.iter_rcn_score_rows(
                document_date_cutoff="2000-01-01",
                regions={"malopolskie"},
                object_kinds={"flat"},
            )
        )
        assert [row["key"] for row in rows] == [old_object["object_key"]]


def test_score_database_loads_successful_rcn_transactions(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        db.conn.execute(
            "INSERT INTO source (id, label, homepage_url, adapter, enabled, created_at) "
            "VALUES ('test', 'Test', 'https://example.test', 'test', 1, 1)"
        )
        db.conn.execute(
            """
            INSERT INTO listing (
                source_id, external_id, url, offer_type, property_type, market,
                price_pln, price_per_m2_pln, area_m2, rooms, voivodeship, city,
                lat, lng, images, extra, canonical_key, first_seen_at, last_seen_at
            ) VALUES (
                'test', '1', 'https://example.test/offer', 'sale', 'flat', 'secondary',
                600000, 15000, 40, 2, 'malopolskie', 'Kraków',
                50.06, 19.94, '[]', '{}', 'target', 1, 1
            )
            """
        )
        rcn = RcnDatabase(db.conn)
        import_id = rcn.start_import(
            region_id="malopolskie",
            object_kind="lokale",
            source_url="https://example.test/rcn.parquet",
            etag=None,
            last_modified=None,
        )
        transactions = []
        objects = []
        for index in range(5):
            transaction, obj = _normalize(
                _row(
                    TRAN_LOKALNY_ID_IIP=f"transaction-{index}",
                    LOK_ID_LOKALU=f"flat-{index}",
                    LOK_POW_UZYT=40,
                )
            )
            obj["_transaction_key"] = transaction["transaction_key"]
            transactions.append(transaction)
            objects.append(obj)
        rcn.upsert_transactions(transactions)
        ids = rcn.transaction_ids([item["transaction_key"] for item in transactions])
        rcn.upsert_objects(objects, transaction_ids=ids, import_id=import_id)
        rcn.refresh_comparables()
        rcn.finish_import(
            import_id,
            status="ok",
            source_rows=5,
            imported_rows=5,
            skipped_rows=0,
        )

        report = score_database(db, config=load_scoring_config(), geo=GeoContext())
        assert report["priceEvidence"]["rcn"] == 1
        row = db.conn.execute("SELECT score_breakdown FROM listing").fetchone()
        assert row is not None
        assert json.loads(row["score_breakdown"])["priceEvidence"]["rcn"] is not None


def test_flat_can_use_property_price_with_supporting_plot_and_building(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        rcn = RcnDatabase(db.conn)
        import_id = rcn.start_import(
            region_id="malopolskie",
            object_kind="lokale",
            source_url="https://example.test/rcn.parquet",
            etag=None,
            last_modified=None,
        )
        transaction, flat = _normalize(_row(LOK_ID_LOKALU="flat-1"))
        _, plot = _normalize(
            _row(DZI_ID_DZIALKI="plot-1", DZI_POW_EWID=800, DZI_CENA_BRUTTO=None),
            layer="dzialki",
        )
        _, building = _normalize(
            _row(BUD_ID_BUDYNKU="building-1", BUD_POW_UZYT=2000, BUD_CENA_BRUTTO=None),
            layer="budynki",
        )
        for obj in (flat, plot, building):
            obj["_transaction_key"] = transaction["transaction_key"]
        rcn.upsert_transactions([transaction])
        ids = rcn.transaction_ids([transaction["transaction_key"]])
        rcn.upsert_objects([flat, plot, building], transaction_ids=ids, import_id=import_id)
        rcn.refresh_comparables()

        row = db.conn.execute(
            "SELECT comparable_price_pln, exclusion_reason FROM rcn_transaction_object "
            "WHERE object_kind = 'flat'"
        ).fetchone()
        assert row is not None
        assert row["comparable_price_pln"] == 500000
        assert row["exclusion_reason"] is None


def test_residential_building_can_use_property_price_with_one_plot(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        rcn = RcnDatabase(db.conn)
        import_id = rcn.start_import(
            region_id="malopolskie",
            object_kind="budynki",
            source_url="https://example.test/rcn.parquet",
            etag=None,
            last_modified=None,
        )
        transaction, building = _normalize(
            _row(
                TRAN_CENA_BRUTTO=900000,
                NIER_CENA_BRUTTO=900000,
                BUD_ID_BUDYNKU="building-1",
                BUD_RODZAJ="mieszkalny",
                BUD_POW_UZYT=140,
                BUD_CENA_BRUTTO=None,
            ),
            layer="budynki",
        )
        _, plot = _normalize(
            _row(DZI_ID_DZIALKI="plot-1", DZI_POW_EWID=900, DZI_CENA_BRUTTO=None),
            layer="dzialki",
        )
        building["_transaction_key"] = transaction["transaction_key"]
        plot["_transaction_key"] = transaction["transaction_key"]
        rcn.upsert_transactions([transaction])
        ids = rcn.transaction_ids([transaction["transaction_key"]])
        rcn.upsert_objects([building, plot], transaction_ids=ids, import_id=import_id)
        rcn.refresh_comparables()

        row = db.conn.execute(
            "SELECT comparable_price_pln, exclusion_reason FROM rcn_transaction_object "
            "WHERE object_kind = 'building'"
        ).fetchone()
        assert row is not None
        assert row["comparable_price_pln"] == 900000
        assert row["exclusion_reason"] is None


def test_multi_object_transaction_price_is_not_split(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        rcn = RcnDatabase(db.conn)
        import_id = rcn.start_import(
            region_id="malopolskie",
            object_kind="lokale",
            source_url="https://example.test/rcn.parquet",
            etag=None,
            last_modified=None,
        )
        first_transaction, first_obj = _normalize(_row(LOK_ID_LOKALU="flat-1"))
        second_transaction, second_obj = _normalize(_row(LOK_ID_LOKALU="flat-2"))
        first_obj["_transaction_key"] = first_transaction["transaction_key"]
        second_obj["_transaction_key"] = second_transaction["transaction_key"]
        rcn.upsert_transactions([first_transaction, second_transaction])
        ids = rcn.transaction_ids([first_transaction["transaction_key"]])
        rcn.upsert_objects([first_obj, second_obj], transaction_ids=ids, import_id=import_id)
        rcn.refresh_comparables()

        rows = db.conn.execute(
            "SELECT comparable_price_pln, exclusion_reason FROM rcn_transaction_object"
        ).fetchall()
        assert len(rows) == 2
        assert all(row["comparable_price_pln"] is None for row in rows)
        assert all(row["exclusion_reason"] == "missing_price" for row in rows)


def test_nominal_prices_are_excluded_from_comparables(tmp_path: Path) -> None:
    with _database(tmp_path) as db:
        rcn = RcnDatabase(db.conn)
        import_id = rcn.start_import(
            region_id="malopolskie",
            object_kind="lokale",
            source_url="https://example.test/rcn.parquet",
            etag=None,
            last_modified=None,
        )
        transaction, obj = _normalize(
            _row(TRAN_CENA_BRUTTO=1, NIER_CENA_BRUTTO=1, LOK_CENA_BRUTTO=1)
        )
        obj["_transaction_key"] = transaction["transaction_key"]
        rcn.upsert_transactions([transaction])
        ids = rcn.transaction_ids([transaction["transaction_key"]])
        rcn.upsert_objects([obj], transaction_ids=ids, import_id=import_id)
        rcn.refresh_comparables()

        row = db.conn.execute(
            "SELECT exclusion_reason, price_per_m2_pln FROM rcn_transaction_object"
        ).fetchone()
        assert row is not None
        assert row["exclusion_reason"] == "nominal_price"
        assert row["price_per_m2_pln"] is None


def test_rcn_config_uses_official_export_pattern() -> None:
    config = load_rcn_config()

    assert config.dataset_url("12", "lokale") == (
        "https://opendata.geoportal.gov.pl/InneDane/latest_exports/"
        "rcn_transakcje_ceny/PARQUET/12_transakcje_ceny_lokale.parquet"
    )
    assert "państwowego zasobu geodezyjnego" in config.attribution


@pytest.mark.parametrize(
    "code", ["mieszkalny", "budynekMieszkalnyJednorodzinny", "budynek_mieszkalny_jednorodzinny"]
)
def test_residential_building_codes(code: str) -> None:
    assert is_residential_building(code)


def test_multi_family_building_is_not_a_house_comparable() -> None:
    assert not is_residential_building("budynekMieszkalnyWielorodzinny")

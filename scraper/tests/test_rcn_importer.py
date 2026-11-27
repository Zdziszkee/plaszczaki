from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pytest

from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.rcn import importer
from plaszczaki_scraper.rcn.client import DownloadResult
from plaszczaki_scraper.rcn.config import load_rcn_config


def _migration_sql() -> str:
    migrations = sorted((repo_root() / "web" / "drizzle").glob("*.sql"))
    return "\n".join(migration.read_text(encoding="utf-8") for migration in migrations)


def _database(tmp_path: Path) -> Database:
    path = tmp_path / "test.db"
    connection = sqlite3.connect(path)
    connection.executescript(_migration_sql())
    connection.commit()
    connection.close()
    return Database(path)


def _parquet(path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("LOAD spatial")
    connection.execute(
        """
        COPY (
            SELECT
                1::INTEGER AS gid,
                '1261'::VARCHAR AS teryt,
                'PL.PZGiK.4094.RCN'::VARCHAR AS tran_przestrzen_nazw,
                'transaction-1'::VARCHAR AS tran_lokalny_id_iip,
                TIMESTAMP '2026-01-02 10:00:00' AS tran_wersja_id,
                'transaction-1'::VARCHAR AS tran_oznaczenie_trans,
                'wolnyRynek'::VARCHAR AS tran_rodzaj_trans,
                'rynekWtorny'::VARCHAR AS tran_rodzaj_rynku,
                'osobaFizyczna'::VARCHAR AS tran_sprzedajacy,
                'osobaFizyczna'::VARCHAR AS tran_kupujacy,
                500000::DOUBLE AS tran_cena_brutto,
                'Rep. A 1/2026'::VARCHAR AS dok_oznaczenie,
                DATE '2026-01-01' AS dok_data,
                'nieruchomoscLokalowa'::VARCHAR AS nier_rodzaj,
                'wlasnoscLokaluWrazZPrawemZwiazanym'::VARCHAR AS nier_prawo,
                '1/1'::VARCHAR AS nier_udzial,
                500000::DOUBLE AS nier_cena_brutto,
                'flat-1'::VARCHAR AS lok_id_lokalu,
                'mieszkalna'::VARCHAR AS lok_funkcja,
                2::SMALLINT AS lok_liczba_izb,
                2::SMALLINT AS lok_nr_kond,
                50::DOUBLE AS lok_pow_uzyt,
                NULL::DOUBLE AS lok_cena_brutto,
                'MSC:KRAKÓW;UL:PLAC JULIUSZA KOSSAKA;NR_PORZ:5'::VARCHAR AS lok_adres,
                ST_SetCRS(ST_Point(567046.3950674619, 244169.06814420596), 'EPSG:2180') AS geometry
        ) TO ? (FORMAT PARQUET)
        """,
        [str(path)],
    )
    connection.close()


def test_import_dataset_streams_parquet_and_skips_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parquet = tmp_path / "source.parquet"
    _parquet(parquet)

    def connection(_cache_dir: Path) -> duckdb.DuckDBPyConnection:
        value = duckdb.connect(config={"threads": 2, "memory_limit": "256MB"})
        value.execute("LOAD spatial")
        return value

    monkeypatch.setattr(importer, "_duckdb_connection", connection)
    changed = True

    def download(*args: object, **kwargs: object) -> DownloadResult:
        return DownloadResult(
            path=parquet,
            changed=changed,
            etag='"etag"',
            last_modified="today",
            sha256="sha",
            bytes_downloaded=parquet.stat().st_size,
        )

    monkeypatch.setattr(importer, "download_dataset", download)
    with _database(tmp_path) as db:
        first = importer.import_dataset(
            db,
            region_id="malopolskie",
            region_teryt="12",
            source_layer="lokale",
            config=load_rcn_config(),
            cache_dir=tmp_path / "cache",
        )
        assert first["status"] == "ok"
        assert first["source_rows"] == 1
        assert first["imported_rows"] == 1
        row = db.conn.execute(
            "SELECT lat, lng, comparable_price_pln, price_per_m2_pln, exclusion_reason "
            "FROM rcn_transaction_object"
        ).fetchone()
        assert row is not None
        assert row["lat"] is not None and row["lng"] is not None
        assert abs(row["lat"] - 50.061) < 0.01
        assert abs(row["lng"] - 19.937) < 0.01
        assert row["comparable_price_pln"] == 500000
        assert row["price_per_m2_pln"] == 10000
        assert row["exclusion_reason"] is None

        changed = False
        second = importer.import_dataset(
            db,
            region_id="malopolskie",
            region_teryt="12",
            source_layer="lokale",
            config=load_rcn_config(),
            cache_dir=tmp_path / "cache",
        )
        assert second["status"] == "skipped"
        assert db.conn.execute("SELECT count(*) FROM rcn_transaction_object").fetchone()[0] == 1

        parquet.write_bytes(b"not a parquet file")
        changed = True
        with pytest.raises(duckdb.Error):
            importer.import_dataset(
                db,
                region_id="malopolskie",
                region_teryt="12",
                source_layer="lokale",
                config=load_rcn_config(),
                cache_dir=tmp_path / "cache",
            )
        assert db.conn.execute("SELECT count(*) FROM rcn_transaction_object").fetchone()[0] == 1
        failed = db.conn.execute(
            "SELECT status FROM rcn_import ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert failed is not None and failed["status"] == "failed"

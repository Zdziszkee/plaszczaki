"""Streaming importer for the official GUGiK RCN GeoParquet exports."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import duckdb

from plaszczaki_scraper.db import Database
from plaszczaki_scraper.rcn.client import download_dataset
from plaszczaki_scraper.rcn.config import RcnConfig, load_rcn_config
from plaszczaki_scraper.rcn.normalize import normalize_row
from plaszczaki_scraper.rcn.rcn_db import RcnDatabase
from plaszczaki_scraper.robots import default_cache_dir

logger = logging.getLogger(__name__)

SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "TERYT": ("TERYT", "teryt"),
    "TRAN_PRZESTRZEN_NAZW": ("TRAN_PRZESTRZEN_NAZW", "tran_przestrzen_nazw"),
    "TRAN_LOKALNY_ID_IIP": ("TRAN_LOKALNY_ID_IIP", "tran_lokalny_id_iip"),
    "TRAN_WERSJA_ID": ("TRAN_WERSJA_ID", "tran_wersja_id"),
    "TRAN_OZNACZENIE_TRANS": ("TRAN_OZNACZENIE_TRANS", "tran_oznaczenie_trans"),
    "TRAN_RODZAJ_TRANS": ("TRAN_RODZAJ_TRANS", "tran_rodzaj_trans"),
    "TRAN_RODZAJ_RYNKU": ("TRAN_RODZAJ_RYNKU", "tran_rodzaj_rynku"),
    "TRAN_SPRZEDAJACY": ("TRAN_SPRZEDAJACY", "tran_sprzedajacy"),
    "TRAN_KUPUJACY": ("TRAN_KUPUJACY", "tran_kupujacy"),
    "TRAN_CENA_BRUTTO": ("TRAN_CENA_BRUTTO", "tran_cena_brutto"),
    "TRAN_VAT": ("TRAN_VAT", "tran_vat"),
    "DOK_OZNACZENIE": ("DOK_OZNACZENIE", "dok_oznaczenie"),
    "DOK_DATA": ("DOK_DATA", "dok_data"),
    "DOK_TWORCA": ("DOK_TWORCA", "dok_tworca"),
    "NIER_RODZAJ": ("NIER_RODZAJ", "nier_rodzaj"),
    "NIER_PRAWO": ("NIER_PRAWO", "nier_prawo"),
    "NIER_UDZIAL": ("NIER_UDZIAL", "nier_udzial"),
    "NIER_POW_GRUNTU": ("NIER_POW_GRUNTU", "nier_pow_gruntu"),
    "NIER_OPIS": ("NIER_OPIS", "nier_opis"),
    "NIER_CENA_BRUTTO": ("NIER_CENA_BRUTTO", "nier_cena_brutto"),
    "NIER_VAT": ("NIER_VAT", "nier_vat"),
    "DZI_ID_DZIALKI": ("DZI_ID_DZIALKI", "dzi_id_dzialki"),
    "DZI_NR_DZIALKI": ("DZI_NR_DZIALKI", "dzi_nr_dzialki"),
    "DZI_PRZEZN_WMPZP": ("DZI_PRZEZN_WMPZP", "dzi_przezn_wmpzp"),
    "DZI_POW_EWID": ("DZI_POW_EWID", "dzi_pow_ewid"),
    "DZI_SPOSOB_UZYT": ("DZI_SPOSOB_UZYT", "dzi_sposob_uzyt"),
    "DZI_CENA_BRUTTO": ("DZI_CENA_BRUTTO", "dzi_cena_brutto"),
    "DZI_VAT": ("DZI_VAT", "dzi_vat"),
    "DZI_INFO": ("DZI_INFO", "dzi_info"),
    "DZI_ADRES": ("DZI_ADRES", "dzi_adres"),
    "BUD_ID_BUDYNKU": ("BUD_ID_BUDYNKU", "bud_id_budynku"),
    "BUD_RODZAJ": ("BUD_RODZAJ", "bud_rodzaj"),
    "BUD_POW_UZYT": ("BUD_POW_UZYT", "bud_pow_uzyt"),
    "BUD_CENA_BRUTTO": ("BUD_CENA_BRUTTO", "bud_cena_brutto"),
    "BUD_VAT": ("BUD_VAT", "bud_vat"),
    "BUD_INFO": ("BUD_INFO", "bud_info"),
    "BUD_ADRES": ("BUD_ADRES", "bud_adres"),
    "LOK_ID_LOKALU": ("LOK_ID_LOKALU", "lok_id_lokalu"),
    "LOK_FUNKCJA": ("LOK_FUNKCJA", "lok_funkcja"),
    "LOK_LICZBA_IZB": ("LOK_LICZBA_IZB", "lok_liczba_izb"),
    "LOK_NR_KOND": ("LOK_NR_KOND", "lok_nr_kond"),
    "LOK_POW_UZYT": ("LOK_POW_UZYT", "lok_pow_uzyt"),
    "LOK_POW_PRZYN": ("LOK_POW_PRZYN", "lok_pow_przyn"),
    "LOK_CENA_BRUTTO": ("LOK_CENA_BRUTTO", "lok_cena_brutto"),
    "LOK_VAT": ("LOK_VAT", "lok_vat"),
    "LOK_INFO": ("LOK_INFO", "lok_info"),
    "LOK_ADRES": ("LOK_ADRES", "lok_adres"),
    "SERWIS_RCN": ("SERWIS_RCN", "serwis_rcn"),
    "gid": ("gid", "GID"),
}


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _duckdb_connection(cache_dir: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(config={"threads": 2, "memory_limit": "1GB"})
    extension_dir = cache_dir / "duckdb-extensions"
    extension_dir.mkdir(parents=True, exist_ok=True)
    try:
        connection.execute("SET extension_directory = ?", [str(extension_dir)])
    except duckdb.Error:
        logger.debug("DuckDB does not support extension_directory on this version")
    connection.execute("INSTALL spatial")
    connection.execute("LOAD spatial")
    return connection


def _schema_columns(connection: duckdb.DuckDBPyConnection, path: Path) -> dict[str, str]:
    rows = connection.execute(
        "DESCRIBE SELECT * FROM read_parquet(?) LIMIT 0", [str(path)]
    ).fetchall()
    return {str(row[0]).lower(): str(row[0]) for row in rows}


def _geometry_column(columns: dict[str, str]) -> str | None:
    for candidate in ("geom", "geometry", "geometria"):
        if candidate in columns:
            return columns[candidate]
    return None


def _build_query(columns: dict[str, str]) -> tuple[str, list[str]]:
    expressions: list[str] = []
    output_names: list[str] = []
    for output_name, candidates in SOURCE_FIELDS.items():
        source_name = next(
            (columns[item.lower()] for item in candidates if item.lower() in columns), None
        )
        expression = _quote(source_name) if source_name else "NULL"
        expressions.append(f"{expression} AS {_quote(output_name)}")
        output_names.append(output_name)

    geometry = _geometry_column(columns)
    if geometry:
        geometry_ref = _quote(geometry)
        centroid = f"ST_Centroid({geometry_ref})"
        expressions.extend(
            (
                f"ST_X({centroid}) AS {_quote('source_x')}",
                f"ST_Y({centroid}) AS {_quote('source_y')}",
                f"ST_CRS({geometry_ref}) AS {_quote('source_crs')}",
            )
        )
        output_names.extend(("source_x", "source_y", "source_crs"))
    return "SELECT " + ", ".join(expressions) + " FROM read_parquet(?)", output_names


def import_dataset(
    db: Database,
    *,
    region_id: str,
    region_teryt: str,
    source_layer: str,
    config: RcnConfig | None = None,
    cache_dir: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    refresh_comparables: bool = True,
) -> dict[str, Any]:
    """Import one official region/layer snapshot into SQLite."""
    config = config or load_rcn_config()
    if source_layer not in config.object_kinds:
        raise ValueError(f"unsupported RCN object kind: {source_layer}")
    cache_dir = cache_dir or default_cache_dir() / "rcn"
    url = config.dataset_url(region_teryt, source_layer)
    destination = cache_dir / f"{region_teryt}_{source_layer}.parquet"
    rcn_db = RcnDatabase(db.conn)
    previous = (
        None
        if dry_run
        else rcn_db.latest_successful_import(region_id=region_id, object_kind=source_layer)
    )
    download = download_dataset(
        url,
        destination,
        etag=str(previous["etag"]) if previous and previous["etag"] else None,
        last_modified=(
            str(previous["last_modified"]) if previous and previous["last_modified"] else None
        ),
        force=force,
        timeout=config.request_timeout_seconds,
    )
    if not download.changed:
        result = {
            "region": region_id,
            "kind": source_layer,
            "status": "skipped",
            "source_rows": 0,
            "imported_rows": 0,
            "skipped_rows": 0,
        }
        if not dry_run:
            import_id = rcn_db.start_import(
                region_id=region_id,
                object_kind=source_layer,
                source_url=url,
                etag=download.etag,
                last_modified=download.last_modified,
            )
            rcn_db.finish_import(
                import_id,
                status="skipped",
                source_rows=0,
                imported_rows=0,
                skipped_rows=0,
                file_sha256=download.sha256,
                etag=download.etag,
                last_modified=download.last_modified,
            )
        return result

    import_id = None
    if not dry_run:
        import_id = rcn_db.start_import(
            region_id=region_id,
            object_kind=source_layer,
            source_url=url,
            etag=download.etag,
            last_modified=download.last_modified,
        )

    source_rows = 0
    imported_rows = 0
    normalized_rows = 0
    skipped_rows = 0
    printed = 0
    try:
        with _duckdb_connection(cache_dir) as connection:
            columns = _schema_columns(connection, download.path)
            query, output_names = _build_query(columns)
            if limit is not None:
                query += " LIMIT ?"
                cursor = connection.execute(query, [str(download.path), limit])
            else:
                cursor = connection.execute(query, [str(download.path)])

            while True:
                batch = cursor.fetchmany(config.batch_rows)
                if not batch:
                    break
                transactions: list[dict[str, Any]] = []
                objects: list[dict[str, Any]] = []
                for values in batch:
                    source_rows += 1
                    row = dict(zip(output_names, values, strict=True))
                    normalized = normalize_row(
                        row,
                        region_id=region_id,
                        region_teryt=region_teryt,
                        source_layer=source_layer,
                        source_url=url,
                        grid_degrees=config.grid_degrees,
                        now_ms=0,
                    )
                    if normalized is None:
                        skipped_rows += 1
                        continue
                    transaction, obj = normalized
                    transactions.append(transaction)
                    obj["_transaction_key"] = transaction["transaction_key"]
                    objects.append(obj)
                normalized_rows += len(objects)

                if dry_run:
                    for obj in objects:
                        if printed >= (limit or 25):
                            break
                        printable = {
                            key: value for key, value in obj.items() if not key.startswith("_")
                        }
                        print(json.dumps(printable, ensure_ascii=False, default=str))
                        printed += 1
                    if printed >= 25:
                        break
                    continue

                assert import_id is not None
                rcn_db.upsert_transactions(transactions)
                ids = rcn_db.transaction_ids(
                    [str(transaction["transaction_key"]) for transaction in transactions]
                )
                imported_rows += rcn_db.upsert_objects(
                    objects, transaction_ids=ids, import_id=import_id
                )
                if source_rows % (config.batch_rows * 20) == 0:
                    logger.info(
                        "RCN %s/%s: %s rows, %s objects",
                        region_id,
                        source_layer,
                        source_rows,
                        imported_rows,
                    )

        if dry_run:
            return {
                "region": region_id,
                "kind": source_layer,
                "status": "dry-run",
                "source_rows": source_rows,
                "imported_rows": normalized_rows,
                "skipped_rows": skipped_rows,
                "printed": printed,
            }

        assert import_id is not None
        if limit is None:
            rcn_db.reconcile(region_id=region_id, source_layer=source_layer, import_id=import_id)
            if refresh_comparables:
                rcn_db.refresh_comparables()
        status = "ok" if limit is None else "partial"
        rcn_db.finish_import(
            import_id,
            status=status,
            source_rows=source_rows,
            imported_rows=imported_rows,
            skipped_rows=skipped_rows,
            file_sha256=download.sha256,
            etag=download.etag,
            last_modified=download.last_modified,
        )
        return {
            "region": region_id,
            "kind": source_layer,
            "status": status,
            "source_rows": source_rows,
            "imported_rows": imported_rows,
            "skipped_rows": skipped_rows,
            "bytes_downloaded": download.bytes_downloaded,
        }
    except Exception as exc:
        if import_id is not None:
            rcn_db.finish_import(
                import_id,
                status="failed",
                source_rows=source_rows,
                imported_rows=imported_rows,
                skipped_rows=skipped_rows,
                error=f"{type(exc).__name__}: {exc}",
                file_sha256=download.sha256,
                etag=download.etag,
                last_modified=download.last_modified,
            )
        raise


def sync_datasets(
    db: Database,
    *,
    regions: list[tuple[str, str]],
    config: RcnConfig | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    config = config or load_rcn_config()
    results = []
    for region_id, region_teryt in regions:
        for source_layer in config.object_kinds:
            logger.info("syncing RCN %s/%s", region_id, source_layer)
            results.append(
                import_dataset(
                    db,
                    region_id=region_id,
                    region_teryt=region_teryt,
                    source_layer=source_layer,
                    config=config,
                    force=force,
                    refresh_comparables=False,
                )
            )
    if any(result["status"] == "ok" for result in results):
        RcnDatabase(db.conn).refresh_comparables()
    return results

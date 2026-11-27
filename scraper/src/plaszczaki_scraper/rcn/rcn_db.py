"""SQLite persistence for normalized RCN transactions and objects."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

TRANSACTION_COLUMNS = (
    "transaction_key",
    "county_teryt",
    "region_id",
    "iip_namespace",
    "local_id",
    "version_at",
    "designation",
    "transaction_type",
    "market",
    "seller_type",
    "buyer_type",
    "gross_price_pln",
    "vat_pln",
    "document_ref",
    "document_date",
    "document_creator",
    "property_kind",
    "right_kind",
    "share_raw",
    "share_fraction",
    "land_area_m2",
    "property_price_pln",
    "property_vat_pln",
    "description",
    "source_url",
    "record_hash",
)

OBJECT_COLUMNS = (
    "object_key",
    "region_id",
    "object_kind",
    "object_id",
    "object_number",
    "address_text",
    "city",
    "street",
    "function_raw",
    "rooms",
    "floor",
    "usable_area_m2",
    "ancillary_area_m2",
    "plot_area_m2",
    "mpzp_purpose",
    "land_use",
    "building_type_raw",
    "object_price_pln",
    "object_vat_pln",
    "info",
    "lat",
    "lng",
    "geo_cell_lat",
    "geo_cell_lng",
    "source_layer",
    "source_gid",
    "record_hash",
)

NULLABLE_TRANSACTION_COLUMNS = frozenset(
    column
    for column in TRANSACTION_COLUMNS
    if column
    not in {
        "transaction_key",
        "county_teryt",
        "region_id",
        "iip_namespace",
        "local_id",
        "record_hash",
    }
)
NULLABLE_OBJECT_COLUMNS = frozenset(
    column
    for column in OBJECT_COLUMNS
    if column
    not in {
        "object_key",
        "region_id",
        "object_kind",
        "source_layer",
        "record_hash",
    }
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class RcnDatabase:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.conn = connection

    def start_import(
        self,
        *,
        region_id: str,
        object_kind: str,
        source_url: str,
        etag: str | None,
        last_modified: str | None,
    ) -> int:
        now = _now_ms()
        cursor = self.conn.execute(
            """
            INSERT INTO rcn_import (
                region_id, object_kind, source_url, etag, last_modified,
                status, started_at
            ) VALUES (?, ?, ?, ?, ?, 'running', ?)
            """,
            (region_id, object_kind, source_url, etag, last_modified, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def finish_import(
        self,
        import_id: int,
        *,
        status: str,
        source_rows: int,
        imported_rows: int,
        skipped_rows: int,
        error: str | None = None,
        file_sha256: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE rcn_import SET
                status = ?, finished_at = ?, source_rows = ?, imported_rows = ?,
                skipped_rows = ?, error = ?, file_sha256 = ?, etag = ?,
                last_modified = ?
            WHERE id = ?
            """,
            (
                status,
                _now_ms(),
                source_rows,
                imported_rows,
                skipped_rows,
                error,
                file_sha256,
                etag,
                last_modified,
                import_id,
            ),
        )
        self.conn.commit()

    def latest_successful_import(self, *, region_id: str, object_kind: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM rcn_import
            WHERE region_id = ? AND object_kind = ? AND status = 'ok'
            ORDER BY id DESC LIMIT 1
            """,
            (region_id, object_kind),
        ).fetchone()

    def upsert_transactions(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = _now_ms()
        placeholders = ", ".join(
            "?" for _ in (*TRANSACTION_COLUMNS, "first_seen_at", "last_seen_at")
        )
        columns = ", ".join((*TRANSACTION_COLUMNS, "first_seen_at", "last_seen_at"))
        updates = []
        for column in TRANSACTION_COLUMNS:
            if column in {
                "transaction_key",
                "county_teryt",
                "region_id",
                "iip_namespace",
                "local_id",
            }:
                updates.append(f"{column} = excluded.{column}")
            elif column == "record_hash":
                updates.append("record_hash = excluded.record_hash")
            else:
                updates.append(f"{column} = coalesce(excluded.{column}, rcn_transaction.{column})")
        updates.append("last_seen_at = excluded.last_seen_at")
        sql = f"""
            INSERT INTO rcn_transaction ({columns}) VALUES ({placeholders})
            ON CONFLICT(transaction_key) DO UPDATE SET {", ".join(updates)}
        """
        values = [(*[row.get(column) for column in TRANSACTION_COLUMNS], now, now) for row in rows]
        with self.conn:
            self.conn.executemany(sql, values)

    def transaction_ids(self, keys: list[str]) -> dict[str, int]:
        found: dict[str, int] = {}
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT transaction_key, id FROM rcn_transaction WHERE transaction_key IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update({str(row["transaction_key"]): int(row["id"]) for row in rows})
        return found

    def upsert_objects(
        self,
        rows: list[dict[str, Any]],
        *,
        transaction_ids: dict[str, int],
        import_id: int,
    ) -> int:
        if not rows:
            return 0
        now = _now_ms()
        columns = (
            *OBJECT_COLUMNS,
            "transaction_id",
            "last_seen_import_id",
            "first_seen_at",
            "last_seen_at",
        )
        placeholders = ", ".join("?" for _ in columns)
        updates = []
        for column in OBJECT_COLUMNS:
            if column in {"object_key", "region_id", "object_kind", "source_layer"}:
                updates.append(f"{column} = excluded.{column}")
            elif column == "record_hash":
                updates.append("record_hash = excluded.record_hash")
            else:
                updates.append(
                    f"{column} = coalesce(excluded.{column}, rcn_transaction_object.{column})"
                )
        updates.extend(
            (
                "transaction_id = excluded.transaction_id",
                "last_seen_import_id = excluded.last_seen_import_id",
                "last_seen_at = excluded.last_seen_at",
            )
        )
        sql = f"""
            INSERT INTO rcn_transaction_object ({", ".join(columns)}) VALUES ({placeholders})
            ON CONFLICT(object_key) DO UPDATE SET {", ".join(updates)}
        """
        values = []
        for row in rows:
            transaction_id = transaction_ids.get(str(row["_transaction_key"]))
            if transaction_id is None:
                continue
            values.append(
                (
                    *[row.get(column) for column in OBJECT_COLUMNS],
                    transaction_id,
                    import_id,
                    now,
                    now,
                )
            )
        if not values:
            return 0
        with self.conn:
            self.conn.executemany(sql, values)
        return len(values)

    def reconcile(self, *, region_id: str, source_layer: str, import_id: int) -> int:
        """Remove objects absent from a successfully completed source snapshot."""
        with self.conn:
            cursor = self.conn.execute(
                """
                DELETE FROM rcn_transaction_object
                WHERE region_id = ? AND source_layer = ?
                  AND (last_seen_import_id IS NULL OR last_seen_import_id != ?)
                """,
                (region_id, source_layer, import_id),
            )
            removed = cursor.rowcount
            self.conn.execute(
                """
                DELETE FROM rcn_transaction
                WHERE id NOT IN (SELECT transaction_id FROM rcn_transaction_object)
                """
            )
        return max(0, removed)

    def refresh_comparables(self) -> None:
        """Derive conservative, object-attributed comparison prices."""
        fallback = """(
            (o.object_kind = 'flat'
             AND (SELECT count(*) FROM rcn_transaction_object x
                  WHERE x.transaction_id = o.transaction_id AND x.object_kind = 'flat') = 1
             AND (SELECT count(*) FROM rcn_transaction_object x
                  WHERE x.transaction_id = o.transaction_id AND x.object_kind = 'premises') = 0)
            OR
            (o.object_kind = 'building'
             AND (SELECT count(*) FROM rcn_transaction_object x
                  WHERE x.transaction_id = o.transaction_id AND x.object_kind = 'building') = 1
             AND (SELECT count(*) FROM rcn_transaction_object x
                  WHERE x.transaction_id = o.transaction_id
                    AND x.object_kind IN ('flat', 'premises')) = 0)
            OR
            (o.object_kind = 'plot'
             AND (SELECT count(*) FROM rcn_transaction_object x
                  WHERE x.transaction_id = o.transaction_id AND x.object_kind = 'plot') = 1
             AND (SELECT count(*) FROM rcn_transaction_object x
                  WHERE x.transaction_id = o.transaction_id
                    AND x.object_kind IN ('flat', 'building', 'premises')) = 0)
        )"""
        price_case = f"""CASE
            WHEN o.object_price_pln > 0 THEN o.object_price_pln
            WHEN t.property_price_pln > 0 AND {fallback} THEN t.property_price_pln
            WHEN t.gross_price_pln > 0 AND {fallback} THEN t.gross_price_pln
            ELSE NULL
        END"""
        with self.conn:
            self.conn.execute(
                f"""
                UPDATE rcn_transaction_object AS o SET
                    comparable_area_m2 = CASE o.object_kind
                        WHEN 'plot' THEN o.plot_area_m2
                        WHEN 'building' THEN o.usable_area_m2
                        WHEN 'flat' THEN o.usable_area_m2
                        ELSE NULL
                    END,
                    comparable_price_pln = {price_case},
                    exclusion_reason = CASE
                        WHEN lower(t.transaction_type) != 'wolnyrynek' THEN 'non_market'
                        WHEN abs(coalesce(t.share_fraction, 0) - 1) > 0.000000001
                            THEN 'partial_share'
                        WHEN t.document_date IS NULL THEN 'missing_date'
                        WHEN o.lat IS NULL OR o.lng IS NULL THEN 'missing_geometry'
                        WHEN o.object_kind = 'flat'
                             AND lower(replace(o.function_raw, '_', '')) != 'mieszkalna'
                            THEN 'non_residential'
                        WHEN o.object_kind = 'building'
                             AND lower(replace(o.building_type_raw, '_', '')) NOT IN (
                                 'mieszkalny',
                                 'budynekmieszkalnyjednorodzinny',
                                 'budynekmieszkalnyjednorodzinnyzlozkasamodzielna',
                                 'budynekmieszkalnyjednorodzinnyzlozkawspolnotowa'
                             ) THEN 'unsupported_building'
                        WHEN o.object_kind NOT IN ('flat', 'building', 'plot')
                            THEN 'unsupported_object'
                        WHEN CASE o.object_kind
                            WHEN 'plot' THEN o.plot_area_m2
                            ELSE o.usable_area_m2
                        END IS NULL OR CASE o.object_kind
                            WHEN 'plot' THEN o.plot_area_m2
                            ELSE o.usable_area_m2
                        END <= 0 THEN 'missing_area'
                        WHEN {price_case} IS NULL THEN 'missing_price'
                        WHEN {price_case} < 10000 THEN 'nominal_price'
                        ELSE NULL
                    END
                FROM rcn_transaction AS t
                WHERE t.id = o.transaction_id
                """
            )
            self.conn.execute(
                """
                UPDATE rcn_transaction_object
                SET price_per_m2_pln = comparable_price_pln / comparable_area_m2
                WHERE exclusion_reason IS NULL
                  AND comparable_price_pln > 0 AND comparable_area_m2 > 0
                """
            )
            self.conn.execute(
                """
                UPDATE rcn_transaction_object
                SET price_per_m2_pln = NULL
                WHERE exclusion_reason IS NOT NULL
                   OR comparable_price_pln IS NULL OR comparable_area_m2 IS NULL
                """
            )

    def status(self) -> dict[str, Any]:
        latest = self.conn.execute(
            """
            SELECT i.* FROM rcn_import i
            JOIN (
                SELECT region_id, object_kind, max(id) AS id
                FROM rcn_import GROUP BY region_id, object_kind
            ) q ON q.id = i.id
            ORDER BY i.region_id, i.object_kind
            """
        ).fetchall()
        totals = self.conn.execute(
            """
            SELECT
                (SELECT count(*) FROM rcn_transaction) AS transactions,
                (SELECT count(*) FROM rcn_transaction_object) AS objects,
                (SELECT count(*) FROM rcn_transaction_object
                 WHERE exclusion_reason IS NULL AND price_per_m2_pln IS NOT NULL)
                    AS comparable_objects
            """
        ).fetchone()
        return {
            "imports": [dict(row) for row in latest],
            "transactions": int(totals["transactions"]),
            "objects": int(totals["objects"]),
            "comparable_objects": int(totals["comparable_objects"]),
        }

"""SQLite persistence for scraped listings.

The schema is owned by the web app (Drizzle). This module verifies the expected
tables/columns and then reads and writes with plain ``sqlite3``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from plaszczaki_scraper.canonical import canonical_key
from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.models import Listing

LISTING_COLUMNS = (
    "source_id",
    "external_id",
    "url",
    "title",
    "description",
    "offer_type",
    "property_type",
    "market",
    "price_kind",
    "price_unit",
    "price_pln",
    "rent_pln",
    "deposit_pln",
    "price_per_m2_pln",
    "area_m2",
    "land_area_m2",
    "rooms",
    "floor",
    "floor_label",
    "build_year",
    "voivodeship",
    "county",
    "commune",
    "city",
    "district",
    "street",
    "address_text",
    "lat",
    "lng",
    "geo_source",
    "images",
    "seller_type",
    "seller_name",
    "published_at",
    "modified_at",
    "auction_date",
    "auction_round",
    "case_number",
    "extraction_method",
    "extraction_model",
    "extra",
    "canonical_key",
)

TRACKED_COLUMNS = tuple(
    column for column in LISTING_COLUMNS if column not in {"source_id", "external_id"}
)

EXPECTED_SCHEMA: dict[str, set[str]] = {
    "source": {"id", "label", "homepage_url", "adapter", "enabled", "created_at"},
    "listing": {
        "id",
        *LISTING_COLUMNS,
        "first_seen_at",
        "last_seen_at",
    },
    "listing_snapshot": {
        "id",
        "listing_id",
        "observed_at",
        "price_pln",
        "changed",
    },
    "scraper_run": {
        "id",
        "source_id",
        "mode",
        "started_at",
        "finished_at",
        "status",
        "stats",
        "error",
    },
    "scraper_checkpoint": {
        "source_id",
        "query_key",
        "cursor",
        "etag",
        "updated_at",
    },
    "llm_job": {
        "id",
        "source_id",
        "url",
        "kind",
        "input_hash",
        "model",
        "prompt_version",
        "status",
        "attempts",
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "response_json",
        "error",
        "created_at",
        "updated_at",
    },
}


class SchemaError(RuntimeError):
    """Raised when the SQLite schema does not match the web app's migrations."""


class UpsertResult(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class RunSummary:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    details_ok: int = 0
    details_failed: int = 0
    skipped_region: int = 0
    skipped_robots: int = 0
    http_errors: int = 0


def default_database_path() -> Path:
    override = os.environ.get("DATABASE_URL")
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else (repo_root() / path).resolve()
    return repo_root() / "data" / "plaszczaki.db"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _listing_values(listing: Listing) -> dict[str, Any]:
    return {
        "source_id": listing.source_id,
        "external_id": listing.external_id,
        "canonical_key": canonical_key(listing),
        "url": listing.url,
        "title": listing.title,
        "description": listing.description,
        "offer_type": listing.offer_type,
        "property_type": listing.property_type,
        "market": listing.market,
        "price_kind": listing.price_kind,
        "price_unit": listing.price_unit,
        "price_pln": listing.price_pln,
        "rent_pln": listing.rent_pln,
        "deposit_pln": listing.deposit_pln,
        "price_per_m2_pln": listing.price_per_m2_pln,
        "area_m2": listing.area_m2,
        "land_area_m2": listing.land_area_m2,
        "rooms": listing.rooms,
        "floor": listing.floor,
        "floor_label": listing.floor_label,
        "build_year": listing.build_year,
        "voivodeship": listing.voivodeship,
        "county": listing.county,
        "commune": listing.commune,
        "city": listing.city,
        "district": listing.district,
        "street": listing.street,
        "address_text": listing.address_text,
        "lat": listing.lat,
        "lng": listing.lng,
        "geo_source": listing.geo_source,
        "images": _json(listing.images),
        "seller_type": listing.seller_type,
        "seller_name": listing.seller_name,
        "published_at": listing.published_at,
        "modified_at": listing.modified_at,
        "auction_date": listing.auction_date,
        "auction_round": listing.auction_round,
        "case_number": listing.case_number,
        "extraction_method": listing.extraction_method,
        "extraction_model": listing.extraction_model,
        "extra": _json(listing.extra),
    }


def _row_to_listing(row: sqlite3.Row) -> Listing:
    images = json.loads(row["images"]) if row["images"] else []
    extra = json.loads(row["extra"]) if row["extra"] else {}
    return Listing(
        source_id=row["source_id"],
        external_id=row["external_id"],
        url=row["url"],
        title=row["title"],
        description=row["description"],
        offer_type=row["offer_type"],
        property_type=row["property_type"],
        market=row["market"] or "unknown",
        price_kind=row["price_kind"] or "asking",
        price_unit=row["price_unit"] or "total",
        price_pln=row["price_pln"],
        rent_pln=row["rent_pln"],
        deposit_pln=row["deposit_pln"],
        price_per_m2_pln=row["price_per_m2_pln"],
        area_m2=row["area_m2"],
        land_area_m2=row["land_area_m2"],
        rooms=row["rooms"],
        floor=row["floor"],
        floor_label=row["floor_label"],
        build_year=row["build_year"],
        voivodeship=row["voivodeship"],
        county=row["county"],
        commune=row["commune"],
        city=row["city"],
        district=row["district"],
        street=row["street"],
        address_text=row["address_text"],
        lat=row["lat"],
        lng=row["lng"],
        geo_source=row["geo_source"],
        images=images,
        seller_type=row["seller_type"],
        seller_name=row["seller_name"],
        published_at=row["published_at"],
        modified_at=row["modified_at"],
        auction_date=row["auction_date"],
        auction_round=row["auction_round"],
        case_number=row["case_number"],
        extraction_method=row["extraction_method"] or "adapter",
        extraction_model=row["extraction_model"],
        extra=extra,
    )


class Database:
    """Small, explicit SQLite gateway used by the runner."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def verify_schema(self) -> None:
        for table, expected in EXPECTED_SCHEMA.items():
            row = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if row is None:
                raise SchemaError(
                    f"missing table '{table}'; run `bun run db:migrate` first",
                )
            actual = {
                str(column["name"])
                for column in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing = expected - actual
            if missing:
                raise SchemaError(
                    f"table '{table}' is missing columns {sorted(missing)}; "
                    "run `bun run db:migrate` first",
                )

    def upsert_source(
        self,
        *,
        source_id: str,
        label: str,
        homepage_url: str,
        adapter: str,
        enabled: bool = True,
    ) -> None:
        now = _now_ms()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO source (id, label, homepage_url, adapter, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label = excluded.label,
                    homepage_url = excluded.homepage_url,
                    adapter = excluded.adapter,
                    enabled = excluded.enabled
                """,
                (source_id, label, homepage_url, adapter, int(enabled), now),
            )

    def get_listing(self, source_id: str, external_id: str) -> Listing | None:
        row = self.conn.execute(
            "SELECT * FROM listing WHERE source_id = ? AND external_id = ?",
            (source_id, external_id),
        ).fetchone()
        return _row_to_listing(row) if row is not None else None

    def existing_external_ids(self, source_id: str, external_ids: Iterable[str]) -> set[str]:
        """Return which of the given external ids are already stored for a source."""
        ids = [external_id for external_id in external_ids if external_id]
        if not ids:
            return set()

        found: set[str] = set()
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"""
                SELECT external_id FROM listing
                WHERE source_id = ? AND external_id IN ({placeholders})
                """,
                (source_id, *chunk),
            ).fetchall()
            found.update(str(row["external_id"]) for row in rows)
        return found

    def backfill_canonical_keys(self) -> int:
        """Recompute ``canonical_key`` for every stored listing."""
        rows = self.conn.execute("SELECT * FROM listing").fetchall()
        updates: list[tuple[str, int]] = []
        for row in rows:
            key = canonical_key(_row_to_listing(row))
            if row["canonical_key"] != key:
                updates.append((key, int(row["id"])))
        if updates:
            with self.conn:
                self.conn.executemany(
                    "UPDATE listing SET canonical_key = ? WHERE id = ?",
                    updates,
                )
        return len(updates)

    def canonical_stats(self) -> dict[str, int]:
        """Summarise cross-source duplicate clusters."""
        total = int(self.conn.execute("SELECT count(*) FROM listing").fetchone()[0])
        keyed = int(
            self.conn.execute(
                "SELECT count(*) FROM listing WHERE canonical_key IS NOT NULL",
            ).fetchone()[0],
        )
        row = self.conn.execute(
            """
            SELECT count(*) AS clusters, coalesce(sum(item_count - 1), 0) AS duplicates
            FROM (
                SELECT count(*) AS item_count
                FROM listing
                WHERE canonical_key IS NOT NULL
                  AND canonical_key NOT LIKE 'src:%'
                GROUP BY canonical_key
                HAVING count(*) > 1
            )
            """,
        ).fetchone()
        return {
            "rows": total,
            "keyed": keyed,
            "clusters": int(row["clusters"]),
            "duplicate_rows": int(row["duplicates"]),
        }

    def last_run_at(self, source_id: str, mode: str = "incremental") -> int | None:
        row = self.conn.execute(
            """
            SELECT started_at FROM scraper_run
            WHERE source_id = ? AND mode = ?
            ORDER BY started_at DESC LIMIT 1
            """,
            (source_id, mode),
        ).fetchone()
        return int(row["started_at"]) if row is not None else None

    def update_listing_geo(
        self,
        *,
        source_id: str,
        external_id: str,
        lat: float,
        lng: float,
        geo_source: str,
        canonical_key: str | None = None,
    ) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE listing
                SET lat = ?, lng = ?, geo_source = ?,
                    canonical_key = coalesce(?, canonical_key)
                WHERE source_id = ? AND external_id = ?
                """,
                (lat, lng, geo_source, canonical_key, source_id, external_id),
            )
        return cursor.rowcount > 0

    def upsert_listing(self, listing: Listing) -> UpsertResult:
        with self.conn:
            return self._upsert_one(listing)

    def upsert_listings(self, listings: list[Listing]) -> dict[str, int]:
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        if not listings:
            return counts
        with self.conn:
            for listing in listings:
                result = self._upsert_one(listing)
                counts[result.value] += 1
        return counts

    def _upsert_one(self, listing: Listing) -> UpsertResult:
        now = _now_ms()
        values = _listing_values(listing)
        row = self.conn.execute(
            "SELECT * FROM listing WHERE source_id = ? AND external_id = ?",
            (listing.source_id, listing.external_id),
        ).fetchone()

        if row is None:
            columns = [*values, "first_seen_at", "last_seen_at"]
            params = [*values.values(), now, now]
            placeholders = ", ".join("?" for _ in columns)
            self.conn.execute(
                f"INSERT INTO listing ({', '.join(columns)}) VALUES ({placeholders})",
                params,
            )
            return UpsertResult.INSERTED

        old = dict(row)
        changed = [column for column in TRACKED_COLUMNS if old[column] != values[column]]
        if not changed:
            self.conn.execute(
                "UPDATE listing SET last_seen_at = ? WHERE id = ?",
                (now, old["id"]),
            )
            return UpsertResult.UNCHANGED

        assignments = ", ".join(f"{column} = ?" for column in values)
        params: list[Any] = [*values.values(), now, old["id"]]
        self.conn.execute(
            f"UPDATE listing SET {assignments}, last_seen_at = ? WHERE id = ?",
            params,
        )
        if old["price_pln"] != values["price_pln"]:
            self.conn.execute(
                """
                INSERT INTO listing_snapshot
                    (listing_id, observed_at, price_pln, changed)
                VALUES (?, ?, ?, ?)
                """,
                (old["id"], now, values["price_pln"], _json(sorted(changed))),
            )
        return UpsertResult.UPDATED

    def count_listings(self, source_id: str | None = None) -> int:
        if source_id is None:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM listing").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM listing WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def count_by_axes(
        self,
    ) -> dict[tuple[str, str | None, str, str], int]:
        """Count stored listings by (source, voivodeship, offer, property)."""
        rows = self.conn.execute(
            """
            SELECT source_id, voivodeship, offer_type, property_type, COUNT(*) AS count
            FROM listing
            GROUP BY source_id, voivodeship, offer_type, property_type
            """,
        ).fetchall()
        return {
            (
                str(row["source_id"]),
                None if row["voivodeship"] is None else str(row["voivodeship"]),
                str(row["offer_type"]),
                str(row["property_type"]),
            ): int(row["count"])
            for row in rows
        }

    def start_run(self, *, source_id: str, mode: str) -> int:
        now = _now_ms()
        with self.conn:
            self.conn.execute(
                """
                UPDATE scraper_run
                SET status = 'interrupted', finished_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            cursor = self.conn.execute(
                """
                INSERT INTO scraper_run (source_id, mode, started_at, status, stats)
                VALUES (?, ?, ?, 'running', '{}')
                """,
                (source_id, mode, now),
            )
        return int(cursor.lastrowid or 0)

    def finish_run(
        self,
        *,
        run_id: int,
        status: str,
        stats: dict[str, Any],
        error: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE scraper_run
                SET finished_at = ?, status = ?, stats = ?, error = ?
                WHERE id = ?
                """,
                (_now_ms(), status, _json(stats), error, run_id),
            )

    def get_checkpoint(self, *, source_id: str, query_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT source_id, query_key, cursor, etag, updated_at
            FROM scraper_checkpoint
            WHERE source_id = ? AND query_key = ?
            """,
            (source_id, query_key),
        ).fetchone()
        return dict(row) if row is not None else None

    def set_checkpoint(
        self,
        *,
        source_id: str,
        query_key: str,
        cursor: str | None,
        etag: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO scraper_checkpoint
                    (source_id, query_key, cursor, etag, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, query_key) DO UPDATE SET
                    cursor = excluded.cursor,
                    etag = excluded.etag,
                    updated_at = excluded.updated_at
                """,
                (source_id, query_key, cursor, etag, _now_ms()),
            )

    def iter_listings(self, source_id: str) -> Iterable[Listing]:
        rows = self.conn.execute(
            "SELECT * FROM listing WHERE source_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
        for row in rows:
            yield _row_to_listing(row)

    def iter_listings_missing_geo(self, limit: int = 500) -> Iterable[Listing]:
        rows = self.conn.execute(
            """
            SELECT * FROM listing
            WHERE lat IS NULL OR lng IS NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            yield _row_to_listing(row)

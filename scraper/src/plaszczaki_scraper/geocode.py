"""Street-level geocoding queue and cache backed by Nominatim.

The queue and cache live in their own SQLite file under the scraper cache
directory. Keeping them out of the listing database means the web app remains
the single owner of the listing schema.

Only street/house-level answers are accepted: a city, village, county or
voivodeship match is never written back as a listing coordinate.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from plaszczaki_scraper.db import Database
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.normalize import normalize_text
from plaszczaki_scraper.ratelimit import HostRateLimiter
from plaszczaki_scraper.regions import RegionRegistry
from plaszczaki_scraper.robots import default_cache_dir

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "plaszczaki-scraper/0.1 (+https://github.com/zdziszkee/plaszczaki)"
TIMEOUT_SECONDS = 30.0
GEO_SOURCE = "geocoded_address"
PROVIDER = "nominatim"

ACCEPTED_ADDRESS_TYPES = frozenset({"house", "building", "road", "street", "residential"})
REJECTED_ADDRESS_TYPES = frozenset(
    {
        "city",
        "town",
        "village",
        "hamlet",
        "municipality",
        "county",
        "state",
        "postcode",
        "country",
        "administrative",
        "province",
        "region",
        "district",
        "borough",
        "suburb",
        "neighbourhood",
    }
)
_STREET_TOKENS = frozenset(
    {"ul", "ulica", "al", "aleja", "aleje", "os", "osiedle", "pl", "plac", "rondo"},
)

Fetcher = Callable[..., Any]
Evaluation = tuple[float, float, str, dict[str, Any]] | str

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    query TEXT PRIMARY KEY,
    lat REAL,
    lng REAL,
    provider TEXT,
    precision TEXT,
    display_name TEXT,
    result_json TEXT,
    created_at INT,
    updated_at INT
);

CREATE INDEX IF NOT EXISTS cache_updated_idx ON cache (updated_at);

CREATE TABLE IF NOT EXISTS job (
    id INTEGER PRIMARY KEY,
    source_id TEXT,
    external_id TEXT,
    address TEXT,
    query TEXT,
    status TEXT,
    attempts INT DEFAULT 0,
    error TEXT,
    created_at INT,
    updated_at INT,
    UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS job_status_idx ON job (status);
"""


def default_geocode_db_path() -> Path:
    """Return the geocoding queue/cache database path."""
    return default_cache_dir() / "geocode.db"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(str(value).split())
    return text or None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def build_address(listing: Listing) -> str | None:
    """Build a street-level query for a listing, or ``None`` when impossible.

    A listing with a street always yields a query. ``address_text`` is only
    used when it carries something more specific than the city, and only when
    it looks like a street (a house number or a ``ul.``/``al.`` style token).
    """
    street = _clean(listing.street)
    city = _clean(listing.city)
    voivodeship = _clean(listing.voivodeship)
    address_text = _clean(listing.address_text)

    if street is not None:
        parts: list[str] = []
        seen: set[str] = set()
        for part in (street, city, voivodeship):
            if part is None:
                continue
            key = normalize_text(part)
            if not key or key in seen:
                continue
            seen.add(key)
            parts.append(part)
        return ", ".join(parts)

    if address_text is None:
        return None

    normalized = normalize_text(address_text)
    if not normalized:
        return None

    city_key = normalize_text(city) if city is not None else ""
    if city_key and normalized == city_key:
        return None

    remainder = normalized
    if city_key:
        remainder = re.sub(rf"\b{re.escape(city_key)}\b", " ", normalized, count=1)
        remainder = " ".join(remainder.split())

    if not remainder:
        return None

    if voivodeship is not None and remainder == normalize_text(voivodeship):
        return None

    tokens = set(normalized.split())
    looks_like_street = bool(re.search(r"\d", address_text)) or bool(tokens & _STREET_TOKENS)
    if not looks_like_street:
        return None
    return address_text


class GeocodeStore:
    """SQLite-backed geocoding cache and job queue."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_geocode_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> GeocodeStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def enqueue(self, listing: Listing) -> bool:
        """Queue a listing for geocoding. Return ``True`` for a new job."""
        if listing.lat is not None and listing.lng is not None:
            return False

        address = build_address(listing)
        if address is None:
            return False

        now = _now_ms()
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO job
                    (source_id, external_id, address, query, status, attempts,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (listing.source_id, listing.external_id, address, address, now, now),
            )
        return cursor.rowcount > 0

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the oldest pending jobs."""
        if limit <= 0:
            return []
        rows = self.conn.execute(
            """
            SELECT id, source_id, external_id, address, query, status, attempts, error
            FROM job
            WHERE status = 'pending'
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def cache_get(self, query: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM cache WHERE query = ?",
            (query,),
        ).fetchone()
        if row is None:
            return None

        result: Any = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except json.JSONDecodeError:
                result = None

        return {
            "query": row["query"],
            "lat": row["lat"],
            "lng": row["lng"],
            "provider": row["provider"],
            "precision": row["precision"],
            "display_name": row["display_name"],
            "result": result,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def cache_put(self, query: str, result: dict[str, Any]) -> None:
        raw = result.get("result_json", result.get("raw", result.get("result")))
        if isinstance(raw, str):
            result_json = raw
        else:
            payload = raw if raw is not None else result
            result_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        now = _now_ms()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO cache
                    (query, lat, lng, provider, precision, display_name,
                     result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    lat = excluded.lat,
                    lng = excluded.lng,
                    provider = excluded.provider,
                    precision = excluded.precision,
                    display_name = excluded.display_name,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    query,
                    _to_float(result.get("lat")),
                    _to_float(result.get("lng", result.get("lon"))),
                    result.get("provider", PROVIDER),
                    result.get("precision"),
                    result.get("display_name"),
                    result_json,
                    now,
                    now,
                ),
            )

    def mark_done(self, job_id: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE job
                SET status = 'done', error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_now_ms(), job_id),
            )

    def mark_failed(self, job_id: int, error: str, retryable: bool = True) -> None:
        """Record a failure. Retryable failures stay queued for the next run."""
        status = "pending" if retryable else "failed"
        with self.conn:
            self.conn.execute(
                """
                UPDATE job
                SET status = ?, attempts = attempts + 1, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, _now_ms(), job_id),
            )

    def mark_rejected(self, job_id: int, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE job
                SET status = 'rejected', error = ?, updated_at = ?
                WHERE id = ?
                """,
                (reason, _now_ms(), job_id),
            )


def _default_fetch(
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> Any:
    return httpx.get(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    )


@dataclass(frozen=True, slots=True)
class GeocodeOutcome:
    """Result of one Nominatim lookup before it is persisted."""

    status: Literal["ok", "rejected", "failed"]
    lat: float | None = None
    lng: float | None = None
    precision: str | None = None
    item: dict[str, Any] | None = None
    error: str | None = None
    retryable: bool = True


class GeocodeWorker:
    """Drain the geocoding queue with Nominatim, one request per second."""

    def __init__(
        self,
        db: Database,
        store: GeocodeStore,
        regions: RegionRegistry,
        fetcher: Fetcher | None = None,
        rps: float = 1.0,
    ) -> None:
        self.db = db
        self.store = store
        self.regions = regions
        self.fetcher: Fetcher = fetcher or _default_fetch
        self.limiter = HostRateLimiter(rps)

    def run(self, limit: int = 100, dry_run: bool = False) -> dict[str, Any]:
        """Process up to ``limit`` pending jobs and return counters."""
        jobs = self.store.pending(limit)
        counts: dict[str, Any] = {
            "jobs": len(jobs),
            "cached": 0,
            "geocoded": 0,
            "rejected": 0,
            "failed": 0,
            "skipped_existing": 0,
            "missing_listing": 0,
            "requests": 0,
            "dry_run": dry_run,
        }

        for job in jobs:
            try:
                self._process(job, counts, dry_run)
            except Exception as exc:  # defensive: one bad job must not stop the batch
                logger.exception("geocode job %s failed unexpectedly", job.get("id"))
                if not dry_run:
                    self.store.mark_failed(
                        int(job["id"]),
                        f"{type(exc).__name__}: {exc}",
                        retryable=True,
                    )
                counts["failed"] += 1
        return counts

    def _process(
        self,
        job: dict[str, Any],
        counts: dict[str, Any],
        dry_run: bool,
    ) -> None:
        job_id = int(job["id"])
        listing = self.db.get_listing(str(job["source_id"]), str(job["external_id"]))
        if listing is None:
            if not dry_run:
                self.store.mark_rejected(job_id, "listing not found")
            counts["missing_listing"] += 1
            return

        if listing.lat is not None and listing.lng is not None:
            if not dry_run:
                self.store.mark_done(job_id)
            counts["skipped_existing"] += 1
            return

        query = _clean(job.get("query")) or _clean(job.get("address"))
        if query is None:
            query = build_address(listing)
        if query is None:
            if not dry_run:
                self.store.mark_rejected(job_id, "no street-level address")
            counts["rejected"] += 1
            return

        if int(job.get("attempts") or 0) >= 3:
            if not dry_run:
                self.store.mark_rejected(job_id, "too many failed attempts")
            counts["rejected"] += 1
            return

        cached = self.store.cache_get(query)
        if cached is not None and cached.get("lat") is not None and cached.get("lng") is not None:
            if not dry_run:
                self._apply(listing, float(cached["lat"]), float(cached["lng"]))
                self.store.mark_done(job_id)
            counts["cached"] += 1
            return

        counts["requests"] += 1
        outcome = self._lookup(listing, query)
        if outcome.status == "failed":
            if not dry_run:
                self.store.mark_failed(
                    job_id,
                    outcome.error or "geocoding failed",
                    retryable=outcome.retryable,
                )
            counts["failed"] += 1
            return

        if outcome.status == "rejected":
            if not dry_run:
                self.store.mark_rejected(job_id, outcome.error or "rejected")
            counts["rejected"] += 1
            return

        if outcome.lat is None or outcome.lng is None or outcome.item is None:
            if not dry_run:
                self.store.mark_rejected(job_id, "malformed result")
            counts["rejected"] += 1
            return

        item = outcome.item
        if not dry_run:
            self._apply(listing, outcome.lat, outcome.lng)
            self.store.cache_put(
                query,
                {
                    "lat": outcome.lat,
                    "lng": outcome.lng,
                    "provider": PROVIDER,
                    "precision": outcome.precision or "street",
                    "display_name": item.get("display_name"),
                    "raw": item,
                },
            )
            self.store.mark_done(job_id)
        counts["geocoded"] += 1

    def _lookup(self, listing: Listing, query: str) -> GeocodeOutcome:
        """Fetch and validate one query."""
        self.limiter.wait(NOMINATIM_URL)

        params: dict[str, Any] = {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 1,
            "countrycodes": "pl",
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "pl-PL,pl;q=0.9",
        }

        try:
            response = self.fetcher(
                NOMINATIM_URL,
                params=params,
                headers=headers,
                timeout=TIMEOUT_SECONDS,
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("geocode request failed: %s", error)
            return GeocodeOutcome(status="failed", error=error, retryable=True)

        status = int(getattr(response, "status_code", 200) or 200)
        if status >= 400:
            retryable = status == 429 or status >= 500
            logger.warning("geocode HTTP %s for %s", status, query)
            return GeocodeOutcome(
                status="failed",
                error=f"HTTP {status}",
                retryable=retryable,
            )

        try:
            payload = response.json()
        except Exception as exc:
            error = f"invalid JSON: {exc}"
            logger.warning("geocode %s", error)
            return GeocodeOutcome(status="failed", error=error, retryable=True)

        evaluation = self._evaluate(listing, payload)
        if isinstance(evaluation, str):
            logger.info("geocode rejected %r: %s", query, evaluation)
            return GeocodeOutcome(status="rejected", error=evaluation)

        lat, lng, precision, item = evaluation
        return GeocodeOutcome(
            status="ok",
            lat=lat,
            lng=lng,
            precision=precision,
            item=item,
        )

    def _evaluate(self, listing: Listing, payload: Any) -> Evaluation:
        if not isinstance(payload, list) or not payload:
            return "no result"
        if len(payload) > 1:
            return "ambiguous: multiple results"

        item = payload[0]
        if not isinstance(item, dict):
            return "malformed result"

        lat = _to_float(item.get("lat"))
        lng = _to_float(item.get("lon", item.get("lng")))
        if lat is None or lng is None:
            return "result without coordinates"

        address = item.get("address")
        if not isinstance(address, dict):
            address = {}

        country_code = str(address.get("country_code") or "").lower()
        if country_code and country_code != "pl":
            return f"outside Poland ({country_code})"

        address_type = str(item.get("addresstype") or item.get("type") or "").lower()
        if address_type in REJECTED_ADDRESS_TYPES:
            return f"not street-level ({address_type})"

        has_number = bool(address.get("house_number"))
        has_road = bool(address.get("road"))
        if address_type not in ACCEPTED_ADDRESS_TYPES and not (has_number or has_road):
            return f"not street-level ({address_type or 'unknown'})"

        containing = self._regions_for_point(lat, lng)
        if not containing:
            return "outside Małopolskie/Śląskie bbox"

        state_region = self._state_region(address.get("state"))
        if state_region is not None and state_region not in containing:
            return f"state {state_region} outside its bbox"

        expected = self.regions.canonical_voivodeship(listing.voivodeship)
        if expected is not None:
            if state_region is not None and state_region != expected:
                return f"region mismatch ({state_region} != {expected})"
            if expected not in containing:
                return f"outside {expected} bbox"

        precision = "house" if has_number else (address_type or "street")
        return lat, lng, precision, item

    def _regions_for_point(self, lat: float, lng: float) -> set[str]:
        matches: set[str] = set()
        for region in self.regions.config.regions.values():
            min_lon, min_lat, max_lon, max_lat = region.bbox
            if min_lon <= lng <= max_lon and min_lat <= lat <= max_lat:
                matches.add(region.id)
        return matches

    def _state_region(self, value: Any) -> str | None:
        if not value:
            return None
        text = " ".join(str(value).split())
        normalized = normalize_text(text)
        for prefix in ("wojewodztwo ", "woj "):
            if normalized.startswith(prefix) and " " in text:
                text = text.split(maxsplit=1)[1]
                break
        return self.regions.canonical_voivodeship(text)

    def _apply(self, listing: Listing, lat: float, lng: float) -> None:
        self.db.update_listing_geo(
            source_id=listing.source_id,
            external_id=listing.external_id,
            lat=lat,
            lng=lng,
            geo_source=GEO_SOURCE,
        )

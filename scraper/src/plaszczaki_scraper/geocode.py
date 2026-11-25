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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import httpx

from plaszczaki_scraper.canonical import canonical_key
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
_STREET_PREFIX_PATTERN = r"(?:(?:ul\.|ulica|al\.|aleja|aleje|os\.|osiedle|pl\.|plac|rondo)\s+)?"
POSTAL_CODE_RE = re.compile(r"(?<![\d-])\d{2}-\d{3}(?![\d-])")
_HOUSE_SUFFIX_RE = re.compile(r"\s+(\d+[A-Za-z]?)$")
_FLAT_SUFFIX_RE = re.compile(r"(?<=\d)\s*/\s*\d+[A-Za-z]?$")
# A fall-through variant only makes sense while the address is still incomplete.
_FINAL_REASON_PREFIXES = ("outside", "state ", "region mismatch")

Fetcher = Callable[..., Any]
Evaluation = tuple[float, float, str, dict[str, Any]] | str
# score, lat, lng, precision, item
Candidate = tuple[int, float, float, str, dict[str, Any]]

REQUEST_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 0.5

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
    auction_date INT,
    created_at INT,
    updated_at INT,
    UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS job_status_idx ON job (status);
"""

# Auctions are time sensitive, so pending work is drained earliest-auction first.
_PENDING_INDEX = "CREATE INDEX IF NOT EXISTS job_pending_idx ON job (status, auction_date, id)"


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
    """Build a human-readable query for a listing, or ``None`` when impossible.

    A listing with a street always yields a query, enriched with the building
    number kept in ``address_text`` when ``street`` carries none. Otherwise
    ``address_text`` is only used when it carries something more specific than
    the city, and only when it looks like a street (a house number or a
    ``ul.``/``al.`` style token).
    """
    street = _clean(listing.street)
    city = _clean(listing.city)
    voivodeship = _clean(listing.voivodeship)
    address_text = _clean(listing.address_text)

    if street is not None:
        street = _with_house_number(street, address_text)
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

    return _street_from_address_text(address_text, city, voivodeship)


@dataclass(frozen=True, slots=True)
class AddressQuery:
    """Structured Nominatim address parameters for one listing."""

    street: str | None = None
    city: str | None = None
    postalcode: str | None = None
    state: str | None = None


def build_address_query(
    listing: Listing,
    regions: RegionRegistry | None = None,
) -> AddressQuery | None:
    """Build structured address parameters, or ``None`` when impossible.

    ``street`` keeps the building number from ``address_text`` (flats are
    dropped, they do not change the coordinates); the state is the human
    readable region name when the registry knows the voivodeship.
    """
    street = _clean(listing.street)
    address_text = _clean(listing.address_text)
    city = _clean(listing.city)
    voivodeship = _clean(listing.voivodeship)

    if street is not None:
        street_line = _without_flat(_with_house_number(street, address_text))
    else:
        street_line = _street_from_address_text(address_text, city, voivodeship)
    if street_line is None:
        return None

    return AddressQuery(
        street=street_line,
        city=city,
        postalcode=_postal_code(address_text, listing.extra),
        state=_state_name(voivodeship, regions),
    )


def _street_from_address_text(
    address_text: str | None,
    city: str | None,
    voivodeship: str | None,
) -> str | None:
    """Return ``address_text`` when it looks like a street, not just a city."""
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

    # A bare postcode (or a postcode plus the city) is not a street.
    stripped = " ".join(POSTAL_CODE_RE.sub(" ", remainder).split())
    if not re.search(r"[^\W\d_]", stripped):
        return None
    return address_text


def _with_house_number(street: str, address_text: str | None) -> str:
    """Append the building number kept in ``address_text`` to a bare street."""
    street = " ".join(street.split())
    if re.search(r"\d", street) or not address_text:
        return street

    pattern = re.compile(
        rf"{_STREET_PREFIX_PATTERN}{re.escape(street)}\s*(\d+[A-Za-z]?)",
        re.IGNORECASE,
    )
    match = pattern.search(address_text)
    if match is None:
        return street
    return f"{street} {match.group(1)}"


def _without_flat(street: str) -> str:
    """Drop a trailing ``/flat`` number: flats share the building's point."""
    return _FLAT_SUFFIX_RE.sub("", street).strip() or street


def _split_street(street: str) -> tuple[str, str | None]:
    """Split ``"Partyzantów 24"`` into ``("Partyzantów", "24")``."""
    match = _HOUSE_SUFFIX_RE.search(street)
    if match is None:
        return street.strip(), None
    return street[: match.start()].strip(), match.group(1)


def _postal_code(address_text: str | None, extra: dict[str, Any]) -> str | None:
    if address_text:
        match = POSTAL_CODE_RE.search(address_text)
        if match is not None:
            return match.group(0)

    raw = _clean(str(extra.get("postal_code"))) if extra.get("postal_code") else None
    if raw is not None and POSTAL_CODE_RE.fullmatch(raw):
        return raw
    return None


def _state_name(voivodeship: str | None, regions: RegionRegistry | None = None) -> str | None:
    if voivodeship is None:
        return None
    registry = regions if regions is not None else _default_registry()
    if registry is not None:
        region_id = registry.canonical_voivodeship(voivodeship)
        if region_id is not None:
            return registry.region(region_id).name
    return voivodeship


_REGISTRY: RegionRegistry | None = None
_REGISTRY_RESOLVED = False


def _default_registry() -> RegionRegistry | None:
    """Lazily load the region registry; geocoding works without it."""
    global _REGISTRY, _REGISTRY_RESOLVED
    if not _REGISTRY_RESOLVED:
        _REGISTRY_RESOLVED = True
        try:
            _REGISTRY = RegionRegistry.load()
        except Exception:  # no config in this environment: fall back to raw names
            logger.debug("regions config unavailable for geocoding", exc_info=True)
            _REGISTRY = None
    return _REGISTRY


def _query_variants(address: AddressQuery) -> list[dict[str, Any]]:
    """Most specific first: house number + postcode, then road level."""
    street = _clean(address.street)
    if street is None:
        return []

    street = _without_flat(street)
    road, _number = _split_street(street)
    city = _clean(address.city)
    postalcode = _clean(address.postalcode)
    state = _clean(address.state)

    variants: list[dict[str, Any]] = []
    for street_value, variant_postalcode in (
        (street, postalcode),
        (street, None),
        (road, postalcode),
        (road, None),
    ):
        params = _nominatim_params(
            street=street_value,
            city=city,
            postalcode=variant_postalcode,
            state=state,
        )
        if params not in variants:
            variants.append(params)
    return variants


def _nominatim_params(
    *,
    street: str | None,
    city: str | None,
    postalcode: str | None,
    state: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 5,
        "countrycodes": "pl",
        "country": "Polska",
    }
    if street:
        params["street"] = street
    if city:
        params["city"] = city
    if postalcode:
        params["postalcode"] = postalcode
    if state:
        params["state"] = state
    return params


def _candidate_score(
    address_type: str,
    house_number: str | None,
    road: str | None,
    details: dict[str, Any],
    address: AddressQuery | None,
) -> int:
    """Rank candidates: exact house number, road, city and postcode win."""
    score = 1 if address_type in ACCEPTED_ADDRESS_TYPES else 0
    if house_number is not None:
        score += 2
    if road is not None:
        score += 1
    if address is None:
        return score

    requested_road, requested_number = _split_street(address.street or "")
    if (
        house_number is not None
        and requested_number is not None
        and normalize_text(house_number) == normalize_text(requested_number)
    ):
        score += 3
    if road is not None and requested_road and _road_key(road) == _road_key(requested_road):
        score += 3
    if address.city and _address_city(details) == normalize_text(address.city):
        score += 3
    if address.postalcode and normalize_text(str(details.get("postcode") or "")) == (
        normalize_text(address.postalcode)
    ):
        score += 2
    return score


def _address_city(details: dict[str, Any]) -> str:
    """The settlement name Nominatim uses for the result."""
    for key in ("city", "town", "village", "municipality", "hamlet"):
        value = _clean(str(details.get(key) or ""))
        if value is not None:
            return normalize_text(value)
    return ""


def _road_key(value: str | None) -> str:
    if not value:
        return ""
    tokens = normalize_text(value).split()
    while tokens and tokens[0] in _STREET_TOKENS:
        tokens.pop(0)
    return " ".join(tokens)


def _try_next_variant(reason: str) -> bool:
    """Only incomplete or non-street answers justify a weaker variant."""
    return not reason.startswith(_FINAL_REASON_PREFIXES)


def _summarise_reasons(reasons: list[str]) -> str:
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return "; ".join(unique) or "no result"


def _backoff(attempt: int) -> None:
    if RETRY_BACKOFF_SECONDS > 0 and attempt < REQUEST_ATTEMPTS:
        time.sleep(RETRY_BACKOFF_SECONDS)


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
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self) -> None:
        """Add columns shipped after the queue database was first created."""
        columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(job)").fetchall()
        }
        if "auction_date" not in columns:
            self.conn.execute("ALTER TABLE job ADD COLUMN auction_date INT")
        self.conn.execute(_PENDING_INDEX)

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
                     auction_date, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                """,
                (
                    listing.source_id,
                    listing.external_id,
                    address,
                    address,
                    listing.auction_date,
                    now,
                    now,
                ),
            )
            inserted = cursor.rowcount > 0
            if not inserted and listing.auction_date is not None:
                # Backfill the priority of jobs queued before the column existed.
                self.conn.execute(
                    """
                    UPDATE job
                    SET auction_date = ?
                    WHERE source_id = ? AND external_id = ?
                      AND auction_date IS NULL
                    """,
                    (listing.auction_date, listing.source_id, listing.external_id),
                )
        return inserted

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return pending jobs, earliest auction first and undated ones last."""
        if limit <= 0:
            return []
        rows = self.conn.execute(
            """
            SELECT id, source_id, external_id, address, query, status, attempts,
                   error, auction_date
            FROM job
            WHERE status = 'pending'
            ORDER BY auction_date IS NULL, auction_date, id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_query(self, job_id: int, query: str) -> None:
        """Store the query rebuilt from the current listing."""
        with self.conn:
            self.conn.execute(
                """
                UPDATE job
                SET address = ?, query = ?, updated_at = ?
                WHERE id = ?
                """,
                (query, query, _now_ms(), job_id),
            )

    def requeue_rejected(self) -> int:
        """Move every rejected job back to the pending queue."""
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE job
                SET status = 'pending', attempts = 0, error = NULL, updated_at = ?
                WHERE status = 'rejected'
                """,
                (_now_ms(),),
            )
        return cursor.rowcount

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
    requests: int = 0


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

        # Rebuild from the current listing: the stored query is only a snapshot
        # from the moment the job was queued.
        address = build_address_query(listing, self.regions)
        query = build_address(listing)
        if address is None or query is None:
            if not dry_run:
                self.store.mark_rejected(job_id, "no street-level address")
            counts["rejected"] += 1
            return

        if not dry_run and _clean(job.get("query")) != query:
            self.store.update_query(job_id, query)

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

        outcome = self._lookup(listing, query, address)
        counts["requests"] += outcome.requests
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

    def _lookup(
        self,
        listing: Listing,
        query: str,
        address: AddressQuery | None = None,
    ) -> GeocodeOutcome:
        """Try the structured query variants until one yields a street point."""
        if address is None:
            address = build_address_query(listing, self.regions) or AddressQuery(street=query)
        variants = _query_variants(address)
        if not variants:
            return GeocodeOutcome(status="rejected", error="no street-level address")

        requests = 0
        reasons: list[str] = []
        for params in variants:
            payload, failure, attempts = self._request(params)
            requests += attempts
            if failure is not None:
                return replace(failure, requests=requests)

            evaluation = self._evaluate(listing, payload, address)
            if not isinstance(evaluation, str):
                lat, lng, precision, item = evaluation
                return GeocodeOutcome(
                    status="ok",
                    lat=lat,
                    lng=lng,
                    precision=precision,
                    item=item,
                    requests=requests,
                )

            reasons.append(evaluation)
            if not _try_next_variant(evaluation):
                break

        reason = _summarise_reasons(reasons)
        logger.info("geocode rejected %r: %s", query, reason)
        return GeocodeOutcome(status="rejected", error=reason, requests=requests)

    def _request(self, params: dict[str, Any]) -> tuple[Any, GeocodeOutcome | None, int]:
        """Fetch one variant, retrying transient transport/HTTP failures."""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "pl-PL,pl;q=0.9",
        }

        failure: GeocodeOutcome | None = None
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            self.limiter.wait(NOMINATIM_URL)
            try:
                response = self.fetcher(
                    NOMINATIM_URL,
                    params=params,
                    headers=headers,
                    timeout=TIMEOUT_SECONDS,
                )
            except Exception as exc:
                failure = GeocodeOutcome(
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    retryable=True,
                )
                logger.warning("geocode request failed: %s", failure.error)
                _backoff(attempt)
                continue

            status = int(getattr(response, "status_code", 200) or 200)
            if status >= 400:
                retryable = status == 429 or status >= 500
                failure = GeocodeOutcome(
                    status="failed",
                    error=f"HTTP {status}",
                    retryable=retryable,
                )
                logger.warning("geocode HTTP %s for %s", status, params.get("street"))
                if not retryable:
                    return None, failure, attempt
                _backoff(attempt)
                continue

            try:
                return response.json(), None, attempt
            except Exception as exc:
                failure = GeocodeOutcome(
                    status="failed",
                    error=f"invalid JSON: {exc}",
                    retryable=True,
                )
                logger.warning("geocode %s", failure.error)
                _backoff(attempt)

        return None, failure, REQUEST_ATTEMPTS

    def _evaluate(
        self,
        listing: Listing,
        payload: Any,
        address: AddressQuery | None = None,
    ) -> Evaluation:
        """Pick the best street-level candidate, ignoring the weaker ones."""
        if not isinstance(payload, list) or not payload:
            return "no result"

        candidates: list[Candidate] = []
        reasons: list[str] = []
        for item in payload:
            candidate = self._candidate(listing, item, address)
            if isinstance(candidate, str):
                reasons.append(candidate)
                continue
            candidates.append(candidate)

        if not candidates:
            return _summarise_reasons(reasons)

        best = max(candidate[0] for candidate in candidates)
        winners = [candidate for candidate in candidates if candidate[0] == best]
        # Several OSM objects for the same spot (building + road) are not ambiguous.
        points = {(round(lat, 5), round(lng, 5)) for _, lat, lng, _, _ in winners}
        if len(points) > 1:
            return f"ambiguous: {len(winners)} similar results"

        _, lat, lng, precision, item = winners[0]
        return lat, lng, precision, item

    def _candidate(
        self,
        listing: Listing,
        item: Any,
        address: AddressQuery | None,
    ) -> Candidate | str:
        """Score one Nominatim result, or explain why it is unusable."""
        if not isinstance(item, dict):
            return "malformed result"

        lat = _to_float(item.get("lat"))
        lng = _to_float(item.get("lon", item.get("lng")))
        if lat is None or lng is None:
            return "result without coordinates"

        raw_address = item.get("address")
        details = raw_address if isinstance(raw_address, dict) else {}

        country_code = str(details.get("country_code") or "").lower()
        if country_code and country_code != "pl":
            return f"outside Poland ({country_code})"

        address_type = str(item.get("addresstype") or item.get("type") or "").lower()
        if address_type in REJECTED_ADDRESS_TYPES:
            return f"not street-level ({address_type})"

        house_number = _clean(str(details.get("house_number") or ""))
        road = _clean(str(details.get("road") or ""))
        if address_type not in ACCEPTED_ADDRESS_TYPES and house_number is None and road is None:
            return f"not street-level ({address_type or 'unknown'})"

        containing = self._regions_for_point(lat, lng)
        if not containing:
            return "outside Małopolskie/Śląskie bbox"

        state_region = self._state_region(details.get("state"))
        if state_region is not None and state_region not in containing:
            return f"state {state_region} outside its bbox"

        expected = self.regions.canonical_voivodeship(listing.voivodeship)
        if expected is not None:
            if state_region is not None and state_region != expected:
                return f"region mismatch ({state_region} != {expected})"
            if expected not in containing:
                return f"outside {expected} bbox"

        score = _candidate_score(address_type, house_number, road, details, address)
        precision = "house" if house_number else (address_type or "street")
        return score, lat, lng, precision, item

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
        located = replace(listing, lat=lat, lng=lng, geo_source=GEO_SOURCE)
        self.db.update_listing_geo(
            source_id=listing.source_id,
            external_id=listing.external_id,
            lat=lat,
            lng=lng,
            geo_source=GEO_SOURCE,
            canonical_key=canonical_key(located),
        )

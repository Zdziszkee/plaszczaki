from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.geocode import (
    GEO_SOURCE,
    NOMINATIM_URL,
    USER_AGENT,
    AddressQuery,
    GeocodeStore,
    GeocodeWorker,
    build_address,
    build_address_query,
    default_geocode_db_path,
)
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry


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


def _listing(**overrides: Any) -> Listing:
    values: dict[str, Any] = {
        "source_id": "otodom",
        "external_id": "123",
        "url": "https://example.test/123",
        "title": "Test flat",
        "offer_type": "sale",
        "property_type": "flat",
        "voivodeship": "malopolskie",
        "city": "Kraków",
        "street": "ul. Długa 5",
    }
    values.update(overrides)
    return Listing(**values)


def _seed(db: Database, listing: Listing) -> None:
    db.upsert_source(
        source_id=listing.source_id,
        label="Otodom",
        homepage_url="https://www.otodom.pl",
        adapter="otodom",
    )
    db.upsert_listing(listing)


def _registry() -> RegionRegistry:
    return RegionRegistry.load()


def _worker(
    db: Database,
    store: GeocodeStore,
    fetcher: Any = None,
) -> GeocodeWorker:
    return GeocodeWorker(
        db,
        store,
        _registry(),
        fetcher=fetcher,
        rps=1_000_000.0,
    )


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", NOMINATIM_URL),
                response=httpx.Response(self.status_code),
            )


class RecordingFetcher:
    def __init__(
        self,
        payload: Any = None,
        *,
        payloads: list[Any] | None = None,
        status_code: int = 200,
        exc: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.payloads = payloads
        self.status_code = status_code
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout},
        )
        if self.exc is not None:
            raise self.exc
        if self.payloads is not None:
            index = min(len(self.calls) - 1, len(self.payloads) - 1)
            return FakeResponse(self.payloads[index], self.status_code)
        return FakeResponse(self.payload, self.status_code)


def _house_payload(
    *,
    lat: float = 50.0614,
    lon: float = 19.9366,
    state: str = "województwo małopolskie",
) -> list[dict[str, Any]]:
    return [
        {
            "place_id": 1,
            "lat": str(lat),
            "lon": str(lon),
            "display_name": "ul. Długa 5, Kraków, małopolskie, Polska",
            "addresstype": "house",
            "type": "house",
            "address": {
                "house_number": "5",
                "road": "Długa",
                "city": "Kraków",
                "state": state,
                "postcode": "31-147",
                "country": "Polska",
                "country_code": "pl",
            },
        },
    ]


def _city_payload() -> list[dict[str, Any]]:
    return [
        {
            "place_id": 2,
            "lat": "50.0614",
            "lon": "19.9366",
            "display_name": "Kraków, małopolskie, Polska",
            "addresstype": "city",
            "type": "city",
            "address": {
                "city": "Kraków",
                "state": "województwo małopolskie",
                "country": "Polska",
                "country_code": "pl",
            },
        },
    ]


def _warsaw_payload() -> list[dict[str, Any]]:
    return [
        {
            "place_id": 3,
            "lat": "52.2297",
            "lon": "21.0122",
            "display_name": "ul. Marszałkowska 1, Warszawa, mazowieckie, Polska",
            "addresstype": "house",
            "type": "house",
            "address": {
                "house_number": "1",
                "road": "Marszałkowska",
                "city": "Warszawa",
                "state": "województwo mazowieckie",
                "country": "Polska",
                "country_code": "pl",
            },
        },
    ]


def _brzesko_payload() -> list[dict[str, Any]]:
    return [
        {
            "place_id": 11,
            "lat": "49.8211",
            "lon": "20.6114",
            "display_name": "Partyzantów 24, Brzesko, małopolskie, Polska",
            "addresstype": "building",
            "type": "building",
            "address": {
                "house_number": "24",
                "road": "Partyzantów",
                "city": "Brzesko",
                "state": "województwo małopolskie",
                "postcode": "32-800",
                "country": "Polska",
                "country_code": "pl",
            },
        },
    ]


def _ambiguous_payload() -> list[dict[str, Any]]:
    """Two same-road candidates in different places: no unique best score."""
    return [
        {
            "place_id": 21,
            "lat": "50.0614",
            "lon": "19.9366",
            "display_name": "Partyzantów, Kraków, małopolskie, Polska",
            "addresstype": "residential",
            "type": "residential",
            "address": {
                "road": "Partyzantów",
                "city": "Kraków",
                "state": "województwo małopolskie",
                "country": "Polska",
                "country_code": "pl",
            },
        },
        {
            "place_id": 22,
            "lat": "50.0712",
            "lon": "19.9501",
            "display_name": "Partyzantów, Kraków, małopolskie, Polska",
            "addresstype": "residential",
            "type": "residential",
            "address": {
                "road": "Partyzantów",
                "city": "Kraków",
                "state": "województwo małopolskie",
                "country": "Polska",
                "country_code": "pl",
            },
        },
    ]


def _brzesko_listing() -> Listing:
    return _listing(
        street="Partyzantów",
        address_text="Partyzantów 24/59, 32-800",
        city="Brzesko",
    )


def test_build_address_prefers_street() -> None:
    assert build_address(_listing()) == "ul. Długa 5, Kraków, malopolskie"


def test_build_address_rejects_city_only() -> None:
    assert build_address(_listing(street=None)) is None
    assert build_address(_listing(street=None, address_text="Kraków")) is None
    assert build_address(_listing(street=None, address_text="Kraków, małopolskie")) is None
    assert (
        build_address(
            _listing(street=None, city=None, voivodeship=None, address_text="Wieliczka"),
        )
        is None
    )


def test_build_address_uses_street_like_address_text() -> None:
    assert (
        build_address(_listing(street=None, address_text="ul. Długa 5, Kraków"))
        == "ul. Długa 5, Kraków"
    )
    assert build_address(_listing(street=None, address_text="Kraków, Długa 5")) == "Kraków, Długa 5"


def test_build_address_rejects_postcode_only_address_text() -> None:
    assert build_address(_listing(street=None, city=None, address_text="32-800")) is None
    assert (
        build_address(_listing(street=None, city="Trzebinia", address_text="32-540 Trzebinia"))
        is None
    )


def test_build_address_extracts_house_number_from_address_text() -> None:
    listing = _brzesko_listing()

    assert build_address(listing) == "Partyzantów 24, Brzesko, malopolskie"


def test_build_address_query_uses_structured_fields() -> None:
    assert build_address_query(_brzesko_listing()) == AddressQuery(
        street="Partyzantów 24",
        city="Brzesko",
        postalcode="32-800",
        state="małopolskie",
    )


def test_build_address_query_drops_the_flat_number() -> None:
    listing = _brzesko_listing()
    listing.street = "Partyzantów 24/59"

    query = build_address_query(listing)

    assert query is not None
    assert query.street == "Partyzantów 24"


def test_build_address_query_reads_postal_code_from_extra() -> None:
    listing = _listing(street="ul. Długa 5", extra={"postal_code": "31-147"})

    query = build_address_query(listing)

    assert query is not None
    assert query.postalcode == "31-147"


def test_build_address_query_falls_back_to_the_raw_voivodeship() -> None:
    listing = _listing(voivodeship="mazowieckie")

    query = build_address_query(listing)

    assert query is not None
    assert query.state == "mazowieckie"


def test_enqueue_skips_city_only_and_deduplicates(tmp_path: Path) -> None:
    listing = _listing()
    with GeocodeStore(tmp_path / "geocode.db") as store:
        assert store.enqueue(listing) is True
        assert store.enqueue(listing) is False
        assert store.enqueue(_listing(street=None)) is False

        jobs = store.pending(10)
        assert len(jobs) == 1
        assert jobs[0]["status"] == "pending"
        assert jobs[0]["source_id"] == "otodom"
        assert jobs[0]["external_id"] == "123"
        assert jobs[0]["query"] == build_address(listing)


def test_cache_hit_updates_listing_without_network(tmp_path: Path) -> None:
    listing = _listing()
    query = build_address(listing)
    assert query is not None
    fetcher = RecordingFetcher(_house_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)
        store.cache_put(
            query,
            {
                "lat": 50.0614,
                "lng": 19.9366,
                "provider": "nominatim",
                "precision": "house",
                "display_name": "ul. Długa 5, Kraków",
            },
        )

        counts = _worker(db, store, fetcher).run()

        assert counts["cached"] == 1
        assert counts["requests"] == 0
        assert fetcher.calls == []

        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat == 50.0614
        assert stored.lng == 19.9366
        assert stored.geo_source == GEO_SOURCE
        assert store.pending(10) == []


def test_accepts_house_result_and_updates_listing(tmp_path: Path) -> None:
    listing = _listing()
    query = build_address(listing)
    assert query is not None
    fetcher = RecordingFetcher(_house_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["jobs"] == 1
        assert counts["geocoded"] == 1
        assert counts["rejected"] == 0
        assert counts["failed"] == 0
        assert counts["requests"] == 1

        call = fetcher.calls[0]
        assert call["url"] == NOMINATIM_URL
        assert call["params"]["street"] == "ul. Długa 5"
        assert call["params"]["city"] == "Kraków"
        assert call["params"]["state"] == "małopolskie"
        assert call["params"]["country"] == "Polska"
        assert call["params"]["format"] == "jsonv2"
        assert call["params"]["addressdetails"] == 1
        assert call["params"]["limit"] == 5
        assert call["params"]["countrycodes"] == "pl"
        assert "q" not in call["params"]
        assert "postalcode" not in call["params"]
        assert call["headers"]["User-Agent"] == USER_AGENT
        assert call["timeout"] == 30.0

        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat == 50.0614
        assert stored.lng == 19.9366
        assert stored.geo_source == GEO_SOURCE

        cached = store.cache_get(query)
        assert cached is not None
        assert cached["lat"] == 50.0614
        assert cached["precision"] == "house"
        assert store.pending(10) == []


def test_rejects_city_level_result(tmp_path: Path) -> None:
    listing = _listing()
    fetcher = RecordingFetcher(_city_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["rejected"] == 1
        assert counts["geocoded"] == 0
        # House number, then road level: a non-street answer falls through.
        assert counts["requests"] == 2

        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat is None
        assert stored.lng is None
        assert store.cache_get(build_address(listing) or "") is None
        assert store.pending(10) == []


def test_rejects_result_outside_scope(tmp_path: Path) -> None:
    listing = _listing()
    fetcher = RecordingFetcher(_warsaw_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["rejected"] == 1
        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat is None
        assert store.pending(10) == []


def test_rejects_result_from_wrong_voivodeship(tmp_path: Path) -> None:
    listing = _listing(
        voivodeship="slaskie",
        city="Katowice",
        street="ul. Mariacka 5",
    )
    # Tarnów is inside Małopolskie, outside Śląskie.
    fetcher = RecordingFetcher(_house_payload(lat=50.0121, lon=20.9858))

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["rejected"] == 1
        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat is None


def test_transient_error_stays_pending_for_retry(tmp_path: Path) -> None:
    listing = _listing()
    fetcher = RecordingFetcher(exc=httpx.ConnectError("connection refused"))

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["failed"] == 1
        assert counts["geocoded"] == 0
        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat is None

        jobs = store.pending(10)
        assert len(jobs) == 1
        assert jobs[0]["attempts"] == 1
        assert "ConnectError" in (jobs[0].get("error") or "")


def test_existing_coordinates_are_not_overwritten(tmp_path: Path) -> None:
    listing = _listing(lat=50.0, lng=19.0, geo_source="source")
    fetcher = RecordingFetcher(_house_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        assert store.enqueue(listing) is False
        assert store.pending(10) == []

        counts = _worker(db, store, fetcher).run()

        assert counts["jobs"] == 0
        assert counts["requests"] == 0
        assert fetcher.calls == []
        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat == 50.0
        assert stored.lng == 19.0
        assert stored.geo_source == "source"
        assert store.cache_get(build_address(listing) or "") is None


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    listing = _listing()
    fetcher = RecordingFetcher(_house_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run(dry_run=True)

        assert counts["geocoded"] == 1
        assert counts["dry_run"] is True
        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat is None
        assert store.cache_get(build_address(listing) or "") is None
        assert len(store.pending(10)) == 1


def test_default_store_lives_in_cache_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLASZCZAKI_CACHE_DIR", str(tmp_path))
    assert default_geocode_db_path() == tmp_path / "geocode.db"

    with GeocodeStore() as store:
        assert store.path == tmp_path / "geocode.db"
        assert store.path.is_file()


def test_komornik_style_address_uses_a_structured_query(tmp_path: Path) -> None:
    listing = _brzesko_listing()
    fetcher = RecordingFetcher(_brzesko_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["geocoded"] == 1
        assert counts["requests"] == 1

        params = fetcher.calls[0]["params"]
        assert params["street"] == "Partyzantów 24"
        assert params["city"] == "Brzesko"
        assert params["postalcode"] == "32-800"
        assert params["state"] == "małopolskie"

        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat == 49.8211
        assert stored.lng == 20.6114


def test_ambiguous_result_falls_through_to_the_next_variant(tmp_path: Path) -> None:
    listing = _brzesko_listing()
    fetcher = RecordingFetcher(payloads=[_ambiguous_payload(), _brzesko_payload()])

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["geocoded"] == 1
        assert counts["rejected"] == 0
        assert counts["requests"] == 2
        assert "postalcode" in fetcher.calls[0]["params"]
        assert "postalcode" not in fetcher.calls[1]["params"]
        assert fetcher.calls[1]["params"]["street"] == "Partyzantów 24"

        stored = db.get_listing("otodom", "123")
        assert stored is not None
        assert stored.lat == 49.8211


def test_road_level_variant_follows_a_rejected_house_number(tmp_path: Path) -> None:
    listing = _brzesko_listing()
    road_payload: list[dict[str, Any]] = [
        {
            "place_id": 31,
            "lat": "49.8213",
            "lon": "20.6120",
            "display_name": "Partyzantów, Brzesko, małopolskie, Polska",
            "addresstype": "road",
            "type": "road",
            "address": {
                "road": "Partyzantów",
                "city": "Brzesko",
                "state": "województwo małopolskie",
                "country": "Polska",
                "country_code": "pl",
            },
        },
    ]
    fetcher = RecordingFetcher(payloads=[[], [], road_payload])

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["geocoded"] == 1
        assert counts["requests"] == 3
        assert fetcher.calls[0]["params"]["street"] == "Partyzantów 24"
        assert fetcher.calls[1]["params"]["street"] == "Partyzantów 24"
        assert fetcher.calls[2]["params"]["street"] == "Partyzantów"


def test_region_rejection_does_not_retry_weaker_variants(tmp_path: Path) -> None:
    listing = _brzesko_listing()
    fetcher = RecordingFetcher(_warsaw_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)

        counts = _worker(db, store, fetcher).run()

        assert counts["rejected"] == 1
        assert len(fetcher.calls) == 1


def test_pending_orders_earliest_auction_first(tmp_path: Path) -> None:
    with GeocodeStore(tmp_path / "geocode.db") as store:
        store.enqueue(_listing(external_id="late", auction_date=2_000))
        store.enqueue(_listing(external_id="undated"))
        store.enqueue(_listing(external_id="early", auction_date=1_000))

        jobs = store.pending(10)

        assert [job["external_id"] for job in jobs] == ["early", "late", "undated"]
        assert [job["auction_date"] for job in jobs] == [1_000, 2_000, None]


def test_pending_limit_keeps_the_earliest_auction(tmp_path: Path) -> None:
    with GeocodeStore(tmp_path / "geocode.db") as store:
        store.enqueue(_listing(external_id="late", auction_date=9_000))
        store.enqueue(_listing(external_id="early", auction_date=1_000))

        jobs = store.pending(1)

        assert [job["external_id"] for job in jobs] == ["early"]


def test_requeue_rejected_resets_the_job(tmp_path: Path) -> None:
    listing = _listing()
    with GeocodeStore(tmp_path / "geocode.db") as store:
        store.enqueue(listing)
        job_id = int(store.pending(1)[0]["id"])
        store.mark_rejected(job_id, "no result")
        assert store.pending(1) == []

        assert store.requeue_rejected() == 1

        jobs = store.pending(1)
        assert len(jobs) == 1
        assert jobs[0]["status"] == "pending"
        assert jobs[0]["attempts"] == 0
        assert jobs[0]["error"] is None


def test_stale_job_query_is_rebuilt_from_the_listing(tmp_path: Path) -> None:
    listing = _brzesko_listing()
    fetcher = RecordingFetcher(_brzesko_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)
        with store.conn:
            store.conn.execute("UPDATE job SET query = 'Partyzantów, Brzesko, malopolskie'")

        counts = _worker(db, store, fetcher).run()

        assert counts["geocoded"] == 1
        row = store.conn.execute("SELECT query, address FROM job").fetchone()
        assert row is not None
        assert row["query"] == "Partyzantów 24, Brzesko, malopolskie"
        assert row["address"] == row["query"]
        assert fetcher.calls[0]["params"]["street"] == "Partyzantów 24"


def test_geocoding_refreshes_the_canonical_key(tmp_path: Path) -> None:
    listing = _listing()
    fetcher = RecordingFetcher(_house_payload())

    with _database(tmp_path) as db, GeocodeStore(tmp_path / "geocode.db") as store:
        _seed(db, listing)
        store.enqueue(listing)
        before = db.conn.execute("SELECT canonical_key FROM listing").fetchone()
        assert before is not None
        assert before["canonical_key"].startswith("addr:")

        _worker(db, store, fetcher).run()

        after = db.conn.execute("SELECT canonical_key FROM listing").fetchone()
        assert after is not None
        assert after["canonical_key"] == "geo:50.0614:19.9366:-:-:sale:flat"


def test_store_migrates_an_existing_queue(tmp_path: Path) -> None:
    path = tmp_path / "geocode.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE cache (
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
        CREATE TABLE job (
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
        """,
    )
    connection.execute(
        """
        INSERT INTO job (source_id, external_id, address, query, status)
        VALUES ('otodom', '123', 'ul. Długa 5, Kraków, malopolskie', 'stale', 'pending')
        """,
    )
    connection.commit()
    connection.close()

    with GeocodeStore(path) as store:
        columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(job)")}
        assert "auction_date" in columns

        # Re-enqueueing backfills the priority of jobs queued before the column.
        assert store.enqueue(_listing(auction_date=1_234)) is False
        jobs = store.pending(1)
        assert jobs[0]["auction_date"] == 1_234
        assert jobs[0]["query"] == "stale"

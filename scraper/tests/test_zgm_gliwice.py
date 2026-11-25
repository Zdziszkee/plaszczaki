from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from plaszczaki_scraper.adapters.base import Query
from plaszczaki_scraper.adapters.zgm_gliwice import SEARCH_URL, ZgmGliwiceAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "zgm-gliwice"
PDFS = Path(__file__).parent / "fixtures" / "pdf"
WARSAW = ZoneInfo("Europe/Warsaw")

COMMERCIAL_URL = (
    "https://zgm-gliwice.pl/wp-content/uploads/2026/11/"
    "wykaz-nr-ZGM.DS_.255.2026-Dunikowskiego-16.I-przetarg.pdf"
)
FLAT_URL = (
    "https://zgm-gliwice.pl/wp-content/uploads/2026/11/"
    "wykaz-nr-ZGM.DS_.158.2026-Blogoslawionego-Czeslawa-5.9-przetarg.pdf"
)


class _Response:
    def __init__(self, body: bytes = b"", *, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.headers: dict[str, str] = {}


class _Client:
    def __init__(self, files: dict[str, Path]) -> None:
        self.files = files
        self.urls: list[str] = []

    def get(self, url: str, **_: object) -> _Response:
        self.urls.append(url)
        path = self.files.get(url)
        if path is None:
            return _Response(status=404)
        return _Response(path.read_bytes())


def _adapter(client: _Client, cache_dir: Path) -> ZgmGliwiceAdapter:
    return ZgmGliwiceAdapter(
        source_id="zgm-gliwice",
        regions=RegionRegistry(load_regions_config()),
        client=client,
        cache_dir=cache_dir,
    )


def _client() -> _Client:
    return _Client(
        {
            COMMERCIAL_URL: PDFS / "wykaz-commercial.pdf",
            FLAT_URL: PDFS / "wykaz-flat.pdf",
        },
    )


def _query(adapter: ZgmGliwiceAdapter, property_type: str) -> Query:
    return next(
        query for query in adapter.queries(("slaskie",)) if query.property_type == property_type
    )


def _search(html: str, property_type: str, tmp_path: Path) -> tuple[list[Listing], _Client]:
    client = _client()
    adapter = _adapter(client, tmp_path)
    page = adapter.parse_search(
        (FIXTURES / html).read_text(encoding="utf-8"),
        _query(adapter, property_type),
    )
    return page.items, client


def test_queries_cover_slaskie_only() -> None:
    adapter = _adapter(_client(), Path("/tmp/unused"))

    queries = adapter.queries(("malopolskie", "slaskie"))

    assert [query.key for query in queries] == [
        "slaskie:wykaz:flat",
        "slaskie:wykaz:commercial",
    ]
    assert {query.url for query in queries} == {SEARCH_URL}
    assert {query.offer_type for query in queries} == {"auction"}
    assert adapter.queries(("malopolskie",)) == []


def test_with_page_keeps_the_unpaginated_url() -> None:
    adapter = _adapter(_client(), Path("/tmp/unused"))
    query = _query(adapter, "flat")

    paged = adapter.with_page(query, 2)

    assert paged.url == query.url
    assert paged.page == 2


def test_parse_search_reads_the_lokal_row(tmp_path: Path) -> None:
    items, client = _search("search.html", "commercial", tmp_path)

    assert len(items) == 1
    listing = items[0]
    assert listing.source_id == "zgm-gliwice"
    assert listing.external_id == "ZGM/DS/255/2026#I"
    assert listing.url == COMMERCIAL_URL
    assert listing.offer_type == "auction"
    assert listing.property_type == "commercial"
    assert listing.market == "secondary"
    assert listing.price_kind == "starting"
    assert listing.price_unit == "total"
    assert listing.price_pln == 241_600
    assert listing.area_m2 == 37.29
    assert listing.city == "Gliwice"
    assert listing.voivodeship == "slaskie"
    assert listing.street == "Ksawerego Dunikowskiego"
    assert listing.address_text == "Ksawerego Dunikowskiego 16, Gliwice"
    assert listing.case_number == "ZGM/DS/255/2026"
    assert listing.seller_type == "government"
    assert listing.auction_date is None
    assert listing.lat is None and listing.lng is None
    assert listing.published_at == int(datetime(2026, 11, 5, tzinfo=WARSAW).timestamp() * 1000)
    assert listing.extra["pdf"]["url"] == COMMERCIAL_URL
    assert listing.extra["pdf"]["page"] == 1
    assert listing.extra["pdf"]["table_index"] == 0
    assert listing.extra["pdf"]["row"][0].startswith("Nieruchomość")
    # The regulamin PDF is not a wykaz and must never be fetched.
    assert COMMERCIAL_URL in client.urls
    assert all("regulamin" not in url for url in client.urls)


def test_parse_search_classifies_residential_flats(tmp_path: Path) -> None:
    items, _ = _search("search.html", "flat", tmp_path)

    assert len(items) == 1
    listing = items[0]
    assert listing.property_type == "flat"
    assert listing.external_id == "ZGM/DS/158/2026#9"
    assert listing.price_pln == 218_000
    assert listing.area_m2 == 43.33
    assert listing.street == "Błogosławionego Czesława"
    assert listing.address_text == "Błogosławionego Czesława 5, Gliwice"


def test_pdf_is_fetched_once_across_queries(tmp_path: Path) -> None:
    client = _client()
    adapter = _adapter(client, tmp_path)
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")

    adapter.parse_search(html, _query(adapter, "flat"))
    adapter.parse_search(html, _query(adapter, "commercial"))

    assert client.urls == [COMMERCIAL_URL, FLAT_URL]


def test_scanned_pdf_is_skipped(tmp_path: Path) -> None:
    client = _Client(
        {
            "https://zgm-gliwice.pl/wp-content/uploads/2026/11/"
            "wykaz-nr-ZGM.DS_.999.2026-Skan-1.1-przetarg.pdf": PDFS / "scanned.pdf",
        },
    )
    adapter = _adapter(client, tmp_path)

    page = adapter.parse_search(
        (FIXTURES / "scanned.html").read_text(encoding="utf-8"),
        _query(adapter, "flat"),
    )

    assert page.items == []
    assert adapter.stats["pdfs"] == 1
    assert adapter.stats["skipped_scanned"] == 1
    assert adapter.stats["failed"] == 0


def test_missing_pdf_is_reported_not_invented(tmp_path: Path) -> None:
    adapter = _adapter(_Client({}), tmp_path)

    page = adapter.parse_search(
        (FIXTURES / "search.html").read_text(encoding="utf-8"),
        _query(adapter, "flat"),
    )

    assert page.items == []
    assert adapter.stats["failed"] == 2
    assert adapter.stats["listings"] == 0


def test_parse_detail_is_identity(tmp_path: Path) -> None:
    items, _ = _search("search.html", "commercial", tmp_path)
    adapter = _adapter(_client(), tmp_path)

    assert adapter.parse_detail("<html></html>", items[0]) is items[0]

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.adapters.pkobp_nieruchomosci import (
    PkobpNieruchomosciAdapter,
)
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> PkobpNieruchomosciAdapter:
    return PkobpNieruchomosciAdapter(
        source_id="pkobp-nieruchomosci",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(region: str = "malopolskie", transaction: str = "sale") -> Query:
    key = f"{region}:{transaction}"
    return next(
        query for query in _adapter().queries(("malopolskie", "slaskie")) if query.key == key
    )


def _search_page() -> SearchPage:
    html = (FIXTURES / "pkobp" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query())


def test_queries_cover_regions_and_transactions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 4
    assert {query.key for query in queries} == {
        "malopolskie:sale",
        "malopolskie:rent",
        "slaskie:sale",
        "slaskie:rent",
    }
    assert {query.offer_type for query in queries} == {"sale", "rent"}
    assert "voivodeship=malopolskie" in queries[0].url
    assert "transaction_type=sale" in queries[0].url
    assert "page_size=100" in queries[0].url


def test_with_page_sets_api_page() -> None:
    paged = _adapter().with_page(_query(), 2)

    assert "page=2" in paged.url
    assert paged.page == 2


def test_parse_search_wrapped_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_pages == 1
    assert page.total_items == 1
    assert len(page.items) == 1

    listing = page.items[0]
    assert listing.external_id == "e1a19b4e-1e67-4446-8d8f-fbbf6ec67f47"
    assert listing.url == (
        "https://nieruchomosci.pkobp.pl/oferty-nieruchomosci-pko-banku-polskiego"
        "/jaroszowiec-ul-kolejowa-2c"
    )
    assert listing.title == "Jaroszowiec, ul. Kolejowa 2c"
    assert listing.offer_type == "sale"
    assert listing.property_type == "commercial"
    assert listing.price_kind == "asking"
    assert listing.price_pln == 1839000
    assert listing.area_m2 == 1918.41
    assert listing.land_area_m2 is None
    assert listing.voivodeship == "malopolskie"
    assert listing.city == "Jaroszowiec"
    assert listing.street == "Kolejowa"
    assert listing.address_text == "Jaroszowiec, Kolejowa 2c"
    assert listing.lat == pytest.approx(50.3339491)
    assert listing.lng == pytest.approx(19.6020247)
    assert listing.geo_source == "source"
    assert listing.seller_type == "bank"
    assert listing.images
    assert listing.extra["pkobp"]["previous_price"] == 2102000


def test_parse_search_accepts_bare_json() -> None:
    raw = (FIXTURES / "pkobp" / "search.json").read_text(encoding="utf-8")

    page = _adapter().parse_search(raw, _query())

    assert len(page.items) == 1


def test_parse_search_paginates() -> None:
    payload = json.dumps({"count": 250, "results": []})

    page = _adapter().parse_search(payload, _query())

    assert page.items == []
    assert page.total_items == 250
    assert page.total_pages == 3


def test_parse_detail_fixture() -> None:
    summary = _search_page().items[0]
    html = (FIXTURES / "pkobp" / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert detail.offer_type == "sale"
    assert detail.property_type == "commercial"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 1839000
    assert detail.area_m2 == 1918.41
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Jaroszowiec"
    assert detail.street == "Kolejowa"
    assert detail.address_text == "Jaroszowiec, Kolejowa 2c"
    assert detail.lat == pytest.approx(50.3339491)
    assert detail.lng == pytest.approx(19.6020247)
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert "Przetarg" in detail.description
    assert len(detail.images) == 2
    assert detail.extra["pkobp"]["offer_deadline"] == "2027-01-12T15:00:00+01:00"


def test_missing_next_data_raises_parse_error() -> None:
    summary = _search_page().items[0]

    with pytest.raises(ParseError):
        _adapter().parse_detail("<html><body>maintenance</body></html>", summary)

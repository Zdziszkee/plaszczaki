from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.adapters.tauron import SITE_ROOT, TauronAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> TauronAdapter:
    return TauronAdapter(
        source_id="tauron",
        regions=RegionRegistry(load_regions_config()),
    )


def _sale_query() -> Query:
    return next(
        query
        for query in _adapter().queries(("malopolskie",))
        if query.key == "malopolskie:sprzedaz"
    )


def _search_page() -> SearchPage:
    html = (FIXTURES / "tauron" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _sale_query())


def test_queries_cover_regions_and_offer_types() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 4
    assert {query.key for query in queries} == {
        "malopolskie:sprzedaz",
        "malopolskie:wynajem",
        "slaskie:sprzedaz",
        "slaskie:wynajem",
    }
    assert {query.offer_type for query in queries} == {"sale", "lease"}
    sale = next(query for query in queries if query.key == "malopolskie:sprzedaz")
    assert "ad_type=sprzeda%C5%BC" in sale.url
    assert "province=ma%C5%82opolskie" in sale.url


def test_with_page_maps_to_zero_based_site_pages() -> None:
    query = _sale_query()

    first = _adapter().with_page(query, 1)
    second = _adapter().with_page(query, 2)

    assert "page=" not in first.url
    assert first.page == 1
    assert second.url.endswith("&page=1")
    assert second.page == 2


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_pages == 1
    assert page.total_items == 2
    assert len(page.items) == 2

    land = page.items[0]
    assert land.external_id == "5898BFAA-DB3B-4007-919C-F94C46913DEB"
    assert land.url == (
        "https://nieruchomosci.tauron.pl/szczegoly"
        "?propertyId=%7B5898BFAA-DB3B-4007-919C-F94C46913DEB%7D"
    )
    assert land.offer_type == "sale"
    assert land.property_type == "plot"
    assert land.price_pln == 24332
    assert land.land_area_m2 == 200.0
    assert land.city == "Rożnów"
    assert land.voivodeship == "malopolskie"

    commercial = page.items[1]
    assert commercial.external_id == "31C51F94-3345-406A-A376-A03E9E6C5FDB"
    assert commercial.property_type == "commercial"
    assert commercial.price_pln == 366000
    assert commercial.city == "Brzeszcze"
    assert commercial.extra["tauron"]["data_skladania_ofert"] == "04.12.2026"


def test_parse_detail_fixture() -> None:
    summary = _search_page().items[1]
    html = (FIXTURES / "tauron" / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert detail.title == "Brzeszcze, ul. Daszyńskiego 6, lokal użytkowy"
    assert detail.offer_type == "sale"
    assert detail.property_type == "commercial"
    assert detail.price_kind == "asking"
    assert detail.price_pln == 366000
    assert detail.area_m2 == 146.2
    assert detail.land_area_m2 is None
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Brzeszcze"
    assert detail.street == "Daszyńskiego"
    assert detail.address_text == "Daszyńskiego 6, 32-620 Brzeszcze"
    assert detail.lat == pytest.approx(49.975389)
    assert detail.lng == pytest.approx(19.150294)
    assert detail.geo_source == "source"
    assert detail.seller_type == "utility"
    assert detail.description is not None
    assert "Brzeszczach" in detail.description
    assert detail.images
    assert all(image.startswith(SITE_ROOT) for image in detail.images)
    assert detail.extra["tauron"]["tryb_postepowania"] == "przetarg pisemny"
    assert detail.auction_date is not None


def test_empty_result_page_is_not_an_error() -> None:
    html = '<html><body><div class="property__wrapper"></div></body></html>'

    page = _adapter().parse_search(html, _sale_query())

    assert page.items == []
    assert page.total_pages == 1


def test_unrecognised_page_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>maintenance</body></html>", _sale_query())

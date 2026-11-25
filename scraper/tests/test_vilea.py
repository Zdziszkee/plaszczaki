from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.vilea import VileaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> VileaAdapter:
    return VileaAdapter(
        source_id="vilea",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_filter_by_localisation() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 4
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {query.offer_type for query in queries} == {"sale", "rent"}
    assert queries[0].url == (
        "https://vilea.pl/nieruchomosci-na-sprzedaz?localizations%5B0%5D=pl%7Cma%C5%82opolskie"
    )
    assert any("%C5%9Bl%C4%85skie" in query.url for query in queries)


def test_with_page_uses_the_wordpress_page_path() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert paged.url == (
        "https://vilea.pl/nieruchomosci-na-sprzedaz/page/2"
        "?localizations%5B0%5D=pl%7Cma%C5%82opolskie"
    )
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "vilea" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert len(page.items) == 1

    item = page.items[0]
    assert item.external_id == "dom-na-sprzedaz-zakopane-559-4917-ods"
    assert item.url == "https://vilea.pl/nieruchomosc/dom-na-sprzedaz-zakopane-559-4917-ods"
    assert item.title == "Dom na sprzedaż \u2013 Zakopane"
    assert item.offer_type == "sale"
    assert item.property_type == "house"
    assert item.price_pln == 3550000
    assert item.price_kind == "asking"
    assert item.area_m2 == 350.0
    assert item.land_area_m2 == 600.0
    assert item.rooms == 6
    assert item.build_year == 2009
    assert item.city == "Zakopane"
    assert item.description and item.description.startswith("Urokliwy, trzypoziomowy dom")


def test_parse_detail_fixture_without_coordinates() -> None:
    search_html = (FIXTURES / "vilea" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "vilea" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "dom-na-sprzedaz-zakopane-559-4917-ods"
    assert detail.title == "Willa W Góralskim Stylu | Przy Szlaku"
    assert detail.price_pln == 3550000
    assert detail.area_m2 == 350.0
    assert detail.rooms == 6
    assert detail.offer_type == "sale"
    assert detail.property_type == "house"
    assert detail.city == "Zakopane"
    assert detail.lat is None
    assert detail.lng is None
    assert detail.geo_source is None
    assert detail.description and detail.description.startswith("Urokliwy, trzypoziomowy dom")
    assert detail.published_at is not None
    assert detail.images


def test_parse_detail_fixture_with_coordinates() -> None:
    search_html = (FIXTURES / "vilea" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "vilea" / "detail_with_geo.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.title == "Penthouse z tarasem na Górnym Mokotowie"
    assert detail.price_pln == 30000000
    assert detail.area_m2 == 233.15
    assert detail.floor == 3
    assert detail.property_type == "flat"
    assert detail.city == "Warszawa"
    assert detail.street == "Jana Karola Chodkiewicza"
    assert detail.lat == 52.2025
    assert detail.lng == 20.9919
    assert detail.geo_source == "source"
    assert detail.seller_type == "agency"
    assert detail.seller_name == "Paulina Woyciechowska"


def test_missing_cards_are_rejected() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    with pytest.raises(ParseError, match="listing cards"):
        _adapter().parse_search("<html><body>nope</body></html>", query)

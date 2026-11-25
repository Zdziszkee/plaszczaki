from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.kupbezposrednio import KupbezposrednioAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> KupbezposrednioAdapter:
    return KupbezposrednioAdapter(
        source_id="kupbezposrednio",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(region: str = "malopolskie", offer_type: str = "sale"):
    return next(query for query in _adapter().queries((region,)) if query.offer_type == offer_type)


def _search_html() -> str:
    return (FIXTURES / "kupbezposrednio" / "search.html").read_text(encoding="utf-8")


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 4
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {
        "https://www.kupbezposrednio.pl/listings?listing_type=sale",
        "https://www.kupbezposrednio.pl/listings?listing_type=rent",
    } == {query.url for query in queries}


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert "page=2" in paged.url
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    page = _adapter().parse_search(_search_html(), _query())
    assert page.current_page == 1
    assert page.total_pages == 2
    assert page.total_items == 24
    assert [item.external_id for item in page.items] == [
        "f46ad0b8-dc51-42c7-a478-56ec0cfbe74b",
    ]

    item = page.items[0]
    assert item.url == (
        "https://www.kupbezposrednio.pl/listings/"
        "mieszkanie-1-pokoj-na-sprzedaz-krakow-20m2-f46ad0b8dc51"
    )
    assert item.title == "Kawalerka na sprzedaz (Krowodrza)"
    assert item.offer_type == "sale"
    assert item.property_type == "flat"
    assert item.price_pln == 490000
    assert item.area_m2 == 20.0
    assert item.rooms == 1
    assert item.city == "Kraków"
    assert item.voivodeship == "malopolskie"
    assert item.lat == 50.0469432
    assert item.lng == 19.9971534
    assert item.geo_source == "source"
    assert len(item.images) == 2


def test_parse_search_keeps_other_region_listing() -> None:
    page = _adapter().parse_search(_search_html(), _query("slaskie"))
    assert [item.city for item in page.items] == ["Mysłowice"]
    assert page.items[0].voivodeship == "slaskie"


def test_parse_search_filters_offer_type() -> None:
    page = _adapter().parse_search(_search_html(), _query(offer_type="rent"))
    assert page.items == []


def test_parse_search_skips_demo_listings() -> None:
    page = _adapter().parse_search(_search_html(), _query())
    assert all("przykładowe" not in (item.title or "").lower() for item in page.items)


def test_parse_detail_fixture() -> None:
    summary = _adapter().parse_search(_search_html(), _query()).items[0]
    html = (FIXTURES / "kupbezposrednio" / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert detail.title == "Kawalerka na sprzedaz (Krowodrza)"
    assert detail.price_pln == 490000
    assert detail.area_m2 == 20.0
    assert detail.rooms == 1
    assert detail.city == "Kraków"
    assert detail.lat == 50.0469432
    assert detail.lng == 19.9971534
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.description.startswith("Na sprzedaż ustawna, jasna kawalerka")
    assert len(detail.images) == 2


def test_parse_detail_requires_coordinates() -> None:
    summary = _adapter().parse_search(_search_html(), _query()).items[0]
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type": "RealEstateListing", "name": "Bez mapy"}</script></head>'
        "<body>Brak współrzędnych</body></html>"
    )
    with pytest.raises(ParseError):
        _adapter().parse_detail(html, summary)

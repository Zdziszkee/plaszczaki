from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.gratka import GratkaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

EMPTY_RESULTS_PAGE = (
    '<html><body><script type="application/json" id="__NUXT_DATA__">'
    '[{"data":1},{"k":2},{"data":3},{"searchResult":4},{"properties":5},'
    '{"nodes":6,"totalCount":7},[],0]'
    "</script></body></html>"
)


FIXTURES = Path(__file__).parent / "fixtures" / "gratka"


def _adapter() -> GratkaAdapter:
    return GratkaAdapter(
        source_id="gratka",
        regions=RegionRegistry(load_regions_config()),
    )


def _search_page(name: str = "search.html"):
    query = _adapter().queries(("malopolskie",))[0]
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return _adapter().parse_search(html, query)


def test_queries_cover_regions_and_categories() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 22
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert len({query.key for query in queries}) == 22

    by_url = {query.url: query for query in queries}
    assert "https://gratka.pl/nieruchomosci/mieszkania/malopolskie" in by_url
    assert "https://gratka.pl/nieruchomosci/mieszkania/malopolskie/wynajem" in by_url
    assert "https://gratka.pl/nieruchomosci/dzialki-grunty/slaskie" in by_url
    assert "https://gratka.pl/nieruchomosci/lokale-uzytkowe/malopolskie/wynajem" in by_url
    assert "https://gratka.pl/nieruchomosci/pokoje/slaskie" in by_url

    flat_sale = by_url["https://gratka.pl/nieruchomosci/mieszkania/malopolskie"]
    assert (flat_sale.offer_type, flat_sale.property_type) == ("sale", "flat")
    flat_rent = by_url["https://gratka.pl/nieruchomosci/mieszkania/malopolskie/wynajem"]
    assert (flat_rent.offer_type, flat_rent.property_type) == ("rent", "flat")
    rooms = by_url["https://gratka.pl/nieruchomosci/pokoje/slaskie"]
    assert (rooms.offer_type, rooms.property_type) == ("rent", "room")

    # Gratka only offers rooms for rent, so there is no sale-room query.
    assert not any(
        query.property_type == "room" and query.offer_type == "sale" for query in queries
    )


def test_with_page_keeps_page_one_bare_and_respects_robots_cap() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    first = _adapter().with_page(query, 1)
    assert first.url == "https://gratka.pl/nieruchomosci/mieszkania/malopolskie"
    assert first.page == 1

    second = _adapter().with_page(query, 2)
    assert second.url == "https://gratka.pl/nieruchomosci/mieszkania/malopolskie?page=2"
    assert second.page == 2

    # robots.txt allows ?page=2 … ?page=10 only, so deeper pages are clamped.
    capped = _adapter().with_page(query, 99)
    assert capped.url.endswith("?page=10")
    assert capped.page == 10


def test_parse_search_grouped_page() -> None:
    page = _search_page("search.html")

    assert page.current_page == 1
    assert page.total_items == 16610
    # ?page=2..?page=10 is all robots.txt allows.
    assert page.total_pages == 10
    assert [item.external_id for item in page.items] == [
        "49019265",  # promoted offer shown above the results
        "49789089",
        "49591889",
        "49809509",
    ]

    promoted = page.items[0]
    assert promoted.price_pln == 820000
    assert promoted.title == "2 pokojowe mieszkanie | Mogilska, Tauron Arena"

    county_item = page.items[1]
    assert county_item.url == (
        "https://gratka.pl/nieruchomosci/mieszkanie-wielicki-wieliczka-krakowska/ob/49789089"
    )
    assert county_item.voivodeship == "malopolskie"
    assert county_item.county == "wielicki"
    assert county_item.city == "Wieliczka"
    assert county_item.district is None
    assert county_item.street == "Krakowska"
    assert county_item.price_pln == 599000
    assert county_item.price_per_m2_pln == 12710
    assert county_item.area_m2 == 47.0
    assert county_item.rooms == 2
    assert county_item.floor == 1
    assert county_item.price_unit == "total"
    assert county_item.offer_type == "sale"
    assert county_item.property_type == "flat"
    assert county_item.seller_type == "agency"
    assert county_item.seller_name == "GWARANT Biuro Nieruchomości"
    assert county_item.images[0].startswith(
        "https://img1.staticmorizon.com.pl/thumb/",
    )
    assert county_item.images[0].endswith("/mieszkanie-na-sprzedaz-47-m.jpg")

    city_item = page.items[2]
    assert city_item.city == "Kraków"
    assert city_item.county is None
    assert city_item.district == "Stare Miasto"
    assert city_item.rooms == 4

    developer = page.items[3]
    assert developer.seller_type == "developer"


def test_parse_search_flat_page_shape() -> None:
    page = _search_page("search-flat.html")

    assert page.total_items == 16610
    assert page.total_pages == 10
    assert [item.external_id for item in page.items] == [
        "48732861",
        "49809481",
        "49809479",
    ]
    assert page.items[1].price_pln is not None
    assert page.items[1].price_unit == "total"


def test_parse_search_handles_empty_results() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    page = _adapter().parse_search(EMPTY_RESULTS_PAGE, query)

    assert page.items == []
    assert page.total_items == 0
    assert page.total_pages == 1


def test_parse_search_rejects_pages_without_payload() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>no payload</body></html>", query)


def test_parse_detail_fixture() -> None:
    page = _search_page()
    summary = next(item for item in page.items if item.external_id == "49591889")
    detail_html = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "49591889"
    assert detail.url == (
        "https://gratka.pl/nieruchomosci/mieszkanie-krakow-stare-miasto-skawinska/ob/49591889"
    )
    assert (
        detail.title == "Przestronne mieszkanie w zielonej oficynie na Kazimierzu \u2013 85,4 m²!"
    )

    assert detail.price_pln == 1450000
    assert detail.price_kind == "asking"
    assert detail.price_unit == "total"
    assert detail.price_per_m2_pln == 17160
    assert detail.area_m2 == 84.5
    assert detail.land_area_m2 is None
    assert detail.rooms == 4
    assert detail.floor == 3
    assert detail.floor_label == "piętro 3/4"
    assert detail.build_year == 1897
    assert detail.market == "secondary"

    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Stare Miasto"
    assert detail.street == "Skawińska"
    assert detail.address_text == "Skawińska, Kraków"

    assert detail.lat == 50.0478
    assert detail.lng == 19.9432
    assert detail.geo_source == "source"

    assert detail.seller_type == "agency"
    assert detail.seller_name == "TECNOMEDIA SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ"
    assert len(detail.images) == 3

    description = detail.description or ""
    assert description.startswith("Przestronne mieszkanie w zielonej oficynie")
    # Paragraph boundaries must not run words together.
    assert "85,4 m²! Kraków" in description

    assert detail.published_at == 1794009600000
    assert detail.modified_at == 1794528000000
    assert detail.extra["gratka"]["reference"] == "KRMH1/0333"
    assert detail.extra["gratka"]["labels"]["Rok budowy"] == "1897"


def test_parse_detail_rent_fixture() -> None:
    summary = Listing(
        source_id="gratka",
        external_id="49808403",
        url="https://gratka.pl/nieruchomosci/mieszkanie-krakow-pradnik-bialy-pradnicka/ob/49808403",
    )
    detail_html = (FIXTURES / "detail-rent.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "49808403"
    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.price_unit == "monthly"
    assert detail.price_pln == 1600
    assert detail.rent_pln == 372
    assert detail.deposit_pln == 2000
    assert detail.area_m2 == 24.0
    assert detail.rooms == 1
    assert detail.floor == 7
    assert detail.city == "Kraków"
    assert detail.lat == 50.093935
    assert detail.lng == 19.9288559
    assert detail.geo_source == "source"
    assert detail.description

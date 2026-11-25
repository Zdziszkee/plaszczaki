from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.rynekpierwotny import RynekpierwotnyAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> RynekpierwotnyAdapter:
    return RynekpierwotnyAdapter(
        source_id="rynekpierwotny",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert [query.url for query in queries] == [
        "https://rynekpierwotny.pl/s/nowe-mieszkania-malopolskie/",
        "https://rynekpierwotny.pl/s/nowe-mieszkania-slaskie/",
    ]
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}


def test_queries_skip_unknown_regions() -> None:
    assert _adapter().queries(("mazowieckie",)) == []


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)

    assert paged.url == "https://rynekpierwotny.pl/s/nowe-mieszkania-malopolskie/?page=3"
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "rynekpierwotny" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 12
    assert page.total_items == 282
    assert len(page.items) == 24

    first = page.items[0]
    assert first.external_id == "18034"
    assert first.title == "Pod Orłem"
    assert first.market == "primary"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.price_kind == "starting"
    assert first.price_pln == 685000
    assert first.area_m2 == 63.0
    assert first.rooms == 4
    assert first.voivodeship == "malopolskie"
    assert first.county == "krakowski"
    assert first.city == "Bębło"
    assert first.lat == 50.18658519999999
    assert first.lng == 19.7865057
    assert first.geo_source == "source"
    assert first.seller_type == "developer"
    assert first.seller_name == "Optimum Inwestycje Optimum Sp. z o.o. Sp. Komandytowa"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "rynekpierwotny" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "rynekpierwotny" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = next(
        item
        for item in _adapter().parse_search(search_html, query).items
        if item.external_id == "19878"
    )

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "19878"
    assert detail.title == "Zakole Wisły"
    assert detail.market == "primary"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 549939
    assert detail.price_per_m2_pln == 17700
    assert detail.area_m2 == 31.07
    assert detail.rooms == 1
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Podgórze"
    assert detail.street == "ul. Na Zakolu Wisły"
    assert detail.lat == 50.0520964
    assert detail.lng == 19.986105
    assert detail.geo_source == "source"
    assert detail.seller_type == "developer"
    assert detail.seller_name == "ATAL S.A."
    assert detail.description is not None
    assert detail.description.startswith("Miejsce dla tych")
    assert len(detail.images) >= 1

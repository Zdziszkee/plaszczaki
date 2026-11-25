from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.flats import FlatsAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> FlatsAdapter:
    return FlatsAdapter(
        source_id="flats",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 6
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(query.url == "https://flats.pl/pl/wynajem/krakow" for query in queries)
    assert any(query.url == "https://flats.pl/pl/wynajem/katowice" for query in queries)
    assert all(query.offer_type == "rent" for query in queries)
    assert all(query.property_type == "flat" for query in queries)


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert paged.url.endswith("?page=2")
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "flats" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 1
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "cmyd0xns62"
    assert first.url == (
        "https://flats.pl/pl/ogloszenia/mieszkanie-4-pokojowe-70-m2-krakow-pradnicka-cmyd0xns62"
    )
    assert first.title == "Wynajmę mieszkanie przy ul. Prądnickiej"
    assert first.offer_type == "rent"
    assert first.property_type == "flat"
    assert first.price_pln == 3900
    assert first.price_unit == "monthly"
    assert first.area_m2 == 70
    assert first.rooms == 4
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Prądnicka"
    assert first.street == "Prądnicka"
    assert first.lat == 50.0825225
    assert first.lng == 19.937678
    assert first.geo_source == "source"
    assert len(first.images) == 5

    second = page.items[1]
    assert second.external_id == "c7a0yytswj"
    assert second.price_pln == 2600
    assert second.area_m2 == 52
    assert second.rooms == 2


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "flats" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "flats" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "cmyd0xns62"
    assert detail.title == "Wynajmę mieszkanie przy ul. Prądnickiej"
    assert detail.price_pln == 3900
    assert detail.area_m2 == 70
    assert detail.rooms == 4
    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Prądnicka"
    assert detail.street == "Prądnicka"
    assert detail.address_text == "Prądnicka, Kraków"
    assert detail.lat == 50.0825225
    assert detail.lng == 19.937678
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.description.startswith("Wynajmę mieszkanie poziomowe")
    assert len(detail.images) == 5

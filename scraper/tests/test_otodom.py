from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.otodom import OtodomAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> OtodomAdapter:
    return OtodomAdapter(
        source_id="otodom",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 20
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(
        query.url == "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/malopolskie"
        for query in queries
    )


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)
    assert paged.url.endswith("?page=3")
    assert paged.page == 3


def test_newest_adds_otodom_sort_parameters() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    newest = _adapter().newest(query)

    assert "by=LATEST" in newest.url
    assert "direction=DESC" in newest.url
    assert newest.url.startswith("https://www.otodom.pl/pl/wyniki/")


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "otodom" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 475
    assert page.total_items == 17095
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "66295360"
    assert first.url == "https://www.otodom.pl/pl/oferta/podedworze-13-ID4uas0"
    assert first.voivodeship == "malopolskie"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.images == [
        "https://example.test/image-1.jpg",
        "https://example.test/image-2.jpg",
    ]

    flat = page.items[1]
    assert flat.external_id == "66992141"
    assert flat.price_pln == 854727
    assert flat.area_m2 == 69.49
    assert flat.rooms == 4
    assert flat.seller_name == "Example Agency"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "otodom" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "otodom" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[1]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "66992141"
    assert detail.price_pln == 854727
    assert detail.price_per_m2_pln == 12300
    assert detail.area_m2 == 69.49
    assert detail.rooms == 4
    assert detail.market == "primary"
    assert detail.voivodeship == "malopolskie"
    assert detail.county == "krakowski"
    assert detail.city == "Rząska"
    assert detail.lat == 50.10298
    assert detail.lng == 19.840769
    assert detail.geo_source == "source"
    assert detail.seller_type == "developer"
    assert detail.seller_name == "Example Agency"
    assert detail.description == "Example description with a balcony."
    assert len(detail.images) == 2

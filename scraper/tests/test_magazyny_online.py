from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.magazyny_online import MagazynyOnlineAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> MagazynyOnlineAdapter:
    return MagazynyOnlineAdapter(
        source_id="magazyny-online",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(query.url == "https://magazyny.online/magazyny/lista/slaskie" for query in queries)
    assert all(query.offer_type == "rent" for query in queries)
    assert all(query.property_type == "commercial" for query in queries)


def test_with_page_keeps_region_url() -> None:
    query = _adapter().queries(("slaskie",))[0]
    paged = _adapter().with_page(query, 4)

    assert paged.url == query.url
    assert paged.page == 4


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "magazyny-online" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("slaskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 1
    assert page.total_items == 2
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "3025"
    assert first.url == (
        "https://magazyny.online/centrum-logistyczne/7r-park-beskid-ii-czechowice-dziedzice-3025"
    )
    assert first.title == "7R Park Beskid II"
    assert first.city == "Czechowice-Dziedzice"
    assert first.voivodeship == "slaskie"
    assert first.offer_type == "rent"
    assert first.price_kind == "call"
    assert first.area_m2 == 88784
    assert first.lat == 49.8844280255
    assert first.lng == 19.001737006946
    assert first.geo_source == "source"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "magazyny-online" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "magazyny-online" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("slaskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "3025"
    assert detail.title == "Magazyn do wynajęcia 7R Park Beskid II"
    assert detail.description is not None
    assert "centrum logistyczne o statusie klasy A" in detail.description
    assert detail.offer_type == "rent"
    assert detail.property_type == "commercial"
    assert detail.price_kind == "call"
    assert detail.price_unit == "monthly"
    assert detail.price_pln is None
    assert detail.area_m2 == 88784
    assert detail.voivodeship == "slaskie"
    assert detail.county == "bielski"
    assert detail.city == "Czechowice-Dziedzice"
    assert detail.lat == 49.8844280255
    assert detail.lng == 19.001737006946
    assert detail.geo_source == "source"
    assert detail.seller_type == "agency"
    assert detail.seller_name == "MAXON Nieruchomości Sp. z o.o."
    assert detail.published_at is not None
    assert detail.modified_at is not None

    extra = detail.extra["magazyny_online"]
    assert extra["available_area_m2"] == 16477
    assert extra["minimum_module_m2"] == 3000

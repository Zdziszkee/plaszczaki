from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.homeportal import HomeportalAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> HomeportalAdapter:
    return HomeportalAdapter(
        source_id="homeportal",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 22
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(
        query.url == "https://homeportal.pl/wynajem/mieszkania/malopolskie/" for query in queries
    )
    assert any(query.url == "https://homeportal.pl/sprzedaz/domy/slaskie/" for query in queries)


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert paged.url.endswith("?page=2")
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "homeportal" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_items == 384
    assert page.total_pages == 39
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "C390-WM-44780"
    assert first.url.endswith("/C390-WM-44780/")
    assert first.title == "Mieszkanie, 50.0 m², Kraków"
    assert first.price_pln == 2499
    assert first.area_m2 == 50.0
    assert first.rooms == 2
    assert first.build_year == 2019
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Dębniki"
    assert first.published_at is not None


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "homeportal" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "homeportal" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "C390-WM-44780"
    assert detail.price_pln == 2499
    assert detail.price_per_m2_pln == 50
    assert detail.area_m2 == 50.0
    assert detail.rooms == 2
    assert detail.floor == 2
    assert detail.floor_label == "2p"
    assert detail.build_year == 2019
    assert detail.market == "secondary"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Dębniki"
    assert detail.lat == 50.0267
    assert detail.lng == 19.9174
    assert detail.geo_source == "source"
    assert "Przykładowy opis nieruchomości" in (detail.description or "")
    assert detail.seller_type == "agency"
    assert detail.seller_name == "RealDom Nieruchomości"
    assert len(detail.images) == 2
    assert detail.published_at is not None

from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.noweinwestycje import NoweinwestycjeAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> NoweinwestycjeAdapter:
    return NoweinwestycjeAdapter(
        source_id="noweinwestycje",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert [query.url for query in queries] == [
        "https://noweinwestycje.pl/rynek-pierwotny/malopolskie",
        "https://noweinwestycje.pl/rynek-pierwotny/slaskie",
    ]
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}


def test_with_page_uses_path_pagination() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    assert _adapter().with_page(query, 1).url == query.url
    paged = _adapter().with_page(query, 3)
    assert paged.url == "https://noweinwestycje.pl/rynek-pierwotny/malopolskie/page3"
    assert paged.page == 3
    assert "?" not in paged.url


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "noweinwestycje" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 6
    assert len(page.items) == 20

    first = page.items[0]
    assert first.external_id == "1907"
    assert first.title == "Murapol Prado"
    assert first.market == "primary"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.price_kind == "starting"
    assert first.price_pln == 423864
    assert first.price_per_m2_pln == 14260
    assert first.area_m2 == 25.23
    assert first.rooms == 1
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Prądnik Biały"
    assert first.lat == 50.0985125
    assert first.lng == 19.9260082
    assert first.geo_source == "source"
    assert first.seller_type == "developer"
    assert first.seller_name == "Murapol S.A."
    assert len(first.images) == 1


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "noweinwestycje" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "noweinwestycje" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "1907"
    assert detail.title == "Murapol Prado"
    assert detail.market == "primary"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 423864
    assert detail.price_per_m2_pln == 14259
    assert detail.area_m2 == 25.23
    assert detail.build_year == 2027
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Prądnik Biały"
    assert detail.lat == 50.098513
    assert detail.lng == 19.926008
    assert detail.geo_source == "source"
    assert detail.seller_type == "developer"
    assert detail.seller_name == "Murapol S.A."
    assert detail.description is not None
    assert "Murapol Prado" in detail.description

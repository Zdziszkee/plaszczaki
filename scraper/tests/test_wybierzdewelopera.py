from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.wybierzdewelopera import WybierzdeweloperaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> WybierzdeweloperaAdapter:
    return WybierzdeweloperaAdapter(
        source_id="wybierzdewelopera",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert [query.url for query in queries] == [
        "https://wybierzdewelopera.pl/inwestycje/malopolskie/",
        "https://wybierzdewelopera.pl/inwestycje/slaskie/",
    ]
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}


def test_with_page_uses_path_pagination() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)

    assert paged.url == "https://wybierzdewelopera.pl/inwestycje/malopolskie/page/3/"
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "wybierzdewelopera" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 10
    assert page.total_items == 230
    assert len(page.items) >= 20

    first = page.items[0]
    assert first.external_id == "atal-bronowice"
    assert first.title == "ATAL Bronowice"
    assert first.url == "https://wybierzdewelopera.pl/inwestycja/atal-bronowice/"
    assert first.market == "primary"
    assert first.offer_type == "sale"
    assert first.price_pln is None
    assert first.price_per_m2_pln is None
    assert first.area_m2 == 36.44
    assert first.rooms == 2
    assert first.build_year == 2027
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Prądnik Biały"
    assert first.seller_type == "developer"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "wybierzdewelopera" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "wybierzdewelopera" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "atal-bronowice"
    assert detail.title == "ATAL Bronowice"
    assert detail.market == "primary"
    assert detail.price_pln is None
    assert detail.price_kind == "unknown"
    assert detail.price_per_m2_pln == 13651
    assert detail.area_m2 == 36.44
    assert detail.rooms == 2
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Prądnik Biały"
    assert detail.lat == 50.101989780205
    assert detail.lng == 19.875385074843
    assert detail.geo_source == "source"
    assert detail.seller_type == "developer"
    assert detail.seller_name == "ATAL S.A."
    assert detail.description is not None
    assert detail.description.startswith("Inwestycja ATAL Bronowice")

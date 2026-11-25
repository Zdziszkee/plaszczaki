from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.tabelaofert import TabelaofertAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> TabelaofertAdapter:
    return TabelaofertAdapter(
        source_id="tabelaofert",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert [query.url for query in queries] == [
        "https://tabelaofert.pl/nowe-mieszkania/malopolskie",
        "https://tabelaofert.pl/nowe-mieszkania/slaskie",
    ]
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)

    assert paged.url == "https://tabelaofert.pl/nowe-mieszkania/malopolskie?page=2"
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "tabelaofert" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 6
    assert len(page.items) == 30

    first = page.items[0]
    assert first.external_id == "6414431"
    assert first.title == "Wiślane Tarasy 2.0"
    assert first.market == "primary"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.price_kind == "starting"
    assert first.price_pln == 787416
    assert first.area_m2 == 26.16
    assert first.rooms == 1
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Grzegórzki"
    assert first.street == "ul. Grzegórzecka/Stachowskiego"
    assert first.lat == 50.056027
    assert first.lng == 19.964022
    assert first.geo_source == "source"
    assert first.seller_type == "developer"
    assert first.description is not None


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "tabelaofert" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "tabelaofert" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = next(
        item
        for item in _adapter().parse_search(search_html, query).items
        if "awangarda-mogilska" in item.url
    )

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.market == "primary"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 666700
    assert detail.price_per_m2_pln == 18198
    assert detail.area_m2 == 32.48
    assert detail.rooms == 1
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Prądnik Czerwony"
    assert detail.street == "ul. Mogilska 126"
    assert detail.lat == 50.06964
    assert detail.lng == 19.98043
    assert detail.geo_source == "source"
    assert detail.seller_type == "developer"
    assert detail.seller_name == "Skanska Residential Development Poland"
    assert detail.description is not None
    assert "Industrialny charakter" in detail.description
    assert len(detail.images) == 1

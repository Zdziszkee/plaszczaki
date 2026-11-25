from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.szybkokomercyjne import SzybkokomercyjneAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> SzybkokomercyjneAdapter:
    return SzybkokomercyjneAdapter(
        source_id="szybkokomercyjne",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_regions_and_transactions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 4
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {query.offer_type for query in queries} == {"sale", "rent"}
    assert any(
        query.url == "https://szybkokomercyjne.pl/oferty/sprzedaz/nieruchomosci-komercyjne/slaskie"
        for query in queries
    )


def test_with_page_adds_strona_parameter() -> None:
    query = _adapter().queries(("slaskie",))[0]
    paged = _adapter().with_page(query, 3)

    assert paged.url.endswith("?strona=3")
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "szybkokomercyjne" / "search.html").read_text(encoding="utf-8")
    query = next(query for query in _adapter().queries(("slaskie",)) if query.offer_type == "sale")
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 3
    assert page.total_items == 29
    assert len(page.items) == 1

    item = page.items[0]
    assert item.external_id == "15784371"
    assert item.url.startswith("https://szybkokomercyjne.pl/oferta-15784371/")
    assert item.title == "Lokal Komercyjny lokal usługowy"
    assert item.price_pln == 139000
    assert item.price_per_m2_pln == 2574
    assert item.area_m2 == 54
    assert item.rooms == 1
    assert item.city == "Piekary Śląskie"
    assert item.voivodeship == "slaskie"
    assert item.offer_type == "sale"
    assert item.property_type == "commercial"
    assert item.geo_source is None


def test_parse_search_keeps_rent_listing() -> None:
    html = (FIXTURES / "szybkokomercyjne" / "search.html").read_text(encoding="utf-8")
    query = next(query for query in _adapter().queries(("slaskie",)) if query.offer_type == "rent")
    page = _adapter().parse_search(html, query)

    assert [item.external_id for item in page.items] == ["15491082"]
    assert page.items[0].price_unit == "monthly"
    assert page.items[0].city == "Siemianowice Śląskie"


def test_parse_search_ignores_nationwide_similar_offers_when_empty() -> None:
    html = (FIXTURES / "szybkokomercyjne" / "search-empty.html").read_text(
        encoding="utf-8",
    )
    query = next(
        query for query in _adapter().queries(("malopolskie",)) if query.offer_type == "sale"
    )
    page = _adapter().parse_search(html, query)

    assert page.items == []
    assert page.total_items == 0
    assert page.total_pages == 1


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "szybkokomercyjne" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "szybkokomercyjne" / "detail.html").read_text(encoding="utf-8")
    query = next(query for query in _adapter().queries(("slaskie",)) if query.offer_type == "rent")
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "15491082"
    assert detail.title == "Magazyn na wynajem - Siemianowice Śląskie"
    assert detail.description is not None
    assert "magazyn o powierzchni 5000 m2" in detail.description
    assert detail.price_pln == 30000
    assert detail.price_per_m2_pln == 30
    assert detail.price_unit == "monthly"
    assert detail.area_m2 == 1000
    assert detail.rooms == 11
    assert detail.offer_type == "rent"
    assert detail.property_type == "commercial"
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Siemianowice Śląskie"
    assert detail.lat == 50.3264314
    assert detail.lng == 19.0295714
    assert detail.geo_source == "source"

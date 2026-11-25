from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.sprzedambiznes import SprzedambiznesAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> SprzedambiznesAdapter:
    return SprzedambiznesAdapter(
        source_id="sprzedambiznes",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_regions_and_transactions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 4
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {query.offer_type for query in queries} == {"sale", "rent"}
    assert any(
        query.url == "https://sprzedambiznes.pl/nieruchomosci-komercyjne?w=slaskie&rt=sprzedaz"
        for query in queries
    )


def test_with_page_adds_strona_parameter() -> None:
    query = _adapter().queries(("slaskie",))[0]
    paged = _adapter().with_page(query, 2)

    assert "w=slaskie" in paged.url
    assert "rt=sprzedaz" in paged.url
    assert "strona=2" in paged.url
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "sprzedambiznes" / "search.html").read_text(encoding="utf-8")
    query = next(query for query in _adapter().queries(("slaskie",)) if query.offer_type == "sale")
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_items == 98
    assert page.total_pages == 10
    # The promoted Mazowieckie card is out of scope and must be dropped.
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "26901"
    assert first.url == (
        "https://sprzedambiznes.pl/nieruchomosci-komercyjne/"
        "nieruchomosc-komercyjna-wojkowice-koscielne-hala-535-m2-biura-i-dom-26901"
    )
    assert first.title == (
        "Nieruchomość komercyjna Wojkowice Kościelne \u2013 hala 535 m², biura i dom"
    )
    assert first.price_pln == 3500000
    assert first.price_unit == "total"
    assert first.city == "Wojkowice koscielne"
    assert first.voivodeship == "slaskie"
    assert first.offer_type == "sale"
    assert first.property_type == "commercial"

    second = page.items[1]
    assert second.external_id == "26440"
    assert second.price_pln == 180000
    assert second.city == "Katowice"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "sprzedambiznes" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "sprzedambiznes" / "detail.html").read_text(encoding="utf-8")
    query = next(query for query in _adapter().queries(("slaskie",)) if query.offer_type == "sale")
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.title == "Hala produkcyjno - magazynowa z zapleczem biurowym"
    assert detail.description is not None
    assert "kompleks produkcyjno-magazynowy" in detail.description
    assert detail.price_pln == 7900000
    assert detail.price_unit == "total"
    assert detail.price_per_m2_pln == 7356
    assert detail.area_m2 == 1074
    assert detail.rooms == 10
    assert detail.offer_type == "sale"
    assert detail.property_type == "commercial"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.street == "Kosocicka"
    assert detail.address_text == "Kosocicka, Kraków, Małopolskie"
    assert detail.lat == 50.0038885
    assert detail.lng == 20.0064846
    assert detail.geo_source == "source"
    assert detail.extra["sprzedambiznes"]["category"] == "Hale przemysłowe, magazyny"

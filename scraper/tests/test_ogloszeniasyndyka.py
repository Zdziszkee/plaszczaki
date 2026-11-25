from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.ogloszeniasyndyka import OgloszeniaSyndykaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "ogloszeniasyndyka"


def _adapter() -> OgloszeniaSyndykaAdapter:
    return OgloszeniaSyndykaAdapter(
        source_id="ogloszeniasyndyka",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert queries[0].key == "malopolskie:nieruchomosci"
    assert "cat=4" in queries[0].url
    assert "placetype=state" in queries[0].url
    assert "placeid=PL.6" in queries[0].url
    assert "location=Ma%C5%82opolskie" in queries[0].url
    assert "placeid=PL.9" in queries[1].url


def test_queries_skip_unknown_regions() -> None:
    assert _adapter().queries(("opolskie",)) == []


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)

    assert paged.page == 3
    assert "page=3" in paged.url
    assert "placeid=PL.6" in paged.url


def test_parse_search_regional_fixture() -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("slaskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 1
    assert page.total_items == 1
    assert len(page.items) == 1

    item = page.items[0]
    assert item.external_id == "23"
    assert item.url == "https://ogloszeniasyndyka.pl/ad/23/syndyk-sprzeda-sosnowiec-ul-grabowa"
    assert item.title == "Syndyk sprzeda - Sosnowiec, ul. Grabowa"
    assert item.price_pln == 335000
    assert item.price_kind == "starting"
    assert item.offer_type == "auction"
    assert item.voivodeship == "slaskie"
    assert item.city == "Sosnowiec"
    assert item.published_at == 1764115200000
    assert item.images == [
        "https://ogloszeniasyndyka.pl/storage/products/thumb/692703fa111581764164602.JPG",
    ]


def test_parse_search_reads_pagination() -> None:
    html = (FIXTURES / "search-national.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.total_items == 40
    assert page.total_pages == 4
    assert len(page.items) == 12
    assert page.items[0].external_id


def test_parse_search_rejects_unrelated_page() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search(
            "<html><body>maintenance</body></html>", _adapter().queries(("slaskie",))[0]
        )


def test_parse_search_rejects_empty_page() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("", _adapter().queries(("slaskie",))[0])


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("slaskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "23"
    assert detail.title == "Syndyk sprzeda - Sosnowiec, ul. Grabowa"
    assert detail.price_pln == 335000
    assert detail.offer_type == "auction"
    assert detail.property_type == "house"
    assert detail.market == "secondary"
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Sosnowiec"
    assert detail.address_text == "Sosnowiec, Śląskie, Polska"
    assert detail.lat == 50.2789
    assert detail.lng == 19.1294
    assert detail.geo_source == "source"
    assert detail.case_number == "WA2M/GUp-s/783/2025"
    assert detail.land_area_m2 == 732.0
    assert detail.auction_date == 1769126400000
    assert detail.published_at == 1764115200000
    assert detail.description is not None
    assert "udział" in detail.description
    assert "Pokaż więcej" not in detail.description
    assert len(detail.images) == 7
    assert detail.extra["ogloszeniasyndyka"]["breadcrumb"] == ["Nieruchomości", "Dom"]
    assert detail.extra["ogloszeniasyndyka"]["views"] == 285

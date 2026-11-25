from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.partnersinternational import PartnersInternationalAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> PartnersInternationalAdapter:
    return PartnersInternationalAdapter(
        source_id="partnersinternational",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_regions_and_transactions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 4
    assert [query.url for query in queries] == [
        "https://partnersinternational.pl/nieruchomosci?q=malopolskie&transaction=sprzedaz",
        "https://partnersinternational.pl/nieruchomosci?q=malopolskie&transaction=wynajem",
        "https://partnersinternational.pl/nieruchomosci?q=slaskie&transaction=sprzedaz",
        "https://partnersinternational.pl/nieruchomosci?q=slaskie&transaction=wynajem",
    ]
    assert {query.offer_type for query in queries} == {"sale", "rent"}


def test_with_page_uses_the_strona_path() -> None:
    query = _adapter().queries(("slaskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert paged.url == (
        "https://partnersinternational.pl/nieruchomosci/strona/2?q=slaskie&transaction=sprzedaz"
    )
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "partnersinternational" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 2
    assert page.total_items == 21
    assert len(page.items) == 3

    first = page.items[0]
    assert first.external_id == "2876933"
    assert first.url == (
        "https://partnersinternational.pl/nieruchomosc/komercja/stefana-batorego-tarnow"
    )
    assert first.title == "Zabytkowa rezydencja miejska"
    assert first.offer_type == "sale"
    assert first.property_type == "commercial"
    assert first.price_pln == 7931000
    assert first.price_kind == "asking"
    assert first.area_m2 == 1005.0
    assert first.rooms == 20
    assert first.build_year == 1893
    assert first.voivodeship == "malopolskie"
    assert first.city == "Tarnów"
    assert first.district is None
    assert first.street == "Stefana Batorego"
    assert first.description and "KOCI ZAMEK" in first.description

    flat = page.items[2]
    assert flat.external_id == "2870873"
    assert flat.property_type == "flat"
    assert flat.price_pln == 3249000
    assert flat.area_m2 == 79.0
    assert flat.rooms == 4
    assert flat.city == "Kościelisko"
    assert flat.street == "Salamandra"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "partnersinternational" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "partnersinternational" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[2]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "2870873"
    assert detail.title == "Ekskluzywny apartament z widokiem na Tatry"
    assert detail.price_pln == 3249000
    assert detail.price_per_m2_pln == 41101
    assert detail.price_kind == "asking"
    assert detail.area_m2 == 79.0
    assert detail.rooms == 4
    assert detail.build_year == 2024
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kościelisko"
    assert detail.street == "Salamandra"
    assert detail.lat == 49.2987858
    assert detail.lng == 19.9080607
    assert detail.geo_source == "source"
    assert detail.published_at is not None
    assert detail.modified_at is not None
    assert detail.description and detail.description.startswith("EKSLUZYWNY APARTAMENT")
    assert "TATRYDwupoziomowy" not in detail.description
    assert detail.images


def test_coordinates_fall_back_to_the_summary() -> None:
    search_html = (FIXTURES / "partnersinternational" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "partnersinternational" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[2]

    stripped = detail_html.replace("data-google-map", "data-no-map")
    detail = _adapter().parse_detail(stripped, summary)

    assert detail.lat is None
    assert detail.lng is None
    assert detail.geo_source is None


def test_empty_search_page_is_not_an_error() -> None:
    html = '<html><body><div class="list-items"></div><p>0 ofert</p></body></html>'
    query = _adapter().queries(("malopolskie",))[1]
    page = _adapter().parse_search(html, query)
    assert page.items == []
    assert page.total_items == 0


def test_missing_cards_are_rejected() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    with pytest.raises(ParseError, match="listing cards"):
        _adapter().parse_search("<html><body>blocked</body></html>", query)

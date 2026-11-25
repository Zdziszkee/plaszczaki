from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.sprzedajemy import SprzedajemyAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "sprzedajemy"


def _adapter() -> SprzedajemyAdapter:
    return SprzedajemyAdapter(
        source_id="sprzedajemy",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(key_suffix: str):
    queries = _adapter().queries(("malopolskie",))
    return next(query for query in queries if query.key.endswith(key_suffix))


def _search_page():
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query("mieszkania:sprzedaz"))


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 24
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert len({query.key for query in queries}) == 24
    assert any(
        query.url == "https://sprzedajemy.pl/malopolskie/nieruchomosci/mieszkania/sprzedaz"
        for query in queries
    )
    assert any(
        query.url
        == "https://sprzedajemy.pl/slaskie/nieruchomosci/garaze-i-miejsca-postojowe/wynajem"
        for query in queries
    )


def test_with_page_uses_offset_pagination() -> None:
    query = _query("mieszkania:sprzedaz")

    first = _adapter().with_page(query, 1)
    third = _adapter().with_page(query, 3)

    assert first.url == query.url
    assert first.page == 1
    assert third.url == f"{query.url}?offset=60"
    assert third.page == 3


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_pages == 49
    assert page.total_items == 1464
    assert len(page.items) == 3

    first = page.items[0]
    assert first.external_id == "71151563"
    assert first.url == (
        "https://sprzedajemy.pl/mieszkanie-3-pokoje-gorlice-4-1b8e55-6fpbc4-nr71151563"
    )
    assert first.title == "Mieszkanie 3 pokoje"
    assert first.price_pln == 468008
    assert first.price_kind == "asking"
    assert first.area_m2 == 62.0
    assert first.land_area_m2 is None
    assert first.city == "Gorlice"
    assert first.voivodeship == "malopolskie"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.seller_type == "agency"
    assert first.published_at == 1795473124000
    assert first.images == [
        "https://thumbs.img-sprzedajemy.pl/350x250c/51/bb/65/"
        "mieszkanie-3-pokoje-gorlice-587434056.jpg"
    ]

    private = page.items[2]
    assert private.external_id == "74118599"
    assert private.price_pln == 259000
    assert private.area_m2 == 34.0
    assert private.city == "Oświęcim"
    assert private.seller_type == "private"


def test_parse_search_rejects_pages_without_offers() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body><p>blocked</p></body></html>", _query("domy:sprzedaz"))


def test_parse_detail_flat_rent_fixture() -> None:
    summary = _search_page().items[0]
    html = (FIXTURES / "detail_flat_rent.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == "74101178"
    assert detail.url.endswith("-nr74101178")
    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.price_pln == 2650
    assert detail.price_unit == "monthly"
    assert detail.price_kind == "asking"
    assert detail.area_m2 == 60.0
    assert detail.land_area_m2 is None
    assert detail.rooms == 3
    assert detail.floor == 0
    assert detail.floor_label == "parter"
    assert detail.market == "unknown"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Niepołomice"
    assert detail.street == "Brzezinki 8a"
    assert detail.address_text == "Niepołomice, Brzezinki 8a"
    assert detail.lat == pytest.approx(50.02265939999999)
    assert detail.lng == pytest.approx(20.1959745)
    assert detail.geo_source == "source"
    assert detail.seller_type == "agency"
    assert detail.seller_name == "Kamil"
    assert detail.description is not None
    assert "Wynajem TYLKO okazjonalny" in detail.description
    assert len(detail.description) > 2000
    assert len(detail.images) == 3
    assert detail.extra["sprzedajemy"]["coordinates_zoom"] == 12
    assert detail.extra["sprzedajemy"]["offer_number"] == "74101178"


def test_parse_detail_house_overrides_summary_type() -> None:
    summary = _search_page().items[0]
    html = (FIXTURES / "detail_house_sale.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.offer_type == "sale"
    assert detail.property_type == "house"
    assert detail.price_pln == 3300000
    assert detail.price_per_m2_pln == 4365
    assert detail.area_m2 == 756.0
    assert detail.land_area_m2 == 5133.0
    assert detail.rooms == 10
    assert detail.build_year == 2021
    assert detail.market == "secondary"
    assert detail.city == "Grywałd"
    assert detail.lat == pytest.approx(49.450207589576)
    assert detail.lng == pytest.approx(20.351187633452)
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.seller_name == "RE/MAX Polska"


def test_parse_detail_plot_uses_land_area() -> None:
    summary = _search_page().items[0]
    html = (FIXTURES / "detail_plot_sale.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.price_pln == 8500000
    assert detail.price_per_m2_pln == 258
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 32910.0
    assert detail.market == "secondary"
    assert detail.city == "Oświęcim"
    assert detail.lat == pytest.approx(50.0343982)
    assert detail.lng == pytest.approx(19.2097782)
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.published_at == 1795542900000

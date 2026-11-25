from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.nieruchomosciowi import NieruchomosciowiAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "nieruchomosciowi"

DETAIL_SLUG = "mieszkanie-na-sprzedaz-2-pokojowe-mieszkanie-38-1-m-rzeszow-ul-hanasiewicza-winda"
PLOT_SLUG = "dzialka-dzialka-0-1289-ha-ruda-lancucka"
RENT_SLUG = "mieszkanie-wynajem-mieszkania-grochow-20m2"


def _adapter() -> NieruchomosciowiAdapter:
    return NieruchomosciowiAdapter(
        source_id="nieruchomosciowi",
        regions=RegionRegistry(load_regions_config()),
    )


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _item(name: str) -> dict[str, Any]:
    payload = json.loads(_load(name))
    assert isinstance(payload, dict)
    return payload


def _summary(slug: str) -> Listing:
    adapter = _adapter()
    query = adapter.queries(("malopolskie",))[0]
    page = adapter.parse_search(_load("search.json"), query)
    return next(item for item in page.items if item.external_id == slug)


def test_queries_use_the_documented_api() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 1
    query = queries[0]
    assert query.key == "all"
    assert query.url == "https://nieruchomosciowi.pl/api/v1/listings?per_page=50"
    assert query.region == "malopolskie"


def test_queries_skip_unknown_regions() -> None:
    adapter = _adapter()
    assert adapter.queries(("podkarpackie",)) == []
    assert adapter.queries(()) == []


def test_with_page_adds_page_parameter() -> None:
    adapter = _adapter()
    query = adapter.queries(("malopolskie",))[0]
    paged = adapter.with_page(query, 2)

    assert paged.page == 2
    assert paged.url == "https://nieruchomosciowi.pl/api/v1/listings?per_page=50&page=2"


def test_detail_url_uses_the_api_endpoint() -> None:
    adapter = _adapter()
    summary = _summary(DETAIL_SLUG)

    assert adapter.detail_url(summary) == (
        f"https://nieruchomosciowi.pl/api/v1/listings/{DETAIL_SLUG}"
    )


def test_parse_search_fixture() -> None:
    adapter = _adapter()
    query = adapter.queries(("malopolskie",))[0]
    page = adapter.parse_search(_load("search.json"), query)

    assert page.current_page == 1
    assert page.total_pages == 2
    assert page.total_items == 54
    assert len(page.items) == 50

    first = page.items[0]
    assert first.source_id == "nieruchomosciowi"
    assert first.external_id == "mieszkanie-piekne-mieszkania-bezczynszowe-6-mieszkan-do-wejscia"
    assert first.url == (
        "https://nieruchomosciowi.pl/oferta/"
        "mieszkanie-piekne-mieszkania-bezczynszowe-6-mieszkan-do-wejscia"
    )
    assert first.title == "PIĘKNE MIESZKANIA BEZCZYNSZOWE  6 MIESZKAŃ DO WEJŚCIA"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.price_pln == 220000
    assert first.price_unit == "total"
    assert first.area_m2 == 28.0
    assert first.rooms == 1
    assert first.city == "Nowy Sącz"
    assert first.district == "Osiedle Szujskiego"
    assert first.address_text == "Nowy Sącz, Henryka Sienkiewicza 13"
    assert first.voivodeship == "malopolskie"
    assert first.lat == 49.6124260042966
    assert first.lng == 20.6947564120771
    assert first.geo_source == "source"
    assert first.seller_type == "private"
    assert first.published_at is not None


def test_parse_search_splits_plots_and_rentals() -> None:
    adapter = _adapter()
    query = adapter.queries(("malopolskie",))[0]
    page = adapter.parse_search(_load("search.json"), query)

    plot = next(item for item in page.items if item.external_id == PLOT_SLUG)
    assert plot.property_type == "plot"
    assert plot.area_m2 is None
    assert plot.land_area_m2 == 1289.0
    assert plot.voivodeship is None

    rentals = [item for item in page.items if item.offer_type == "rent"]
    assert rentals
    assert all(item.price_unit == "monthly" for item in rentals)
    assert any(item.city == "Żory" for item in rentals)


def test_parse_detail_flat_fixture() -> None:
    adapter = _adapter()
    summary = _summary(DETAIL_SLUG)
    detail = adapter.parse_detail(_load("detail-flat.json"), summary)

    assert detail.external_id == DETAIL_SLUG
    assert detail.url == f"https://nieruchomosciowi.pl/oferta/{DETAIL_SLUG}"
    assert detail.title == (
        "Na sprzedaż 2-pokojowe mieszkanie \u2013 38,1 m² | Rzeszów, ul. Hanasiewicza | Winda"
    )
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.market == "secondary"
    assert detail.price_pln == 379000
    assert detail.price_unit == "total"
    assert detail.area_m2 == 38.1
    assert detail.land_area_m2 is None
    assert detail.rooms == 2
    assert detail.floor == 4
    assert detail.floor_label == "4"
    assert detail.city == "Rzeszów"
    assert detail.district == "Staroniwa"
    assert detail.address_text == "Rzeszów"
    assert detail.lat == 50.029551308312705
    assert detail.lng == 21.981191188868472
    assert detail.geo_source == "source"
    assert detail.voivodeship is None
    assert detail.description is not None
    assert "Hanasiewicza" in detail.description
    assert detail.published_at is not None
    assert detail.modified_at is not None
    assert detail.seller_type == "agency"
    assert detail.seller_name == "Res Nieruchomości"
    assert len(detail.images) == 8
    assert detail.extra["nieruchomosciowi"]["address_exact"] is False
    assert "Winda" in detail.extra["nieruchomosciowi"]["features"]


def test_parse_detail_plot_fixture() -> None:
    adapter = _adapter()
    summary = _summary(PLOT_SLUG)
    detail = adapter.parse_detail(_load("detail-plot.json"), summary)

    assert detail.property_type == "plot"
    assert detail.price_pln == 120000
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 1289.0
    assert detail.rooms is None
    assert detail.market == "unknown"
    assert detail.city == "Ruda Łańcucka"
    assert detail.lat == 50.32357628057178
    assert detail.lng == 22.356359400883722
    assert detail.seller_type == "private"
    assert detail.description is not None
    assert detail.description.startswith("Sprzedam działkę")


def test_parse_detail_rent_fixture() -> None:
    adapter = _adapter()
    summary = _summary(RENT_SLUG)
    detail = adapter.parse_detail(_load("detail-rent.json"), summary)

    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.price_pln == 2350
    assert detail.price_unit == "monthly"
    assert detail.area_m2 == 20.0
    assert detail.rooms == 1
    assert detail.floor == 3
    assert detail.city == "Warszawa"
    assert detail.district == "Praga-Południe"
    assert detail.voivodeship is None
    assert detail.seller_type == "private"
    assert detail.description is not None
    assert "Siennickiej" in detail.description


def test_parse_search_rejects_html() -> None:
    adapter = _adapter()
    query = adapter.queries(("malopolskie",))[0]
    with pytest.raises(ParseError):
        adapter.parse_search("<html>blocked</html>", query)

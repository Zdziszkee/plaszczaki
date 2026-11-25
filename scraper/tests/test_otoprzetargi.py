from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.otoprzetargi import OtoprzetargiAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "otoprzetargi"


def _adapter() -> OtoprzetargiAdapter:
    return OtoprzetargiAdapter(
        source_id="otoprzetargi",
        regions=RegionRegistry(load_regions_config()),
    )


def _search_page():
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _adapter().queries(("malopolskie",))[0])


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 2
    assert [query.region for query in queries] == ["malopolskie", "slaskie"]
    assert queries[0].url == ("https://www.otoprzetargi.pl/nieruchomosci?voivodeship=małopolskie")
    assert queries[1].url == ("https://www.otoprzetargi.pl/nieruchomosci?voivodeship=śląskie")
    assert {query.offer_type for query in queries} == {"auction"}


def test_with_page_preserves_region_filter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)
    assert paged.page == 3
    assert paged.url == (
        "https://www.otoprzetargi.pl/nieruchomosci?voivodeship=ma%C5%82opolskie&page=3"
    )


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_items == 1949
    assert page.total_pages == 130
    assert len(page.items) == 15

    first = page.items[0]
    assert first.external_id == (
        "wykaz-czesci-dzialek-stanowiacych-wlasnosc-gminy-rzezawa-"
        "przeznaczonych-do-oddania-w-dzierzawe-pod-instalacje-"
        "urzadzen-infrastruktury-technicznej-2"
    )
    assert first.url.startswith("https://www.otoprzetargi.pl/nieruchomosci/")
    assert first.city == "Rzezawa"
    assert first.voivodeship == "malopolskie"
    assert first.offer_type == "lease"
    assert first.property_type == "plot"
    assert first.images

    biesiadki = next(item for item in page.items if "pierwszym-przetargu" in item.url)
    assert biesiadki.city == "Biesiadki"
    assert biesiadki.offer_type == "auction"
    assert biesiadki.property_type == "house"

    chrzanow = next(item for item in page.items if "syndyk-sprzeda" in item.url)
    assert chrzanow.city == "Chrzanów"
    assert chrzanow.offer_type == "sale"
    assert chrzanow.property_type == "flat"
    assert chrzanow.price_pln == 254_700
    assert chrzanow.area_m2 == 47.6


def test_parse_detail_fixture() -> None:
    page = _search_page()
    summary = next(item for item in page.items if "pierwszym-przetargu" in item.url)
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.title is not None
    assert detail.title.startswith("Ogłoszenie o pierwszym przetargu ustnym")
    assert detail.description is not None
    assert "Wójt Gminy Gnojnik" in detail.description
    assert "Działka ewidencyjna nr 344" in detail.description

    assert detail.offer_type == "auction"
    assert detail.property_type == "house"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 13_530
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 300.0

    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Biesiadki"
    assert detail.address_text == "Biesiadki, Małopolskie"

    assert detail.lat == pytest.approx(49.89081)
    assert detail.lng == pytest.approx(20.66463)
    assert detail.geo_source == "source"

    expected_auction = int(
        datetime(2027, 1, 4, 11, 0, tzinfo=ZoneInfo("Europe/Warsaw")).timestamp() * 1000,
    )
    assert detail.auction_date == expected_auction
    assert detail.auction_round == 1
    assert detail.case_number is None
    assert detail.seller_type == "government"


def test_parse_detail_case_number_and_lease_price() -> None:
    page = _search_page()
    summary = next(item for item in page.items if "starosta-bochenski" in item.url)
    html = (FIXTURES / "detail_case.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.offer_type == "auction"
    assert detail.property_type == "plot"
    assert detail.case_number == "GN.6845.1.12.2026"
    assert detail.price_pln is None
    assert detail.rent_pln == 2_776
    assert detail.price_unit == "unknown"
    assert detail.land_area_m2 == 54_400.0

    expected_auction = int(
        datetime(2026, 12, 15, 11, 0, tzinfo=ZoneInfo("Europe/Warsaw")).timestamp() * 1000,
    )
    assert detail.auction_date == expected_auction
    assert detail.city == "Trawniki"
    assert detail.voivodeship == "malopolskie"
    assert detail.lat == pytest.approx(50.13196)
    assert detail.lng == pytest.approx(20.41294)


def test_parse_detail_rejects_missing_coordinates_or_description() -> None:
    page = _search_page()
    summary = page.items[0]
    html = (FIXTURES / "detail_missing_coords.html").read_text(encoding="utf-8")

    with pytest.raises(ParseError):
        _adapter().parse_detail(html, summary)

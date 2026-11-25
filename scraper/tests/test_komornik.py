from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError, Query
from plaszczaki_scraper.adapters.komornik import (
    KomornikAdapter,
    _auction_round,
    _case_number,
    _hydrate,
)
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> KomornikAdapter:
    return KomornikAdapter(
        source_id="komornik",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(region: str = "malopolskie") -> Query:
    return next(query for query in _adapter().queries((region,)) if query.region == region)


def _search_page():
    html = (FIXTURES / "komornik" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query())


def _page_with_flat(flat: list[object]) -> str:
    return (
        "<html><body>"
        f'<script type="application/json" id="__NUXT_DATA__">{json.dumps(flat)}</script>'
        "</body></html>"
    )


def test_queries_use_server_side_province_filter() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert {query.key for query in queries} == {"malopolskie:all", "slaskie:all"}
    assert {query.offer_type for query in queries} == {"auction"}
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}

    malopolska = next(query for query in queries if query.key == "malopolskie:all")
    assert malopolska.url == (
        "https://licytacje.komornik.pl/wyszukiwarka-licytacji"
        "?mainCategory=REAL_ESTATE&province=ma%C5%82opolskie"
    )
    slaskie = next(query for query in queries if query.key == "slaskie:all")
    assert "province=%C5%9Bl%C4%85skie" in slaskie.url


def test_with_page_uses_offset_pagination() -> None:
    query = _query()

    first = _adapter().with_page(query, 1)
    second = _adapter().with_page(query, 2)
    third = _adapter().with_page(query, 3)

    assert first.url.endswith("&offset=0")
    assert second.url.endswith("&offset=20")
    assert third.url.endswith("&offset=40")
    assert (first.page, second.page, third.page) == (1, 2, 3)


def test_hydrate_resolves_flattened_payload() -> None:
    flat = [
        ["ShallowReactive", 1],
        {"data": 2, "value": 5, "date": 6, "missing": -1},
        {"search-items": 3},
        {"items": 4, "count": 7},
        [5, 5],
        {"id": 8, "title": 9, "self": 5},
        1_234_567_890,
        2,
        4242,
        "Aukcja",
    ]

    payload = _hydrate(flat)
    block = payload["data"]["search-items"]

    assert block["count"] == 2
    assert len(block["items"]) == 2
    assert block["items"][0] is block["items"][1]
    assert block["items"][0]["title"] == "Aukcja"
    assert block["items"][0]["self"] is block["items"][0]
    assert payload["value"] is block["items"][0]
    assert payload["date"] == 1_234_567_890
    assert payload["missing"] is None


def test_hydrate_rejects_dangling_references() -> None:
    with pytest.raises(ParseError):
        _hydrate([{"broken": 9}])


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_items == 257
    assert page.total_pages == 13
    assert len(page.items) == 3

    house = page.items[0]
    assert house.external_id == "81995"
    assert house.url.startswith("https://licytacje.komornik.pl/licytacje/81995/")
    assert house.title is not None
    assert house.title.startswith("nieruchomość gruntowa położona w Krakowie-Krowodrzy")
    assert house.offer_type == "auction"
    assert house.property_type == "house"
    assert house.price_kind == "starting"
    assert house.price_pln == 753198
    assert house.area_m2 == 125.0
    assert house.city == "Kraków"
    assert house.voivodeship == "malopolskie"
    assert house.street == "Kurozwęckiego 14"
    assert house.address_text == "Kurozwęckiego 14, 30-198"
    assert house.extra["postal_code"] == "30-198"
    assert house.auction_date == int(
        datetime.fromisoformat("2026-11-26T14:00:00+01:00").timestamp() * 1000,
    )
    assert house.extra["komornik"]["notice_id"] == 49138
    assert house.extra["komornik"]["estimate"] == 1004264


def test_flat_number_and_postal_code_are_decoded() -> None:
    flat = _search_page().items[1]

    assert flat.external_id == "91625"
    assert flat.url == "https://licytacje.komornik.pl/licytacje/91625/lokal-mieszkalny"
    assert flat.property_type == "flat"
    assert flat.price_pln == 393750
    assert flat.area_m2 is None
    assert flat.street == "Wysokie 1/63"
    assert flat.address_text == "Wysokie 1/63, 31-818"
    assert flat.extra["postal_code"] == "31-818"
    assert flat.auction_date == int(
        datetime.fromisoformat("2026-12-02T10:00:00+01:00").timestamp() * 1000,
    )


def test_plot_area_is_parsed_from_title() -> None:
    plot = _search_page().items[2]

    assert plot.external_id == "90915"
    assert plot.property_type == "plot"
    assert plot.price_pln == 101519
    assert plot.area_m2 == 1448.0
    assert plot.land_area_m2 == 1448.0
    assert plot.city == "Łapanów"
    assert plot.extra["postal_code"] == "32-740"


def test_street_includes_building_and_flat_number() -> None:
    house, flat, _plot = _search_page().items

    assert house.street == "Kurozwęckiego 14"
    assert flat.street == "Wysokie 1/63"
    # The human readable address keeps the postal code.
    assert house.address_text == "Kurozwęckiego 14, 30-198"


def test_street_without_a_building_number_stays_bare() -> None:
    listing = _adapter()._summary(
        {
            "id": 3,
            "title": "lokal mieszkalny",
            "province": "malopolskie",
            "address": {"province": "malopolskie", "city": "Brzesko", "street": "Partyzantów"},
        },
        _query(),
        {},
    )

    assert listing is not None
    assert listing.street == "Partyzantów"
    assert listing.address_text == "Partyzantów"


def test_zero_coordinates_are_never_emitted() -> None:
    for item in _search_page().items:
        assert item.lat is None
        assert item.lng is None
        assert item.lat != 0.0
        assert item.lng != 0.0
        assert item.geo_source is None


def test_case_number_and_round_helpers() -> None:
    assert _case_number({"caseNumber": "Km 42/25"}, None) == "Km 42/25"
    assert _case_number({}, "Sprzedaż, sygn. akt Km 123/24, nieruchomość") == "Km 123/24"
    assert _case_number({}, "brak sygnatury") is None

    assert _auction_round({"auctionRound": 3}, None) == 3
    assert _auction_round({}, "Pierwsza licytacja nieruchomości") == 1
    assert _auction_round({}, "II licytacja") == 2
    assert _auction_round({}, "brak informacji") is None


def test_summary_uses_payload_case_and_round() -> None:
    listing = _adapter()._summary(
        {
            "id": 2,
            "title": "lokal mieszkalny",
            "province": "malopolskie",
            "caseNumber": "Km 7/25",
            "auctionRound": 2,
            "address": {"province": "malopolskie", "city": "Kraków"},
        },
        _query(),
        {},
    )

    assert listing is not None
    assert listing.case_number == "Km 7/25"
    assert listing.auction_round == 2


def test_out_of_scope_province_is_preserved() -> None:
    listing = _adapter()._summary(
        {
            "id": 1,
            "title": "lokal mieszkalny",
            "province": "mazowieckie",
            "address": {"province": "mazowieckie", "city": "Warszawa"},
        },
        _query(),
        {},
    )

    assert listing is not None
    assert listing.voivodeship == "mazowieckie"
    assert listing.url == "https://licytacje.komornik.pl/licytacje/1"


def test_parse_detail_returns_summary_unchanged() -> None:
    summary = _search_page().items[0]

    assert _adapter().parse_detail("<html></html>", summary) is summary


def test_empty_result_page_is_not_an_error() -> None:
    flat: list[object] = [
        ["ShallowReactive", 1],
        {"data": 2, "serverRendered": 6},
        {"search-items": 3},
        {"items": 4, "count": 5},
        [],
        0,
        True,
    ]

    page = _adapter().parse_search(_page_with_flat(flat), _query())

    assert page.items == []
    assert page.total_items == 0
    assert page.total_pages == 1


def test_unrecognised_page_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>maintenance</body></html>", _query())

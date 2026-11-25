from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.adapters.pkp import PkpAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> PkpAdapter:
    return PkpAdapter(
        source_id="pkp",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(region: str = "malopolskie") -> Query:
    return next(
        query for query in _adapter().queries(("malopolskie", "slaskie")) if query.region == region
    )


def _search_page(region: str = "malopolskie") -> SearchPage:
    html = (FIXTURES / "pkp" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query(region))


def test_queries_use_province_filters() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert queries[0].url == "https://www.pkp.pl/pl/sprzedaz?menu=2&province_id=6"
    assert queries[1].url == "https://www.pkp.pl/pl/sprzedaz?menu=2&province_id=12"
    assert {query.offer_type for query in queries} == {"sale"}


def test_with_page_keeps_single_url() -> None:
    query = _query()
    paged = _adapter().with_page(query, 3)

    assert paged.url == query.url
    assert paged.page == 1


def test_parse_search_filters_by_region() -> None:
    malopolska = _search_page("malopolskie")
    slaskie = _search_page("slaskie")

    assert malopolska.total_pages == 1
    assert malopolska.total_items == 2
    assert [item.external_id for item in malopolska.items] == ["21994"]
    assert [item.external_id for item in slaskie.items] == ["30945"]

    tarnow = malopolska.items[0]
    assert tarnow.url == "https://www.pkp.pl/pl/sprzedaz?menu=2&show=21994"
    assert tarnow.title == "Grunty - Tarnów"
    assert tarnow.property_type == "plot"
    assert tarnow.offer_type == "sale"
    assert tarnow.price_pln is None
    assert tarnow.city == "Tarnów"
    assert tarnow.street == "Sportowa Ogrodowa"
    assert tarnow.address_text == "33-100 Tarnów, Sportowa Ogrodowa"
    assert tarnow.lat == pytest.approx(50.0046)
    assert tarnow.lng == pytest.approx(20.9552)
    assert tarnow.geo_source == "source"
    assert tarnow.extra["pkp"]["kategoria"] == "GRUNTY"


def test_parse_detail_fixture() -> None:
    summary = _search_page().items[0]
    html = (FIXTURES / "pkp" / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == "21994"
    assert detail.title == (
        "Atrakcyjna działka w Tarnowie w dzielnicy Strusina o powierzchni 1,0643 ha"
    )
    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 2673000
    assert detail.land_area_m2 == 10643.0
    assert detail.area_m2 is None
    assert detail.voivodeship == "malopolskie"
    assert detail.county == "Tarnów"
    assert detail.commune == "M. Tarnów"
    assert detail.city == "Tarnów"
    assert detail.street == "Sportowa Ogrodowa"
    assert detail.address_text == "33-100 Tarnów, Sportowa Ogrodowa"
    assert detail.lat == pytest.approx(50.0046)
    assert detail.lng == pytest.approx(20.9552)
    assert detail.geo_source == "source"
    assert detail.seller_type == "government"
    assert detail.description is not None
    assert "Tarnów" in detail.description
    assert detail.images
    assert detail.extra["pkp"]["numer_oferty"] == "21994"
    assert detail.extra["pkp"]["typ_postepowania"] == "Przetarg ustny nieograniczony"


def test_parse_detail_falls_back_to_search_coordinates() -> None:
    summary = replace(_search_page().items[0], external_id="999999", lat=49.62, lng=20.95)
    html = (FIXTURES / "pkp" / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.lat == 49.62
    assert detail.lng == 20.95
    assert detail.geo_source == "source"


def test_candidate_regions_uses_city_and_bounding_boxes() -> None:
    adapter = _adapter()

    assert adapter._candidate_regions("Katowice", 50.26, 19.02) == {"slaskie"}
    assert adapter._candidate_regions("Tarnów", 50.0046, 20.9552) == {"malopolskie"}
    # Unknown city in the eastern part of Małopolska: only one bbox matches.
    assert adapter._candidate_regions("Grybów", 49.62, 20.95) == {"malopolskie"}


def test_missing_geojson_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>maintenance</body></html>", _query())

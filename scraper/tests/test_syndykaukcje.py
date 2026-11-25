from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.syndykaukcje import SyndykAukcjeAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "syndykaukcje"


def _adapter() -> SyndykAukcjeAdapter:
    return SyndykAukcjeAdapter(
        source_id="syndykaukcje",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert queries[0].key == "malopolskie:nieruchomosci"
    assert queries[0].url == (
        "https://syndykaukcje.pl/lista-ogloszen/nieruchomosci/?wojewodztwo=malopolskie"
    )
    assert queries[1].url.endswith("?wojewodztwo=slaskie")


def test_queries_skip_unknown_regions() -> None:
    assert _adapter().queries(("opolskie",)) == []


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("slaskie",))[0]
    paged = _adapter().with_page(query, 2)

    assert paged.page == 2
    assert paged.url.endswith("?wojewodztwo=slaskie&paginacja=2")


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 1
    assert page.total_items is None
    assert len(page.items) == 3

    first = page.items[0]
    assert first.external_id == "84287"
    assert first.url == (
        "https://syndykaukcje.pl/ogloszenia/syndyk-sprzeda-lokal-mieszkalny-w-tarnowie/"
    )
    assert first.title == "syndyk sprzeda lokal mieszkalny w Tarnowie"
    assert first.price_pln == 242400
    assert first.price_kind == "starting"
    assert first.offer_type == "auction"
    assert first.property_type == "flat"
    assert first.voivodeship == "malopolskie"
    assert first.city == "Tarnów"
    assert first.seller_name == "Joanna"
    assert first.extra["syndykaukcje"]["slug"] == "syndyk-sprzeda-lokal-mieszkalny-w-tarnowie"

    second = page.items[1]
    assert second.city == "Jabłonka"
    assert second.county == "nowotarski"
    assert second.property_type == "plot"
    assert second.seller_name == "Redress Sp. z o.o."
    assert len(second.images) == 3


def test_parse_search_advances_to_next_page_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    monkeypatch.setattr("plaszczaki_scraper.adapters.syndykaukcje.PAGE_SIZE", 2)

    page = _adapter().parse_search(html, query)

    assert len(page.items) == 3
    assert page.total_pages == 2


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
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "84287"
    assert detail.title == "syndyk sprzeda lokal mieszkalny w Tarnowie"
    assert detail.price_pln == 242400
    assert detail.offer_type == "auction"
    assert detail.property_type == "flat"
    assert detail.market == "secondary"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Tarnów"
    assert detail.address_text == "33-100 Tarnów, Polska"
    assert detail.lat == 50.0121011
    assert detail.lng == 20.9858407
    assert detail.geo_source == "source"
    assert detail.case_number == "TR1T/GUp-s/100/2025"
    assert detail.auction_date == 1798416000000
    assert detail.description is not None
    assert "konkurs ofert" in detail.description
    assert detail.extra["syndykaukcje"]["category"] == "Nieruchomości"
    assert detail.extra["syndykaukcje"]["views"] == 1168

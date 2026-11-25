from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.amw_nieruchomosci import AmwAdapter
from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> AmwAdapter:
    return AmwAdapter(
        source_id="amw-nieruchomosci",
        regions=RegionRegistry(load_regions_config()),
    )


def _query() -> Query:
    return _adapter().queries(("malopolskie", "slaskie"))[0]


def _search_page() -> SearchPage:
    html = (FIXTURES / "amw-nieruchomosci" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query())


def _item(external_id: str) -> Listing:
    return next(item for item in _search_page().items if item.external_id == external_id)


def test_queries_use_one_national_tender_list() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 1
    query = queries[0]
    assert query.key == "national:przetargi"
    assert query.region == "malopolskie,slaskie"
    assert query.url == (
        "https://amw.com.pl/pl/nieruchomosci/przetargi-nieruchomosci"
        "/wyniki-wyszukiwania/page,0,limit,50"
    )


def test_with_page_rewrites_the_path_segment() -> None:
    query = _query()

    first = _adapter().with_page(query, 1)
    second = _adapter().with_page(query, 2)

    assert first.url.endswith("page,0,limit,50")
    assert first.page == 1
    assert second.url.endswith("page,1,limit,50")
    assert second.page == 2
    assert "?page=" not in second.url


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_pages == 2
    assert len(page.items) == 50

    sale = _item("12053")
    assert sale.url == (
        "https://amw.com.pl/pl/nieruchomosci/przetargi-nieruchomosci"
        "/tarnowskie-gory-ul-wyspianskiego-12-3-12053"
    )
    assert sale.title == "Tarnowskie Góry, ul. Wyspiańskiego 12/3"
    assert sale.offer_type == "auction"
    assert sale.property_type == "flat"
    assert sale.price_kind == "starting"
    assert sale.price_unit == "total"
    assert sale.price_pln == 349000
    assert sale.area_m2 == 73.96
    assert sale.land_area_m2 is None
    assert sale.voivodeship == "slaskie"
    assert sale.county == "tarnogórski"
    assert sale.commune == "Tarnowskie Góry"
    assert sale.city == "Tarnowskie Góry"
    assert sale.street == "Wyspiańskiego 12/3"
    assert sale.address_text == "ul. Wyspiańskiego 12/3, Tarnowskie Góry"
    assert sale.auction_date == 1796637600000
    assert sale.lat is None
    assert sale.lng is None
    assert sale.geo_source is None

    lease = _item("11200")
    assert lease.offer_type == "lease"
    assert lease.price_kind == "starting"
    assert lease.price_unit == "monthly"
    assert lease.price_pln == 2650
    assert lease.city == "Morąg"
    assert lease.street == "Wojska Polskiego 21"
    assert lease.county == "ostródzki"
    assert lease.auction_date == 1795680000000


def test_parse_detail_fixture() -> None:
    summary = _item("12053")
    html = (FIXTURES / "amw-nieruchomosci" / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert detail.title == summary.title
    assert detail.offer_type == "auction"
    assert detail.property_type == "flat"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 349000
    assert detail.area_m2 == 73.96
    assert detail.land_area_m2 is None
    assert detail.voivodeship == "slaskie"
    assert detail.county == "tarnogórski"
    assert detail.commune == "Tarnowskie Góry"
    assert detail.city == "Tarnowskie Góry"
    assert detail.street == "Wyspiańskiego 12/3"
    assert detail.address_text == "ul. Wyspiańskiego 12/3, Tarnowskie Góry"
    assert detail.auction_date == 1796637600000
    assert detail.lat is None
    assert detail.lng is None
    assert detail.geo_source is None
    assert detail.seller_type == "government"
    assert detail.description is not None
    assert "Tarnowskich Gór" in detail.description
    assert len(detail.images) == 2
    assert detail.extra["amw"]["heading"] == "Ogłoszenie o przetargu - Sprzedaż"


def test_empty_result_page_is_not_an_error() -> None:
    html = '<html><body><div class="row real-estate-list-container"></div></body></html>'

    page = _adapter().parse_search(html, _query())

    assert page.items == []
    assert page.total_pages == 1


def test_unrecognised_page_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>maintenance</body></html>", _query())

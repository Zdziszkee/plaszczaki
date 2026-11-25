from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.adapters.poczta_polska import PocztaPolskaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> PocztaPolskaAdapter:
    return PocztaPolskaAdapter(
        source_id="poczta-polska",
        regions=RegionRegistry(load_regions_config()),
    )


def _queries() -> dict[str, Query]:
    return {query.key: query for query in _adapter().queries(("malopolskie", "slaskie"))}


def _search_page(name: str, query: Query) -> SearchPage:
    html = (FIXTURES / "poczta-polska" / name).read_text(encoding="utf-8")
    return _adapter().parse_search(html, query)


def _item(page: SearchPage, external_id: str) -> Listing:
    return next(item for item in page.items if item.external_id == external_id)


def test_queries_cover_sale_and_rent_lists() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert {query.key for query in queries} == {"national:sprzedaz", "national:wynajem"}
    assert {query.offer_type for query in queries} == {"sale", "rent"}
    sale = next(query for query in queries if query.key == "national:sprzedaz")
    assert sale.url == "https://nieruchomosci.poczta-polska.pl/oferty/sprzedaz"


def test_with_page_uses_query_param_pagination() -> None:
    query = _queries()["national:wynajem"]

    first = _adapter().with_page(query, 1)
    second = _adapter().with_page(query, 2)

    assert "page=" not in first.url
    assert first.page == 1
    assert second.url.endswith("?page=2")
    assert second.page == 2


def test_parse_sale_search_fixture() -> None:
    page = _search_page("search_sale.html", _queries()["national:sprzedaz"])

    assert page.current_page == 1
    assert page.total_pages == 1
    assert len(page.items) == 2

    auction = _item(page, "431-6-1")
    assert auction.url == (
        "https://nieruchomosci.poczta-polska.pl/oferta/431-6-1/"
        "krakow-grochowska-aukcja-budynek-biurowy-krakow"
    )
    assert auction.title == "Aukcja | Budynek biurowy | Kraków"
    assert auction.offer_type == "auction"
    assert auction.property_type == "commercial"
    assert auction.price_kind == "starting"
    assert auction.price_unit == "total"
    assert auction.price_pln == 28300000
    assert auction.area_m2 == 6739.78
    assert auction.voivodeship == "malopolskie"
    assert auction.county is None
    assert auction.city == "Kraków"
    assert auction.street == "Grochowska"
    assert auction.address_text == "Grochowska, Kraków"
    assert auction.lat is None
    assert auction.lng is None

    outside = _item(page, "814-6-1")
    assert outside.voivodeship == "warmińsko-mazurskie"
    assert outside.city == "Olsztyn"
    assert outside.street == "Seweryna Pieniężnego"


def test_parse_rent_search_fixture() -> None:
    query = _adapter().with_page(_queries()["national:wynajem"], 2)
    page = _search_page("search_rent.html", query)

    assert page.current_page == 2
    assert page.total_pages == 5
    assert len(page.items) == 12

    flat = _item(page, "823-2-2")
    assert flat.offer_type == "rent"
    assert flat.property_type == "flat"
    assert flat.price_kind == "asking"
    assert flat.price_unit == "monthly"
    assert flat.price_pln == 1625
    assert flat.area_m2 == 49.27
    assert flat.rooms == 2
    assert flat.voivodeship == "slaskie"
    assert flat.county is None
    assert flat.city == "Katowice"
    assert flat.street == "Rolna"
    assert flat.address_text == "Rolna, Katowice"
    assert flat.lat is None
    assert flat.lng is None

    garage = _item(page, "820-4-2")
    assert garage.property_type == "garage"
    assert garage.county == "tarnogórski"
    assert garage.city == "Tarnowskie Góry"
    assert garage.street == "J. Piłsudskiego"

    bielsko = _item(page, "819-6-2")
    assert bielsko.voivodeship == "slaskie"
    assert bielsko.county is None
    assert bielsko.city == "Bielsko-Biała"
    assert bielsko.street == "1 Maja"


def test_parse_sale_detail_fixture() -> None:
    summary = _item(
        _search_page("search_sale.html", _queries()["national:sprzedaz"]),
        "431-6-1",
    )
    html = (FIXTURES / "poczta-polska" / "detail_sale.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert "Aukcja" in (detail.title or "")
    assert detail.offer_type == "auction"
    assert detail.property_type == "commercial"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 28300000
    assert detail.price_per_m2_pln == 4198
    assert detail.area_m2 == 6739.78
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.street == "Grochowska"
    assert detail.auction_date == 1791280800000
    assert detail.lat is None
    assert detail.lng is None
    assert detail.geo_source is None
    assert detail.seller_type == "government"
    assert detail.description is not None
    assert "OGŁOSZENIE O SPRZEDAŻY" in detail.description
    assert detail.extra["poczta"]["numer_oferty"] == "431"
    assert detail.images


def test_parse_rent_detail_fixture() -> None:
    query = _adapter().with_page(_queries()["national:wynajem"], 2)
    summary = _item(_search_page("search_rent.html", query), "823-2-2")
    html = (FIXTURES / "poczta-polska" / "detail_rent.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.price_kind == "asking"
    assert detail.price_unit == "monthly"
    assert detail.price_pln == 1625
    assert detail.price_per_m2_pln == 33
    assert detail.area_m2 == 49.27
    assert detail.rooms == 2
    assert detail.auction_date is None
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Katowice"
    assert detail.street == "Rolna"
    assert detail.description is not None
    assert "KATOWICE" in detail.description


def test_unrecognised_page_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search(
            "<html><body>maintenance</body></html>", _queries()["national:wynajem"]
        )

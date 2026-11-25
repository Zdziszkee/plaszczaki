from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.oferty_net import OfertyNetAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> OfertyNetAdapter:
    return OfertyNetAdapter(
        source_id="oferty-net",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(key: str, region: str = "malopolskie"):
    return next(query for query in _adapter().queries((region,)) if query.key == f"{region}:{key}")


def _search_html() -> str:
    return (FIXTURES / "oferty-net" / "search.html").read_text(encoding="utf-8")


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 22
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(
        query.url == "https://www.oferty.net/mieszkania-sprzedaz,malopolskie" for query in queries
    )
    assert any(query.url == "https://www.oferty.net/pokoje-wynajem,slaskie" for query in queries)


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)
    assert paged.url.endswith("?page=3")
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    page = _adapter().parse_search(_search_html(), _query("mieszkania:sprzedaz"))
    assert page.current_page == 1
    assert page.total_pages == 54
    assert len(page.items) == 1

    item = page.items[0]
    assert item.external_id == "1543386433"
    assert item.url == (
        "https://www.oferty.net/"
        "mieszkanie-na-sprzedaz-krakow-pradnik-czerwony-55m2-2-pokoje-710000-pln-ba,1543386433"
    )
    assert item.offer_type == "sale"
    assert item.property_type == "flat"
    assert item.price_pln == 710000
    assert item.price_per_m2_pln == 12909
    assert item.area_m2 == 55.0
    assert item.rooms == 2
    assert item.voivodeship == "malopolskie"
    assert item.city == "Kraków"
    assert item.district == "Os. Prądnik Czerwony"
    assert item.published_at is not None


def test_parse_search_filters_cross_listed_offer_type() -> None:
    page = _adapter().parse_search(_search_html(), _query("mieszkania:wynajem"))
    assert [item.external_id for item in page.items] == ["1543382919"]
    assert page.items[0].offer_type == "rent"
    assert page.items[0].price_unit == "monthly"


def test_parse_search_plot_uses_land_area() -> None:
    page = _adapter().parse_search(_search_html(), _query("dzialki:sprzedaz"))
    assert len(page.items) == 1
    item = page.items[0]
    assert item.property_type == "plot"
    assert item.area_m2 is None
    assert item.land_area_m2 == 3220.0
    assert item.city == "Bukowina Tatrzańska"
    assert item.price_pln == 1690000


def test_parse_detail_fixture() -> None:
    summary = _adapter().parse_search(_search_html(), _query("mieszkania:sprzedaz")).items[0]
    html = (FIXTURES / "oferty-net" / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert (detail.title or "").startswith("Mieszkanie na sprzedaż - Błękitna")
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.price_pln == 299000
    assert detail.price_per_m2_pln == 6500
    assert detail.area_m2 == 46.0
    assert detail.land_area_m2 is None
    assert detail.rooms == 3
    assert detail.floor == 4
    assert detail.floor_label == "4"
    assert detail.build_year == 1980
    assert detail.market == "secondary"
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Katowice"
    assert detail.district == "Katowice Koszutka"
    assert detail.street == "Błękitna"
    assert detail.address_text == "Katowice, Koszutka, Błękitna"
    assert detail.lat == 50.2725
    assert detail.lng == 19.0101
    assert detail.geo_source == "source"
    assert detail.seller_type == "agency"
    assert detail.seller_name == "ADM HOME&EVENT"
    assert detail.published_at is not None
    assert detail.description is not None
    assert detail.description.startswith("Katowice ul. Błękitna")
    assert len(detail.images) == 2


def test_parse_detail_requires_coordinates() -> None:
    summary = _adapter().parse_search(_search_html(), _query("mieszkania:sprzedaz")).items[0]
    html = (
        "<html><head><title>Bez mapy</title></head><body>"
        '<div class="header"><h1>Kraków</h1></div>'
        "</body></html>"
    )
    with pytest.raises(ParseError):
        _adapter().parse_detail(html, summary)

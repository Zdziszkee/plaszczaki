from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.nieruchomosci_online import NieruchomosciOnlineAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "nieruchomosci-online"


def _adapter() -> NieruchomosciOnlineAdapter:
    return NieruchomosciOnlineAdapter(
        source_id="nieruchomosci-online",
        regions=RegionRegistry(load_regions_config()),
    )


def _query():
    return _adapter().queries(("malopolskie",))[0]


def _search_page():
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query())


def test_queries_cover_both_regions_and_categories() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 20
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    urls = {query.url for query in queries}
    assert "https://malopolskie.nieruchomosci-online.pl/mieszkania,sprzedaz/" in urls
    assert "https://slaskie.nieruchomosci-online.pl/dzialki,wynajem/" in urls
    assert "https://slaskie.nieruchomosci-online.pl/budynki-uzytkowe,sprzedaz/" in urls

    lease = next(query for query in queries if query.url.endswith("/dzialki,wynajem/"))
    assert lease.offer_type == "lease"
    assert lease.property_type == "plot"


def test_with_page_uses_p_parameter() -> None:
    adapter = _adapter()
    query = _query()

    paged = adapter.with_page(query, 3)
    params = dict(parse_qsl(urlsplit(paged.url).query))

    assert paged.page == 3
    assert params == {"p": "3"}
    assert urlsplit(paged.url).path == "/mieszkania,sprzedaz/"

    first = adapter.with_page(query, 1)
    assert dict(parse_qsl(urlsplit(first.url).query)) == {"p": "1"}


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_pages == 135
    assert page.total_items == 9754
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "26840504"
    assert first.url == (
        "https://krakow.nieruchomosci-online.pl/"
        "mieszkanie,kawalerka,z-aneksem-kuchennym/26840504.html"
    )
    assert first.title == "Sprzedam Mieszkanie Kraków - 35,56 m²"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.market == "unknown"
    assert first.price_kind == "asking"
    assert first.price_pln == 470000
    assert first.price_per_m2_pln == 13217
    assert first.area_m2 == 35.56
    assert first.rooms == 1
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.street == "Reduta"
    assert first.address_text == "Reduta, Kraków"

    second = page.items[1]
    assert second.external_id == "24538587"
    assert second.market == "primary"
    assert second.price_pln is None
    assert second.price_kind == "call"
    assert second.area_m2 == 71.27
    assert second.rooms == 3


def test_parse_detail_fixture() -> None:
    adapter = _adapter()
    summary = _search_page().items[0]
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    detail = adapter.parse_detail(html, summary)

    assert detail.external_id == "26840504"
    assert detail.title == summary.title
    assert detail.price_pln == 470000
    assert detail.price_per_m2_pln == 13217
    assert detail.area_m2 == 35.56
    assert detail.rooms == 1
    assert detail.floor == 0
    assert detail.floor_label == "0"
    assert detail.build_year == 2009
    assert detail.market == "unknown"

    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.street == "Reduta"
    assert detail.district == "Prądnik Czerwony"
    assert detail.address_text == "Reduta, Kraków"

    assert detail.lat == 50.09025
    assert detail.lng == 19.9564359
    assert detail.geo_source == "source"

    assert detail.seller_type == "agency"
    assert detail.seller_name == "Aleksandra Czelny"
    assert len(detail.images) == 2
    assert detail.published_at is not None
    assert detail.description is not None
    assert "ATUTY" in detail.description
    assert "Rozwiń opis" not in detail.description
    assert "(...)" not in detail.description

    extra = detail.extra["nieruchomosci-online"]
    assert extra["additional_property"]["Floor size"] == "35.56 m²"
    assert extra["agent"]["name"] == "Aleksandra Czelny"


def test_parse_plot_detail_uses_land_area() -> None:
    summary = Listing(
        source_id="nieruchomosci-online",
        external_id="27046558",
        url="https://trzebinia.nieruchomosci-online.pl/dzialka,ogrodzona/27046558.html",
        title="Działka przemysłowa, ul. Lipcowa",
        offer_type="lease",
        property_type="plot",
        market="unknown",
        voivodeship="malopolskie",
        city="Trzebinia",
    )
    html = (FIXTURES / "detail_plot.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.property_type == "plot"
    assert detail.offer_type == "lease"
    assert detail.price_pln == 20000
    assert detail.area_m2 == 13200.0
    assert detail.land_area_m2 == 13200.0
    assert detail.lat == 50.1746701
    assert detail.lng == 19.4579739
    assert detail.description == (
        "Działka przemysłowa o powierzchni 1,32 ha, uzbrojona, z dostępem do drogi."
    )


def test_parse_empty_search_page() -> None:
    html = (FIXTURES / "empty.html").read_text(encoding="utf-8")

    page = _adapter().parse_search(html, _query())

    assert page.items == []
    assert page.total_items == 0
    assert page.total_pages == 1


def test_parse_search_without_json_ld_raises() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>Nie ma danych</body></html>", _query())

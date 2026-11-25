from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.domiporta import DomiportaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "domiporta"


def _adapter() -> DomiportaAdapter:
    return DomiportaAdapter(
        source_id="domiporta",
        regions=RegionRegistry(load_regions_config()),
    )


def _query():
    return _adapter().queries(("malopolskie",))[0]


def _premium_query():
    return next(query for query in _adapter().queries(("malopolskie",)) if ":premium:" in query.key)


def _search_page():
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query())


def test_queries_cover_both_regions_categories_and_premium() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 30
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}

    urls = {query.url for query in queries}
    assert "https://www.domiporta.pl/mieszkanie/sprzedam/malopolskie" in urls
    assert "https://www.domiporta.pl/garaz/wynajme/slaskie" in urls
    assert "https://www.domiporta.pl/dzialka/wydzierzawie/malopolskie" in urls
    assert (
        "https://www.domiporta.pl/nieruchomosci-premium/nieruchomosci/sprzedam"
        "?Localization=malopolskie"
    ) in urls
    assert (
        "https://www.domiporta.pl/nieruchomosci-premium/nieruchomosci/wynajme?Localization=slaskie"
    ) in urls

    lease = next(
        query for query in queries if query.url.endswith("/dzialka/wydzierzawie/malopolskie")
    )
    assert lease.offer_type == "lease"
    assert lease.property_type == "plot"

    premium = next(query for query in queries if query.key == "malopolskie:premium:sprzedam")
    assert premium.property_type == "other"
    assert premium.offer_type == "sale"


def test_with_page_uses_page_number_and_keeps_filters() -> None:
    adapter = _adapter()

    paged = adapter.with_page(_query(), 3)
    assert dict(parse_qsl(urlsplit(paged.url).query)) == {"PageNumber": "3"}
    assert urlsplit(paged.url).path == "/mieszkanie/sprzedam/malopolskie"
    assert paged.page == 3

    premium = adapter.with_page(_premium_query(), 2)
    assert dict(parse_qsl(urlsplit(premium.url).query)) == {
        "Localization": "malopolskie",
        "PageNumber": "2",
    }


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_pages == 153
    assert page.total_items == 5474
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "157024063"
    assert first.url == (
        "https://www.domiporta.pl/nieruchomosci/"
        "sprzedam-mieszkanie-trzypokojowe-krakow-nowa-huta-stare-wislisko-96m2/157024063"
    )
    assert first.title == "Mieszkanie trzypokojowe na sprzedaż: Kraków Nowa Huta: Stare Wiślisko"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.market == "unknown"
    assert first.price_kind == "asking"
    assert first.price_pln == 1314172
    assert first.price_per_m2_pln == 13705
    assert first.area_m2 == 95.89
    assert first.rooms == 3
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.street == "Stare Wiślisko"
    assert first.address_text == "Stare Wiślisko, Kraków"
    assert first.images == ["https://galeria.domiporta.pl/pictures/big/18/83/54/example.jpg"]
    assert first.seller_type == "agency"
    assert first.seller_name is None
    assert first.published_at is not None

    second = page.items[1]
    assert second.external_id == "157000001"
    assert second.price_pln == 1700
    assert second.price_per_m2_pln == 34
    assert second.area_m2 == 50.0
    assert second.rooms == 2
    assert second.city == "Tarnów"
    assert second.seller_name == "Biuro Testowe"


def test_parse_detail_fixture() -> None:
    adapter = _adapter()
    summary = _search_page().items[0]
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    detail = adapter.parse_detail(html, summary)

    assert detail.external_id == "157024063"
    assert detail.title == summary.title
    assert detail.price_pln == 1314172
    assert detail.price_per_m2_pln == 13705
    assert detail.area_m2 == 95.89
    assert detail.rooms == 3
    assert detail.floor == 1
    assert detail.floor_label == "1"
    assert detail.build_year == 2025
    assert detail.market == "unknown"

    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.street == "Stare Wiślisko"
    assert detail.address_text == "Stare Wiślisko, Kraków"

    assert detail.lat == 50.0602369
    assert detail.lng == 20.0576478
    assert detail.geo_source == "source"

    assert detail.seller_type == "agency"
    assert detail.seller_name == "Biuro Testowe"
    assert len(detail.images) == 2
    assert detail.published_at is not None
    assert detail.description is not None
    assert "Ogromny salon" in detail.description
    assert "..." not in detail.description

    extra = detail.extra["domiporta"]
    assert extra["premium"] is False
    assert extra["additional_property"]["Typ budynku"] == "dom wielorodzinny"
    assert extra["amenity_feature"][0]["name"] == "balkon"


def test_parse_empty_search_page() -> None:
    html = (FIXTURES / "empty.html").read_text(encoding="utf-8")

    page = _adapter().parse_search(html, _premium_query())

    assert page.items == []
    assert page.total_items == 0
    assert page.total_pages == 1


def test_parse_search_without_json_ld_or_empty_marker_raises() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>Nie ma danych</body></html>", _query())

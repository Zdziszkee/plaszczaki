from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.base import Query
from plaszczaki_scraper.adapters.gdom import CATEGORIES, GdomAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> GdomAdapter:
    return GdomAdapter(
        source_id="gdom",
        regions=RegionRegistry(load_regions_config()),
    )


def _query() -> Query:
    queries = _adapter().queries(("malopolskie",))
    return next(query for query in queries if query.key == "malopolskie:mieszkania-na-sprzedaz")


def _search_summary():
    html = (FIXTURES / "gdom" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query()).items[0]


def test_queries_cover_every_category() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    # Nationwide category pages: crawled once, not once per region.
    assert len(queries) == len(CATEGORIES)
    assert {query.region for query in queries} == {"malopolskie"}
    assert any(
        query.url == "https://gdom.pl/kategoria-ogloszen/mieszkania-na-sprzedaz/"
        for query in queries
    )


def test_queries_skip_unknown_regions() -> None:
    assert _adapter().queries(("mazowieckie",)) == []


def test_with_page_uses_page_path() -> None:
    query = _query()
    adapter = _adapter()

    assert (
        adapter.with_page(query, 1).url
        == "https://gdom.pl/kategoria-ogloszen/mieszkania-na-sprzedaz/"
    )
    assert (
        adapter.with_page(query, 2).url
        == "https://gdom.pl/kategoria-ogloszen/mieszkania-na-sprzedaz/page/2/"
    )


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "gdom" / "search.html").read_text(encoding="utf-8")
    page = _adapter().parse_search(html, _query())

    assert page.current_page == 1
    assert page.total_pages == 10
    assert len(page.items) == 3

    first = page.items[0]
    assert (
        first.external_id == "bezposrednio-bez-prowizji-odrebna-wlasnosc-metro-imielin-5-min-pieszo"
    )
    assert first.url == (
        "https://gdom.pl/o/bezposrednio-bez-prowizji-odrebna-wlasnosc-metro-imielin-5-min-pieszo/"
    )
    assert (
        first.title == "BEZPOŚREDNIO, BEZ PROWIZJI, ODRĘBNA WŁASNOŚĆ, METRO IMIELIN 5 min. pieszo"
    )
    assert first.price_pln == 759_000
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    # Out-of-scope listing: the address names Mazowieckie, so no voivodeship is assigned.
    assert first.voivodeship is None
    assert first.city == "Warszawa"
    assert first.lat == 52.23072
    assert first.lng == 21.016317
    assert first.geo_source == "source"
    assert first.published_at is not None
    assert first.images

    in_scope = page.items[1]
    assert in_scope.external_id == "mieszkanie-na-sprzedaz-myszkow"
    assert in_scope.price_pln == 390_000
    assert in_scope.voivodeship == "slaskie"
    assert in_scope.county == "Myszkowski"
    assert in_scope.city == "Myszków"
    assert in_scope.lat == 50.57629
    assert in_scope.lng == 19.32791


def test_parse_detail_fixture() -> None:
    html = (FIXTURES / "gdom" / "detail.html").read_text(encoding="utf-8")
    detail = _adapter().parse_detail(html, _search_summary())

    assert (
        detail.external_id
        == "bezposrednio-bez-prowizji-odrebna-wlasnosc-metro-imielin-5-min-pieszo"
    )
    assert detail.title == "Kurort Kozubnik - Apartament*33"
    # The detail page's own transaction attribute wins over the search category.
    assert detail.offer_type == "rent"
    assert detail.price_unit == "monthly"
    assert detail.property_type == "flat"
    assert detail.price_pln == 65
    assert detail.area_m2 == 35.0
    assert detail.rooms == 2
    assert detail.floor == 3
    assert detail.build_year == 2021
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Porąbka"
    assert detail.street == "Mała Puszcza 22/33"
    assert detail.address_text == "Mała Puszcza 22/33, 43-353 Porąbka, województwo śląskie, Polska"
    assert detail.lat == 49.799326
    assert detail.lng == 19.228758
    assert detail.geo_source == "source"
    assert detail.seller_type == "private"
    assert detail.images
    assert detail.description is not None
    assert detail.description.startswith("Apartament*33 znajduje się w Kurort-Kozubnik Resort")

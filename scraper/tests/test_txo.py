from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.base import Query
from plaszczaki_scraper.adapters.txo import CATEGORIES, TxoAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing, OfferType, PropertyType
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _registry() -> RegionRegistry:
    return RegionRegistry(load_regions_config())


def _adapter() -> TxoAdapter:
    return TxoAdapter(source_id="txo", regions=_registry())


def _expected_query_count(registry: RegionRegistry) -> int:
    cities = 0
    for region in ("malopolskie", "slaskie"):
        cities += len(dict.fromkeys(registry.region(region).cities))
    return cities * len(CATEGORIES)


def test_queries_cover_both_regions() -> None:
    registry = _registry()
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == _expected_query_count(registry)
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert all(query.url.startswith("https://txo.pl/ogloszenia?") for query in queries)
    assert all(query.key.startswith(("malopolskie:", "slaskie:")) for query in queries)

    krakow = next(query for query in queries if query.key == "malopolskie:krakow:sale:apartment")
    assert krakow.url == (
        "https://txo.pl/ogloszenia?type=sale&property_type=apartment&city=Krak%C3%B3w"
    )
    assert krakow.offer_type == "sale"
    assert krakow.property_type == "flat"


def test_queries_skip_unknown_regions() -> None:
    adapter = _adapter()
    assert adapter.queries(("podkarpackie",)) == []
    assert adapter.queries(()) == []


def test_with_page_adds_page_parameter() -> None:
    adapter = _adapter()
    query = next(
        item for item in adapter.queries(("malopolskie",)) if item.key.endswith(":sale:plot")
    )
    paged = adapter.with_page(query, 3)

    assert paged.page == 3
    assert paged.url.endswith("&page=3")
    assert "city=" in paged.url


def test_parse_search_fixture() -> None:
    adapter = _adapter()
    html = (FIXTURES / "txo" / "search.html").read_text(encoding="utf-8")
    query = next(
        item
        for item in adapter.queries(("malopolskie",))
        if item.key == "malopolskie:krakow:sale:apartment"
    )
    page = adapter.parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 417
    assert page.total_items == 10000
    assert len(page.items) == 24

    first = page.items[0]
    assert first.source_id == "txo"
    assert first.external_id == "1463360"
    assert first.url == (
        "https://txo.pl/ogloszenia/apartment-krakow-mieszkanie-136m2-kamienica-sienkiewicza-krakow"
    )
    assert first.title == "Mieszkanie 136m2, kamienica, Sienkiewicza, Kraków"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.price_pln == 2390000
    assert first.price_unit == "total"
    assert first.price_per_m2_pln == 17574
    assert first.area_m2 == 136.0
    assert first.rooms == 3
    assert first.floor == 1
    assert first.city == "Kraków"
    assert first.district == "Kraków-Krowodrza"
    assert first.voivodeship == "malopolskie"
    assert first.lat == 50.0719485
    assert first.lng == 19.9267457
    assert first.geo_source == "source"
    assert first.published_at is not None
    assert len(first.images) == 6


def test_parse_search_derives_types_from_payload() -> None:
    adapter = _adapter()
    html = (FIXTURES / "txo" / "search.html").read_text(encoding="utf-8")
    query = Query(
        key="test",
        url="https://txo.pl/ogloszenia?type=sale&property_type=apartment&city=Krak%C3%B3w",
        region="malopolskie",
        offer_type="other",
        property_type="other",
    )
    page = adapter.parse_search(html, query)

    assert page.items[0].offer_type == "sale"
    assert page.items[0].property_type == "flat"


def _summary(
    adapter: TxoAdapter,
    slug: str,
    offer_type: OfferType,
    property_type: PropertyType,
) -> Listing:
    html = (FIXTURES / "txo" / "search.html").read_text(encoding="utf-8")
    query = next(
        item
        for item in adapter.queries(("malopolskie",))
        if item.key == "malopolskie:krakow:sale:apartment"
    )
    base = adapter.parse_search(html, query).items[0]
    return Listing(
        source_id=base.source_id,
        external_id=slug.rsplit("/", 1)[-1],
        url=slug,
        title=base.title,
        offer_type=offer_type,
        property_type=property_type,
        price_unit="monthly" if offer_type == "rent" else "total",
        voivodeship="malopolskie",
    )


def test_parse_detail_rent_fixture() -> None:
    adapter = _adapter()
    html = (FIXTURES / "txo" / "detail-rent.html").read_text(encoding="utf-8")
    summary = _summary(
        adapter,
        "https://txo.pl/ogloszenia/apartment-krakow-dwupokojowe-mieszkanie-do-wynajecia-krakow-krowodrza-lobzow-wroclawska",
        "rent",
        "flat",
    )

    detail = adapter.parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.price_pln == 2500
    assert detail.price_unit == "monthly"
    assert detail.price_per_m2_pln == 60
    assert detail.area_m2 == 42.0
    assert detail.rooms == 2
    assert detail.floor == 5
    assert detail.build_year == 1980
    assert detail.market == "secondary"
    assert detail.city == "Kraków"
    assert detail.street == "Wrocławska"
    assert detail.voivodeship == "malopolskie"
    assert detail.lat == 50.0795
    assert detail.lng == 19.92
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.description.startswith("2-POKOJOWE MIESZKANIE Z BALKONEM")
    assert len(detail.images) == 12
    assert detail.extra["txo"]["characteristics"]["Liczba pokoi"] == "2"


def test_parse_detail_house_fixture() -> None:
    adapter = _adapter()
    html = (FIXTURES / "txo" / "detail-house.html").read_text(encoding="utf-8")
    summary = _summary(
        adapter,
        "https://txo.pl/ogloszenia/house-krakow-dom-na-sprzedaz-348-m2-7-pokoi-mistrzejowice-krakow-1",
        "sale",
        "house",
    )

    detail = adapter.parse_detail(html, summary)

    assert detail.property_type == "house"
    assert detail.price_pln == 3100000
    assert detail.area_m2 == 348.4
    assert detail.land_area_m2 == 318.0
    assert detail.rooms == 7
    assert detail.build_year == 1994
    assert detail.market == "unknown"
    assert detail.city == "Kraków"
    assert detail.lat == 50.096828
    assert detail.lng == 20.0240876
    assert detail.description is not None
    assert "Mistrzejowicach" in detail.description


def test_parse_detail_plot_fixture() -> None:
    adapter = _adapter()
    html = (FIXTURES / "txo" / "detail-plot.html").read_text(encoding="utf-8")
    summary = _summary(
        adapter,
        "https://txo.pl/ogloszenia/plot-krakow-okazja-dzialka-bud-5300m2-mpzp-w-krakowie-podgorzu",
        "sale",
        "plot",
    )

    detail = adapter.parse_detail(html, summary)

    assert detail.property_type == "plot"
    assert detail.price_pln == 699000
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 5300.0
    assert detail.rooms is None
    assert detail.lat == 49.99259
    assert detail.lng == 20.00595
    assert detail.geo_source == "source"

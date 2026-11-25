from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.terenyprzemyslowe import TerenyPrzemysloweAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "terenyprzemyslowe"


def _adapter() -> TerenyPrzemysloweAdapter:
    return TerenyPrzemysloweAdapter(
        source_id="terenyprzemyslowe",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(region: str = "malopolskie", rodzaj: str = "5"):
    for query in _adapter().queries((region,)):
        if query.key == f"{region}:{rodzaj}":
            return query
    raise AssertionError(f"missing query {region}:{rodzaj}")


def test_queries_cover_regions_and_categories() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 8
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {query.key.split(":", 1)[1] for query in queries} == {"5", "6", "8", "9"}
    assert any("wojewodztwo=ma%C5%82opolskie" in query.url for query in queries)
    assert any("rodzaj=6" in query.url for query in queries)
    assert {query.property_type for query in queries} == {"plot", "commercial"}


def test_with_page_adds_page_parameter() -> None:
    query = _query()
    paged = _adapter().with_page(query, 3)

    assert "page=3" in paged.url
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "search_malopolskie.html").read_text(encoding="utf-8")
    page = _adapter().parse_search(html, _query())

    assert page.current_page == 1
    assert page.total_pages == 1
    assert page.total_items == 19
    assert len(page.items) == 19

    plot = next(item for item in page.items if item.external_id == "566")
    assert plot.url == "https://terenyprzemyslowe.pl/teren-inwestycyjny-273-ha-nowe-brzesko/"
    assert plot.title == "Teren inwestycyjny 2,73 ha \u2013 Nowe Brzesko"
    assert plot.offer_type == "sale"
    assert plot.property_type == "plot"
    assert plot.price_kind == "asking"
    assert plot.price_pln == 1_642_368
    assert plot.price_per_m2_pln == 60
    assert plot.area_m2 is None
    assert plot.land_area_m2 == 27_300
    assert plot.voivodeship == "malopolskie"
    assert plot.city == "Nowe Brzesko"
    assert plot.images == [
        "https://terenyprzemyslowe.pl/media/nieruchomosci/2026/10/"
        "paih-735cd3e77416af610c61e6889b43a954.jpg",
    ]

    warehouse = next(item for item in page.items if item.external_id == "787")
    assert warehouse.offer_type == "rent"
    assert warehouse.property_type == "commercial"
    assert warehouse.price_kind == "call"
    assert warehouse.price_pln is None
    assert warehouse.area_m2 == 13_100
    assert warehouse.land_area_m2 is None
    assert warehouse.city == "Wieliczka"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "search_malopolskie.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "detail_nowe_brzesko.html").read_text(encoding="utf-8")
    summary = next(
        item
        for item in _adapter().parse_search(search_html, _query()).items
        if item.external_id == "566"
    )

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "566"
    assert detail.title == "Teren inwestycyjny 2,73 ha \u2013 Nowe Brzesko"
    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.price_kind == "asking"
    assert detail.price_pln == 1_642_368
    assert detail.price_per_m2_pln == 60
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 27_300
    assert detail.voivodeship == "malopolskie"
    assert detail.county == "proszowicki"
    assert detail.commune == "Nowe Brzesko"
    assert detail.city == "Nowe Brzesko"
    assert detail.address_text == "Nowe Brzesko, pow. proszowicki, małopolskie"
    assert detail.lat == 50.128334
    assert detail.lng == 20.379167
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.description.startswith("Lokalizacja: Strefa Aktywności Ekonomicznej")
    assert "Krakowski Park Technologiczny" in detail.description
    assert len(detail.images) == 6
    assert detail.published_at is not None
    assert detail.extra["terenyprzemyslowe"]["zone"] == "Krakowski Park Technologiczny"

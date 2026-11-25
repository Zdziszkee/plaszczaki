from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.base import Query
from plaszczaki_scraper.adapters.okolica import CATEGORIES, OkolicaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> OkolicaAdapter:
    return OkolicaAdapter(
        source_id="okolica",
        regions=RegionRegistry(load_regions_config()),
    )


def _query() -> Query:
    queries = _adapter().queries(("malopolskie",))
    return next(query for query in queries if query.key == "malopolskie:krakow:mieszkanie:sprzedam")


def _search_summary():
    html = (FIXTURES / "okolica" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query()).items[0]


def test_queries_cover_every_city_and_category() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    regions = _adapter().regions

    expected = sum(
        len(dict.fromkeys(regions.region(region).cities)) for region in ("malopolskie", "slaskie")
    ) * len(CATEGORIES)
    assert len(queries) == expected
    assert len({query.key for query in queries}) == len(queries)
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(
        query.url == "https://www.okolica.pl/mieszkanie/sprzedam/krakow/" for query in queries
    )
    assert any(
        query.url == "https://www.okolica.pl/komercyjne/wynajme/krakow/" for query in queries
    )


def test_queries_skip_unknown_regions() -> None:
    assert _adapter().queries(("mazowieckie",)) == []


def test_with_page_adds_page_parameter() -> None:
    query = _query()
    adapter = _adapter()

    assert adapter.with_page(query, 1).url == "https://www.okolica.pl/mieszkanie/sprzedam/krakow/"
    assert (
        adapter.with_page(query, 3).url
        == "https://www.okolica.pl/mieszkanie/sprzedam/krakow/?page=3"
    )


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "okolica" / "search.html").read_text(encoding="utf-8")
    page = _adapter().parse_search(html, _query())

    assert page.current_page == 1
    assert page.total_pages == 91
    assert len(page.items) == 3

    first = page.items[0]
    assert first.external_id == "62764-S-5832_5595_OMS"
    assert first.url == ("https://www.okolica.pl/offer/show/62764-S-5832_5595_OMS/formular")
    assert first.title == "2 pokoje 36 m Osiedle Oficerskie Balkon Brodowicza"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.price_pln == 590_000
    assert first.price_per_m2_pln == 14_813
    assert first.area_m2 == 39.83
    assert first.rooms == 2
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Prądnik Czerwony"
    assert first.street == "Józefa Brodowicza"
    assert first.address_text == "Kraków, Prądnik Czerwony, Józefa Brodowicza"
    assert first.images
    assert first.description is not None
    assert first.description.startswith("Na sprzedaż 2-pokojowe mieszkanie")


def test_parse_detail_fixture() -> None:
    html = (FIXTURES / "okolica" / "detail.html").read_text(encoding="utf-8")
    detail = _adapter().parse_detail(html, _search_summary())

    assert detail.external_id == "62764-S-5832_5595_OMS"
    assert detail.title == "Mieszkanie 2 pokojowe, rozkładowe, z dużą piwnicą , niskie koszty"
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.market == "secondary"
    assert detail.price_pln == 399_000
    assert detail.price_per_m2_pln == 7_336
    assert detail.price_unit == "total"
    assert detail.area_m2 == 54.39
    assert detail.rooms == 2
    assert detail.floor == 3
    assert detail.build_year == 1945
    assert detail.lat == 54.17716
    assert detail.lng == 15.57362
    assert detail.geo_source == "source"
    assert detail.images
    assert detail.description is not None
    assert detail.description.startswith("Okazja, nowa niższa cena")
    assert detail.published_at is not None


def test_parse_detail_rent_fixture() -> None:
    html = (FIXTURES / "okolica" / "detail_rent.html").read_text(encoding="utf-8")
    detail = _adapter().parse_detail(html, _search_summary())

    assert detail.offer_type == "rent"
    assert detail.price_pln == 3_000
    assert detail.price_unit == "monthly"
    assert detail.area_m2 == 48.0
    assert detail.rooms == 2
    assert detail.floor == 0
    assert detail.floor_label == "parter"
    assert detail.build_year == 2018
    assert detail.city == "Kraków"
    assert detail.district == "Prądnik Biały"
    assert detail.lat == 50.092694
    assert detail.lng == 19.919315
    assert detail.geo_source == "source"


def test_parse_detail_plot_fixture() -> None:
    html = (FIXTURES / "okolica" / "detail_plot.html").read_text(encoding="utf-8")
    detail = _adapter().parse_detail(html, _search_summary())

    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.title == "Działka budowalna w topowej lokalizacji Emaus"
    assert detail.price_pln == 2_050_000
    assert detail.price_per_m2_pln == 3_328
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 616.0
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Zwierzyniec"
    assert detail.street == "Królowej Jadwigi"
    assert detail.lat == 50.060652
    assert detail.lng == 19.892567
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert len(detail.description) > 500

from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.axiimmo import AxiImmoAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "axiimmo"


def _adapter() -> AxiImmoAdapter:
    return AxiImmoAdapter(
        source_id="axiimmo",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(region: str, category: str):
    for query in _adapter().queries((region,)):
        if query.key == f"{region}:{category}":
            return query
    raise AssertionError(f"missing query {region}:{category}")


def _search(filename: str, region: str, category: str):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query(region, category))


def test_queries_cover_regions_and_categories() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 6
    assert {query.key for query in queries} == {
        "malopolskie:grunty-inwestycyjne",
        "malopolskie:magazyny",
        "malopolskie:biura",
        "slaskie:grunty-inwestycyjne",
        "slaskie:magazyny",
        "slaskie:biura",
    }
    urls = {query.key: query.url for query in queries}
    assert urls["malopolskie:grunty-inwestycyjne"] == (
        "https://www.axiimmo.com/grunty-inwestycyjne/malopolskie/"
    )
    assert urls["malopolskie:magazyny"] == ("https://www.axiimmo.com/magazyny/region-krakow/")
    assert urls["slaskie:magazyny"] == ("https://www.axiimmo.com/magazyny/region-gorny-slask/")
    assert urls["slaskie:biura"] == "https://www.axiimmo.com/biura/slaskie/"
    assert _query("malopolskie", "magazyny").offer_type == "rent"
    assert _query("malopolskie", "grunty-inwestycyjne").offer_type == "sale"


def test_with_page_builds_path_segment() -> None:
    query = _query("malopolskie", "grunty-inwestycyjne")

    paged = _adapter().with_page(query, 2)
    assert paged.url == ("https://www.axiimmo.com/grunty-inwestycyjne/malopolskie/page/2/")
    assert paged.page == 2

    first = _adapter().with_page(paged, 1)
    assert first.url == "https://www.axiimmo.com/grunty-inwestycyjne/malopolskie/"
    assert first.page == 1


def test_parse_search_plots() -> None:
    page = _search("search_grunty_malopolskie.html", "malopolskie", "grunty-inwestycyjne")

    assert page.current_page == 1
    assert page.total_pages == 1
    assert page.total_items == 2
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "d644"
    assert first.url == (
        "https://www.axiimmo.com/grunty-inwestycyjne/teren-inwestycyjny-w-okolicy-krakowa-d644/"
    )
    assert first.title == "Teren inwestycyjny w okolicy Krakowa"
    assert first.offer_type == "sale"
    assert first.property_type == "plot"
    assert first.land_area_m2 == 27_500
    assert first.voivodeship == "malopolskie"

    second = page.items[1]
    assert second.external_id == "d635"
    assert second.land_area_m2 == 150_000
    assert second.offer_type == "sale"


def test_parse_detail_plot() -> None:
    summary = _search(
        "search_grunty_malopolskie.html",
        "malopolskie",
        "grunty-inwestycyjne",
    ).items[0]
    detail_html = (FIXTURES / "detail_plot_krakow.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "d644"
    assert detail.title == "Teren inwestycyjny w okolicy Krakowa"
    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.price_kind == "call"
    assert detail.price_pln is None
    assert detail.price_per_m2_pln is None
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 27_500
    assert detail.voivodeship == "malopolskie"
    assert detail.lat == 50.006751
    assert detail.lng == 20.132911
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert "Lokalizacja gruntu" in detail.description
    assert len(detail.images) == 1
    assert detail.seller_type == "agency"
    assert detail.seller_name == "AXI IMMO"
    assert detail.extra["axiimmo"]["offer_id"] == "d644"


def test_parse_search_offices() -> None:
    page = _search("search_biura_malopolskie.html", "malopolskie", "biura")

    assert page.total_items == 142
    assert page.total_pages == 2
    assert len(page.items) == 1

    office = page.items[0]
    assert office.external_id == "bb10993"
    assert office.property_type == "commercial"
    assert office.offer_type == "rent"
    assert office.area_m2 == 2_100
    assert office.voivodeship == "malopolskie"


def test_parse_detail_office_with_pln_rent() -> None:
    summary = _search("search_biura_malopolskie.html", "malopolskie", "biura").items[0]
    detail_html = (FIXTURES / "detail_office_krakow.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "bb10993"
    assert detail.offer_type == "rent"
    assert detail.property_type == "commercial"
    assert detail.price_kind == "asking"
    assert detail.price_unit == "monthly"
    assert detail.price_pln is None
    assert detail.price_per_m2_pln == 59
    assert detail.area_m2 == 212
    assert detail.land_area_m2 is None
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Stare Miasto"
    assert detail.lat == 50.055494
    assert detail.lng == 19.933195
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert "Nowoczesny biurowiec klasy A" in detail.description


def test_parse_detail_warehouse_with_eur_quote() -> None:
    summary = _search("search_magazyny_slaskie.html", "slaskie", "magazyny").items[0]
    detail_html = (FIXTURES / "detail_warehouse_zabrze.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "mp12743"
    assert detail.offer_type == "rent"
    assert detail.property_type == "commercial"
    assert detail.price_unit == "monthly"
    assert detail.price_pln is None
    assert detail.price_per_m2_pln is None
    assert detail.area_m2 == 10_643
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Zabrze"
    assert detail.lat == 50.347756
    assert detail.lng == 18.80379
    assert detail.geo_source == "source"
    assert detail.extra["axiimmo"]["currency"] == "EUR"
    assert detail.extra["axiimmo"]["offer"]["price"] == "5.50"
    assert detail.description is not None
    assert "kompleks produkcyjno-magazynowy" in detail.description

from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.adresowo import AdresowoAdapter
from plaszczaki_scraper.adapters.base import Query
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> AdresowoAdapter:
    return AdresowoAdapter(
        source_id="adresowo",
        regions=RegionRegistry(load_regions_config()),
    )


def _query() -> Query:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    return next(query for query in queries if query.key == "malopolskie:domy")


def _search_summary():
    html = (FIXTURES / "adresowo" / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query()).items[0]


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 16
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(query.url == "https://adresowo.pl/mieszkania/fma" for query in queries)
    assert any(query.url == "https://adresowo.pl/pokoje-wynajem/fsl" for query in queries)


def test_queries_skip_unknown_regions() -> None:
    assert _adapter().queries(("mazowieckie",)) == []


def test_with_page_uses_path_suffix() -> None:
    query = _query()
    adapter = _adapter()

    assert adapter.with_page(query, 1).url == "https://adresowo.pl/domy/fma"
    assert adapter.with_page(query, 2).url == "https://adresowo.pl/domy/fma_l2"
    assert adapter.with_page(query, 12).url == "https://adresowo.pl/domy/fma_l12"


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "adresowo" / "search.html").read_text(encoding="utf-8")
    page = _adapter().parse_search(html, _query())

    assert page.current_page == 1
    # Only prev/next links exist, so a page with "next" promises one more page.
    assert page.total_pages == 2
    assert len(page.items) == 3

    first = page.items[0]
    assert first.external_id == "4179320"
    assert first.url == ("https://adresowo.pl/o/dom-stary-sacz-przysietnica-ul-pieniezne-n5w1t7")
    assert first.title == "Dom na sprzedaż, Przysietnica"
    assert first.offer_type == "sale"
    assert first.property_type == "house"
    assert first.price_pln == 1_000_000
    assert first.area_m2 == 330.0
    assert first.rooms == 6
    assert first.voivodeship == "malopolskie"
    assert first.city == "Przysietnica"
    assert first.street == "ul. Pieniężne 367"
    assert first.address_text == "ul. Pieniężne 367, Przysietnica"
    assert first.seller_type == "private"
    assert first.images
    assert first.description is not None
    assert first.description.startswith("Dom ~330 m²")

    second = page.items[1]
    assert second.title == "Dom na sprzedaż, Zagórze"
    assert second.price_pln == 870_000
    assert second.area_m2 == 80.0
    assert second.rooms == 3


def test_parse_detail_fixture() -> None:
    html = (FIXTURES / "adresowo" / "detail.html").read_text(encoding="utf-8")
    detail = _adapter().parse_detail(html, _search_summary())

    assert detail.external_id == "4179320"
    assert detail.title is not None
    assert detail.title.startswith("Mieszkanie Kraków, ul. Kielecka")
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.price_pln == 730_000
    assert detail.price_per_m2_pln == 16_111
    assert detail.price_unit == "total"
    assert detail.area_m2 == 45.31
    assert detail.land_area_m2 is None
    assert detail.rooms == 2
    assert detail.floor == 0
    assert detail.floor_label == "Parter"
    assert detail.build_year == 1960
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Grzegórzki"
    assert detail.street == "ul. Kielecka"
    assert detail.lat == 50.069933
    assert detail.lng == 19.962872
    assert detail.geo_source == "source"
    assert detail.seller_type == "private"
    assert detail.description is not None
    assert detail.description.startswith("Na sprzedaż dwupokojowe mieszkanie")
    assert len(detail.images) > 1
    assert detail.images[0].startswith("https://s2.adresowa.pl/oi/")


def test_parse_detail_rent_fixture() -> None:
    html = (FIXTURES / "adresowo" / "detail_rent.html").read_text(encoding="utf-8")
    detail = _adapter().parse_detail(html, _search_summary())

    assert detail.offer_type == "rent"
    assert detail.price_pln == 2_800
    assert detail.price_unit == "monthly"
    assert detail.rent_pln == 640
    assert detail.deposit_pln == 1_000
    assert detail.area_m2 == 54.0
    assert detail.rooms == 2
    assert detail.floor == 1
    assert detail.city == "Kraków"
    assert detail.district == "Dębniki"
    assert detail.lat == 50.024059
    assert detail.lng == 19.904481
    assert detail.geo_source == "source"


def test_parse_detail_plot_fixture() -> None:
    html = (FIXTURES / "adresowo" / "detail_plot.html").read_text(encoding="utf-8")
    detail = _adapter().parse_detail(html, _search_summary())

    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.price_pln == 350_000
    assert detail.price_per_m2_pln == 130
    assert detail.area_m2 is None
    assert detail.land_area_m2 == 2_700.0
    assert detail.voivodeship == "malopolskie"
    assert detail.county == "krakowski"
    assert detail.commune == "Michałowice"
    assert detail.city == "Kozierów"
    assert detail.street == "ul. Lipowa"
    assert detail.lat == 50.180891
    assert detail.lng == 19.957331

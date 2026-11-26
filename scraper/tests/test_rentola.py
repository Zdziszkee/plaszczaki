from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.rentola import RentolaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> RentolaAdapter:
    return RentolaAdapter(
        source_id="rentola",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_every_configured_city_and_category() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == len({query.url for query in queries})
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {query.property_type for query in queries} == {"flat", "house"}
    assert any(query.url == "https://rentola.pl/wynajem/mieszkanie/krakow" for query in queries)
    assert any(query.url == "https://rentola.pl/wynajem/dom/katowice" for query in queries)
    assert any(query.url == "https://rentola.pl/wynajem/mieszkanie/wieliczka" for query in queries)
    assert any(query.url == "https://rentola.pl/wynajem/dom/tychy" for query in queries)


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert paged.url.endswith("?page=2")
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "rentola" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 3
    assert page.total_items == 63
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == (
        "czarnowiejska-55a-krowodrza-2-pokoje-z-parkingiem-krakow-lobzow-pf1396a"
    )
    assert first.url.startswith("https://rentola.pl/listings/")
    assert first.offer_type == "rent"
    assert first.property_type == "flat"
    assert first.price_pln == 1700
    assert first.price_unit == "monthly"
    assert first.area_m2 == 33
    assert first.rooms == 2
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.street == "Czarnowiejska 55a"
    assert first.lat == 50.0678159
    assert first.lng == 19.9201229
    assert first.geo_source == "source"
    assert first.published_at == 1793897798000

    second = page.items[1]
    assert second.price_pln == 1390
    assert second.area_m2 == 13
    assert second.rooms == 1


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "rentola" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "rentola" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == summary.external_id
    assert (detail.title or "").startswith("Czarnowiejska 55a")
    assert detail.price_pln == 1700
    assert detail.rent_pln == 670
    assert detail.area_m2 == 33
    assert detail.rooms == 2
    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.street == "Czarnowiejska 55a"
    assert detail.lat == 50.0678159
    assert detail.lng == 19.9201229
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.description.startswith("Opis lokalu:")
    assert len(detail.images) == 10

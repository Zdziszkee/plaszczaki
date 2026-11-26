from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.rentumo import RentumoAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> RentumoAdapter:
    return RentumoAdapter(
        source_id="rentumo",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_every_configured_city_in_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == len({query.url for query in queries})
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(
        query.url == "https://rentumo.pl/wynajmij-mieszkanie/krakow-malopolskie"
        for query in queries
    )
    assert any(
        query.url == "https://rentumo.pl/wynajmij-mieszkanie/wieliczka-malopolskie"
        for query in queries
    )
    assert any(
        query.url == "https://rentumo.pl/wynajmij-mieszkanie/tychy-slaskie" for query in queries
    )
    assert all(query.offer_type == "rent" for query in queries)
    assert all(query.property_type == "flat" for query in queries)


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)
    assert paged.url.endswith("?page=3")
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "rentumo" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 2
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "1574571"
    assert first.url == "https://rentumo.pl/ogloszenia/mieszkanie-ul-milkowskiego-1574571"
    assert first.title == "Mieszkanie, ul. Miłkowskiego"
    assert first.offer_type == "rent"
    assert first.property_type == "flat"
    assert first.price_pln == 690
    assert first.price_unit == "monthly"
    assert first.area_m2 == 42
    assert first.rooms == 2
    assert first.city == "Kraków"
    assert first.voivodeship == "malopolskie"
    assert len(first.images) == 6

    second = page.items[1]
    assert second.external_id == "137292"
    assert second.price_pln == 990
    assert second.rooms == 1
    assert second.city == "Kraków"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "rentumo" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "rentumo" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "1574571"
    assert detail.title == "Mieszkanie, ul. Miłkowskiego"
    assert detail.price_pln == 690
    assert detail.deposit_pln == 2500
    assert detail.build_year == 2005
    assert detail.area_m2 == 42
    assert detail.rooms == 2
    assert detail.offer_type == "rent"
    assert detail.property_type == "flat"
    assert detail.market == "secondary"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Wawel"
    assert detail.lat == 50.02772
    assert detail.lng == 19.91987
    assert detail.geo_source == "source"
    assert detail.description is not None
    assert detail.description.startswith("Wynajmę od 15 listopada 2026r.")
    assert len(detail.images) == 7


def test_newest_requests_the_date_descending_sort() -> None:
    adapter = _adapter()
    query = adapter.queries(("malopolskie",))[0]

    assert adapter.newest(query).url.endswith("?sort_by=date_desc")

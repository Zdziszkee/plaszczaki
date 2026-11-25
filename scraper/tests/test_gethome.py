from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.base import Query
from plaszczaki_scraper.adapters.gethome import MAX_PAGE, GetHomeAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> GetHomeAdapter:
    return GetHomeAdapter(
        source_id="gethome",
        regions=RegionRegistry(load_regions_config()),
    )


def _house_query() -> Query:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    return next(query for query in queries if query.key == "malopolskie:domy:na-sprzedaz")


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 10
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(
        query.url == "https://gethome.pl/mieszkania/na-sprzedaz/malopolskie/" for query in queries
    )
    assert any(query.url == "https://gethome.pl/dzialki/slaskie/" for query in queries)
    assert len({query.key for query in queries}) == len(queries)


def test_queries_skip_unknown_regions() -> None:
    assert _adapter().queries(("mazowieckie",)) == []


def test_with_page_respects_robots_limit() -> None:
    query = _house_query()
    adapter = _adapter()

    assert adapter.with_page(query, 1).url == query.url
    assert adapter.with_page(query, 2).url.endswith("?page=2")
    assert adapter.with_page(query, 9).url.endswith(f"?page={MAX_PAGE}")


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "gethome" / "search.html").read_text(encoding="utf-8")
    page = _adapter().parse_search(html, _house_query())

    assert page.current_page == 1
    assert page.total_pages == 6
    assert page.total_items == 739
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "2430292"
    assert first.url == (
        "https://gethome.pl/oferta/"
        "sprzedam-dom-krakow-wola-justowska-410m2-5-pokoi-z-2023-roku-ogrod-dzialke-837m2-2430292/"
    )
    assert first.title == "Luksusowa rezydencja z widokiem na las wolski | wola justowska"
    assert first.offer_type == "sale"
    assert first.property_type == "house"
    assert first.market == "secondary"
    assert first.price_pln == 6_500_000
    assert first.price_per_m2_pln == 15_853
    assert first.area_m2 == 410.0
    assert first.land_area_m2 == 837.0
    assert first.rooms == 5
    assert first.build_year == 2023
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Zwierzyniec"
    assert first.street is None
    assert first.lat == 50.06174
    assert first.lng == 19.84866
    assert first.geo_source == "source"
    assert first.seller_type == "agency"
    assert first.seller_name == "Nest Brokers Estate Agency"
    assert first.published_at is not None
    assert first.images
    assert first.description is not None
    assert first.description.startswith("Luksusowa rezydencja")


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "gethome" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "gethome" / "detail.html").read_text(encoding="utf-8")
    adapter = _adapter()
    summary = adapter.parse_search(search_html, _house_query()).items[0]

    detail = adapter.parse_detail(detail_html, summary)

    assert detail.source_id == "gethome"
    assert detail.external_id == "2430292"
    assert detail.title == "Dom na sprzedaż 5 pokoje 410m2"
    assert detail.offer_type == "sale"
    assert detail.property_type == "house"
    assert detail.market == "secondary"
    assert detail.price_pln == 6_500_000
    assert detail.area_m2 == 410.0
    assert detail.land_area_m2 == 837.0
    assert detail.rooms == 5
    assert detail.build_year == 2023
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Zwierzyniec"
    assert detail.address_text == "Kraków, Zwierzyniec"
    assert detail.lat == 50.0617388
    assert detail.lng == 19.8486621
    assert detail.geo_source == "source"
    assert detail.seller_type == "agency"
    assert detail.seller_name == "Nest Brokers Estate Agency"
    assert detail.description is not None
    assert detail.description.startswith("Luksusowa rezydencja")
    # The detail payload has no gallery, so the search images are preserved.
    assert detail.images == summary.images

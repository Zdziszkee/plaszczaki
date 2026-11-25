from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.nportal import NportalAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> NportalAdapter:
    return NportalAdapter(source_id="nportal", regions=RegionRegistry(load_regions_config()))


def test_queries_cover_both_regions_and_transactions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 18
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(query.url == "http://nportal.pl/mieszkania/malopolskie/" for query in queries)
    assert any(
        query.url == "http://nportal.pl/mieszkania/wynajem/malopolskie/" for query in queries
    )
    assert any(query.url == "http://nportal.pl/komercyjne/slaskie/" for query in queries)


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert paged.url == "http://nportal.pl/mieszkania/malopolskie/?page=2"
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "nportal" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 280
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "2149423328"
    assert first.url.endswith("-npo2149423328")
    assert first.price_pln == 439000
    assert first.price_per_m2_pln == 14967
    assert first.area_m2 == 29
    assert first.rooms == 2
    assert first.floor == 3
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.district == "Bieńczyce"
    assert first.street == "Fatimska"
    assert first.description == "Przykładowy opis oferty z balkonem i piwnicą."
    assert first.images == ["http://img1.staticdomy.com.pl/thumbnail/example-1.jpg"]


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "nportal" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "nportal" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "2149423328"
    assert detail.price_pln == 439000
    assert detail.price_per_m2_pln == 14967
    assert detail.area_m2 == 29.33
    assert detail.rooms == 2
    assert detail.floor == 3
    assert detail.build_year == 1975
    assert detail.market == "secondary"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Bieńczyce"
    assert detail.street == "Fatimska"
    assert detail.lat == 50.0852949
    assert detail.lng == 20.0319338
    assert detail.geo_source == "source"
    assert "Przykładowy opis nieruchomości" in (detail.description or "")
    assert detail.extra["nportal"]["offer_number"] == "BS1-MS-318818"
    assert detail.modified_at is not None

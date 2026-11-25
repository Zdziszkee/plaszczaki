from __future__ import annotations

from pathlib import Path

from plaszczaki_scraper.adapters.szybko import SzybkoAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> SzybkoAdapter:
    return SzybkoAdapter(source_id="szybko", regions=RegionRegistry(load_regions_config()))


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 26
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert any(
        query.url == "https://szybko.pl/l/na-sprzedaz/lokal-mieszkalny+mieszkanie/malopolskie"
        for query in queries
    )
    assert any(query.url == "https://szybko.pl/l/na-wynajem/pokoj/malopolskie" for query in queries)


def test_with_page_uses_strona_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 2)
    assert paged.url.endswith("?strona=2")
    assert "page=" not in paged.url
    assert paged.page == 2


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "szybko" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 304
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "15920208"
    assert first.url.startswith("https://szybko.pl/o/")
    assert first.price_pln == 365000
    assert first.price_per_m2_pln == 6053
    assert first.area_m2 == 60.3
    assert first.rooms == 4
    assert first.voivodeship == "malopolskie"
    assert first.city == "Tarnów"
    assert first.street == "Promienna"
    assert first.lat == 50.0236969
    assert first.lng == 21.0082745
    assert first.geo_source == "source"
    assert first.description == "Przykładowy opis oferty z czterema pokojami i balkonem."
    assert first.seller_name == "Agnieszka Piotrowska"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "szybko" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "szybko" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "15920208"
    assert detail.price_pln == 365000
    assert detail.price_per_m2_pln == 6053
    assert detail.area_m2 == 60.3
    assert detail.rooms == 4
    assert detail.floor == 0
    assert detail.floor_label == "parter / 4"
    assert detail.build_year == 1982
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Tarnów"
    assert detail.street == "Promienna"
    assert detail.lat == 50.0236969
    assert detail.lng == 21.0082745
    assert detail.geo_source == "source"
    assert "Przykładowy opis nieruchomości" in (detail.description or "")
    assert detail.seller_type == "agency"
    assert detail.seller_name == "Piotrowska Nieruchomości"
    assert detail.images == [
        "https://mediaproxy.szybko.pl/210x140/photo/asset/example-1.jpg",
        "https://mediaproxy.szybko.pl/210x140/photo/asset/example-2.jpg",
    ]

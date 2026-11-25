from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.luksusowemieszkania import LuksusoweMieszkaniaAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def _adapter() -> LuksusoweMieszkaniaAdapter:
    return LuksusoweMieszkaniaAdapter(
        source_id="luksusowemieszkania",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_cover_regions_and_transactions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))
    assert len(queries) == 8
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {
        "https://luksusowemieszkania.pl/sprzedaz/apartamenty/malopolskie",
        "https://luksusowemieszkania.pl/sprzedaz/domy/malopolskie",
        "https://luksusowemieszkania.pl/wynajem/apartamenty/slaskie",
        "https://luksusowemieszkania.pl/wynajem/domy/slaskie",
    } <= {query.url for query in queries}
    assert {(query.offer_type, query.property_type) for query in queries} == {
        ("sale", "flat"),
        ("sale", "house"),
        ("rent", "flat"),
        ("rent", "house"),
    }


def test_with_page_adds_page_parameter() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 3)
    assert paged.url.endswith("?page=3")
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "luksusowemieszkania" / "search.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(html, query)

    assert page.current_page == 1
    assert page.total_pages == 32
    assert len(page.items) == 2

    first = page.items[0]
    assert first.external_id == "i9150872"
    assert first.url == (
        "https://luksusowemieszkania.pl/inwestycja/"
        "drago-stefana-banacha-krakow-bronowice-gorka-narodowa-apartamenty-na-sprzedaz,i9150872"
    )
    assert first.title == "Drago"
    assert first.offer_type == "sale"
    assert first.property_type == "flat"
    assert first.market == "primary"
    assert first.price_pln == 675780
    assert first.price_kind == "starting"
    assert first.area_m2 == 46.12
    assert first.rooms == 1
    assert first.voivodeship == "malopolskie"
    assert first.city == "Kraków"
    assert first.lat == 50.102129
    assert first.lng == 19.961609
    assert first.geo_source == "source"
    assert first.description and first.description.startswith("Apartamenty DRAGO")

    second = page.items[1]
    assert second.external_id == "i8986377"
    assert second.title == "Tatrzański Resort"
    assert second.area_m2 == 27.27
    assert second.city == "Białka Tatrzańska"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "luksusowemieszkania" / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "luksusowemieszkania" / "detail.html").read_text(encoding="utf-8")
    query = _adapter().queries(("malopolskie",))[0]
    summary = _adapter().parse_search(search_html, query).items[0]

    detail = _adapter().parse_detail(detail_html, summary)

    assert detail.external_id == "i8970882"
    assert detail.url.endswith(",i8970882")
    assert detail.title == "Craft Zabłocie - apartamenty na sprzedaż"
    assert detail.price_pln == 575122
    assert detail.price_kind == "starting"
    assert detail.area_m2 == 26.19
    assert detail.rooms == 1
    assert detail.floor == 5
    assert detail.floor_label == "5"
    assert detail.market == "primary"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Podgórze"
    assert detail.street == "Zabłocie 22A"
    assert detail.lat == 50.05042
    assert detail.lng == 19.966098
    assert detail.geo_source == "source"
    assert detail.seller_type == "developer"
    assert detail.seller_name == "Cordia Polska"
    assert detail.description and "Gotowa inwestycja" in detail.description
    assert detail.images
    assert detail.extra["luksusowemieszkania"]["high_price"] == "1634575.44"


def test_missing_json_ld_is_rejected() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    with pytest.raises(ParseError, match=r"JSON-LD|Product"):
        _adapter().parse_search("<html><body>nope</body></html>", query)

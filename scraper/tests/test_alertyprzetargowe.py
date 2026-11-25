from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.alertyprzetargowe import (
    AlertyPrzetargoweAdapter,
    _parse_amount,
    _parse_measure,
)
from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "alertyprzetargowe"


def _adapter() -> AlertyPrzetargoweAdapter:
    return AlertyPrzetargoweAdapter(
        source_id="alertyprzetargowe",
        regions=RegionRegistry(load_regions_config()),
    )


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 8
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert len({query.url for query in queries}) == 8
    for query in queries:
        assert query.url.startswith("https://alertyprzetargowe.pl/nieruchomosci?")
        assert "latitude=" in query.url
        assert "longitude=" in query.url
        assert "radius=75" in query.url


def test_with_page_keeps_the_search_filters() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    paged = _adapter().with_page(query, 5)

    assert paged.page == 5
    assert paged.url.endswith("&page=5")
    assert "latitude=" in paged.url
    assert "longitude=" in paged.url


def test_parse_search_fixture() -> None:
    query = _adapter().queries(("malopolskie",))[0]
    page = _adapter().parse_search(_fixture("search.html"), query)

    assert page.current_page == 1
    assert page.total_pages == 14
    assert page.total_items == 197
    assert len(page.items) == 15

    first = page.items[0]
    assert first.external_id.startswith("przetarg-sprzedaz-nieruchomosci-niezabudowanej")
    assert first.url.startswith("https://alertyprzetargowe.pl/nieruchomosci/")
    assert first.title == "PRZETARG - SPRZEDAŻ NIERUCHOMOŚCI NIEZABUDOWANEJ"
    assert first.city == "Garlica Murowana"
    assert first.voivodeship == "malopolskie"
    assert first.price_pln == 819_000
    assert first.area_m2 == 2700.0
    assert first.offer_type == "sale"
    assert first.property_type == "plot"
    assert first.published_at == 1_791_936_000_000
    assert first.images == [
        "https://alertyprzetargowe.pl/storage/97696/01M4XG542EJJ41EP59JSA73PSE.webp",
    ]

    garage = page.items[2]
    assert garage.property_type == "garage"
    assert garage.price_pln is None
    assert garage.area_m2 is None

    assert all(item.voivodeship == "malopolskie" for item in page.items)


def test_parse_search_rejects_unknown_pages() -> None:
    query = _adapter().queries(("malopolskie",))[0]

    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>Just a moment</body></html>", query)


def test_parse_detail_flat_fixture() -> None:
    summary = Listing(
        source_id="alertyprzetargowe",
        external_id="lokal-mieszkalny-nr-10",
        url="https://alertyprzetargowe.pl/nieruchomosci/lokal-mieszkalny-nr-10",
        price_pln=1,
    )

    detail = _adapter().parse_detail(_fixture("detail-flat.html"), summary)

    assert detail.title == (
        "Lokal mieszkalny nr 10 o powierzchni 48,45 m2, zlokalizowany na II piętrze "
        "w budynku mieszkalnym położonym w Sosnowcu przy ul. J.I.Kraszewskiego 15"
    )
    assert detail.price_pln == 247_000
    assert detail.area_m2 == 48.45
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.price_kind == "starting"
    assert detail.city == "Sosnowiec"
    assert detail.voivodeship == "slaskie"
    assert detail.lat == 50.27799
    assert detail.lng == 19.1268
    assert detail.geo_source == "source"
    assert detail.seller_type == "government"
    assert detail.published_at == 1_794_268_800_000
    assert detail.auction_date == 1_799_884_800_000
    assert detail.description is not None
    assert "cena wywoławcza" in detail.description
    assert len(detail.images) >= 5
    assert detail.extra["otoprzetargi"]["edition"] == "10/11/2026"


def test_parse_detail_plot_fixture() -> None:
    summary = Listing(
        source_id="alertyprzetargowe",
        external_id="dzialka-katowice-ul-szopienicka-i-sowki-pow-7156-m2",
        url=(
            "https://alertyprzetargowe.pl/nieruchomosci/"
            "dzialka-katowice-ul-szopienicka-i-sowki-pow-7156-m2"
        ),
    )

    detail = _adapter().parse_detail(_fixture("detail-plot.html"), summary)

    assert detail.title == "Działka Katowice ul. Szopienicka i Sówki pow. 7156 m²"
    assert detail.price_pln == 1_144_200
    assert detail.area_m2 == 7156.0
    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.price_kind == "asking"
    assert detail.city == "Katowice"
    assert detail.voivodeship == "slaskie"
    assert detail.lat == 50.26489
    assert detail.lng == 19.02378
    assert detail.description is not None
    assert detail.description.startswith("Działka gruntu nr 2193/86")


def test_badge_mapping_is_order_independent() -> None:
    adapter = _adapter()

    assert adapter._offer_type("Sprzedaż", "other") == "sale"
    assert adapter._offer_type("Wynajem", "other") == "rent"
    assert adapter._offer_type("Dzierżawa", "other") == "lease"
    assert adapter._property_type("garaż", "other") == "garage"
    assert adapter._property_type("Obiekt użytkowy", "other") == "commercial"
    assert adapter._property_type("Mieszkanie", "other") == "flat"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("819.000,00", 819_000),
        ("1.926.000,00 zł", 1_926_000),
        ("247 000,00", 247_000),
        ("650,00", 650),
        ("1 100 000", 1_100_000),
        (None, None),
    ],
)
def test_parse_amount(raw: str | None, expected: int | None) -> None:
    assert _parse_amount(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("48.45 m2", 48.45),
        ("2700.00", 2700.0),
        ("1.234,56 m²", 1234.56),
        ("12 600,00", 12_600.0),
        (None, None),
    ],
)
def test_parse_measure(raw: str | None, expected: float | None) -> None:
    assert _parse_measure(raw) == expected

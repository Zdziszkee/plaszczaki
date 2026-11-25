from __future__ import annotations

from pathlib import Path

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.olx import OlxAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "olx"


def _adapter() -> OlxAdapter:
    return OlxAdapter(
        source_id="olx",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(key: str):
    return next(query for query in _adapter().queries(("malopolskie",)) if query.key == key)


def test_queries_cover_regions_and_categories() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 26
    assert {query.region for query in queries} == {"malopolskie", "slaskie"}
    assert {query.offer_type for query in queries} == {"sale", "rent"}
    assert {query.property_type for query in queries} == {
        "flat",
        "house",
        "plot",
        "commercial",
        "garage",
        "room",
    }

    by_key = {query.key: query for query in queries}
    assert (
        by_key["malopolskie:sprzedaz:mieszkania"].url
        == "https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/malopolskie/"
    )
    assert (
        by_key["slaskie:wynajem:garaze-parkingi"].url
        == "https://www.olx.pl/nieruchomosci/garaze-parkingi/wynajem/slaskie/"
    )
    assert (
        by_key["malopolskie:all:stancje-pokoje"].url
        == "https://www.olx.pl/nieruchomosci/stancje-pokoje/malopolskie/"
    )
    assert by_key["malopolskie:all:stancje-pokoje"].offer_type == "rent"
    assert by_key["malopolskie:all:stancje-pokoje"].property_type == "room"


def test_with_page_adds_page_parameter() -> None:
    adapter = _adapter()
    query = _query("malopolskie:sprzedaz:mieszkania")

    assert adapter.with_page(query, 1).url == query.url

    paged = adapter.with_page(query, 3)

    assert paged.url.endswith("?page=3")
    assert paged.page == 3


def test_parse_search_fixture() -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    adapter = _adapter()

    page = adapter.parse_search(html, _query("malopolskie:sprzedaz:mieszkania"))

    assert page.current_page == 1
    assert page.total_pages == 7
    assert page.total_items == 261
    # The third ad links to Otodom and must be skipped.
    assert len(page.items) == 2

    flat = page.items[0]
    assert flat.external_id == "900000001"
    assert flat.url == "https://www.olx.pl/d/oferta/example-3-room-flat-CID3-ID900000001.html"
    assert flat.title == "Example 3-room flat with balcony"
    assert flat.offer_type == "sale"
    assert flat.property_type == "flat"
    assert flat.market == "secondary"
    assert flat.price_kind == "asking"
    assert flat.price_unit == "total"
    assert flat.price_pln == 709000
    assert flat.price_per_m2_pln == 9779
    assert flat.area_m2 == 72.5
    assert flat.rooms == 4
    assert flat.voivodeship == "malopolskie"
    assert flat.city == "Kraków"
    assert flat.district == "Prądnik Biały"
    assert flat.lat == pytest.approx(50.03574)
    assert flat.lng == pytest.approx(19.22832)
    assert flat.geo_source == "source"
    assert flat.images == [
        "https://example.test/olx/flat-1.jpg",
        "https://example.test/olx/flat-2.jpg",
    ]

    second = page.items[1]
    assert second.external_id == "900000002"
    assert second.offer_type == "sale"
    assert second.property_type == "flat"
    assert second.market == "primary"
    assert second.price_pln == 500000
    assert second.area_m2 == 48.34
    assert second.rooms == 2
    assert second.city == "Wieliczka"
    assert second.seller_type == "agency"


def test_parse_detail_fixture() -> None:
    search_html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    detail_html = (FIXTURES / "detail.html").read_text(encoding="utf-8")
    adapter = _adapter()
    summary = adapter.parse_search(search_html, _query("malopolskie:sprzedaz:mieszkania")).items[0]

    detail = adapter.parse_detail(detail_html, summary)

    assert detail.external_id == "900000001"
    assert detail.url == summary.url
    assert detail.title == "Example 3-room flat with balcony"
    assert detail.description == (
        "Example description with a balcony. Second line of the description."
    )
    assert detail.offer_type == "sale"
    assert detail.property_type == "flat"
    assert detail.market == "secondary"
    assert detail.price_kind == "asking"
    assert detail.price_pln == 709000
    assert detail.price_per_m2_pln == 9779
    assert detail.area_m2 == 72.5
    assert detail.rooms == 4
    assert detail.floor == 3
    assert detail.floor_label == "3"
    assert detail.voivodeship == "malopolskie"
    assert detail.city == "Kraków"
    assert detail.district == "Prądnik Biały"
    assert detail.address_text == "Kraków, Prądnik Biały"
    assert detail.lat == pytest.approx(50.03574)
    assert detail.lng == pytest.approx(19.22832)
    assert detail.geo_source == "source"
    assert detail.seller_type == "private"
    assert detail.seller_name == "Example Seller"
    assert detail.published_at == 1793568142000
    assert detail.modified_at == 1795550583000
    assert detail.extra["olx"]["params"]["rooms"]["normalized"] == "four"
    assert detail.extra["olx"]["category_id"] == 14


def test_parse_detail_rejects_page_without_state() -> None:
    adapter = _adapter()
    summary = Listing(
        source_id="olx",
        external_id="900000001",
        url="https://www.olx.pl/d/oferta/example-3-room-flat-CID3-ID900000001.html",
        offer_type="sale",
        property_type="flat",
    )
    html = '<html><script type="application/ld+json">{"@type": "Product"}</script></html>'

    with pytest.raises(ParseError, match="coordinates unavailable"):
        adapter.parse_detail(html, summary)


def test_parse_search_dom_fallback() -> None:
    html = """
    <html><body>
      <div data-cy="l-card" id="900000009">
        <a data-testid="card-title-link"
           href="/d/oferta/example-flat-CID3-ID900000009.html">
          <h4>Example flat from DOM</h4>
        </a>
        <p data-testid="ad-price">500 000 zł</p>
        <p data-testid="location-date">Kraków - Odświeżono dnia 24 listopada 2026</p>
        <span>50 m²</span>
      </div>
    </body></html>
    """

    page = _adapter().parse_search(html, _query("malopolskie:sprzedaz:mieszkania"))

    assert page.current_page == 1
    assert page.total_pages is None
    assert len(page.items) == 1
    item = page.items[0]
    assert item.external_id == "900000009"
    assert item.url == "https://www.olx.pl/d/oferta/example-flat-CID3-ID900000009.html"
    assert item.title == "Example flat from DOM"
    assert item.price_pln == 500000
    assert item.area_m2 == 50
    assert item.city == "Kraków"
    assert item.voivodeship == "malopolskie"
    assert item.geo_source is None

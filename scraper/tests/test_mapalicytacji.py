from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from plaszczaki_scraper.adapters.base import ParseError
from plaszczaki_scraper.adapters.mapalicytacji import (
    BASE_URL,
    MapalicytacjiAdapter,
)
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "mapalicytacji"
WARSAW = ZoneInfo("Europe/Warsaw")


def _adapter() -> MapalicytacjiAdapter:
    return MapalicytacjiAdapter(
        source_id="mapalicytacji",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(region: str = "slaskie"):
    return _adapter().queries((region,))[0]


def _search_page():
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query())


def test_queries_cover_both_regions() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert [query.key for query in queries] == ["malopolskie:all", "slaskie:all"]
    assert [query.region for query in queries] == ["malopolskie", "slaskie"]
    assert queries[0].url == f"{BASE_URL}/wojewodztwo/malopolskie"
    assert queries[1].url == f"{BASE_URL}/wojewodztwo/slaskie"
    assert {query.offer_type for query in queries} == {"auction"}


def test_queries_accept_registry_aliases_and_skip_unknown_regions() -> None:
    adapter = _adapter()

    aliased = adapter.queries(("małopolskie",))
    assert len(aliased) == 1
    assert aliased[0].region == "malopolskie"
    assert aliased[0].key == "malopolskie:all"

    assert adapter.queries(("opolskie", "dolnoslaskie")) == []


def test_with_page_uses_strona_parameter_and_keeps_page_one_clean() -> None:
    adapter = _adapter()
    query = _query()

    first = adapter.with_page(query, 1)
    second = adapter.with_page(query, 2)

    assert first.page == 1
    assert first.url == f"{BASE_URL}/wojewodztwo/slaskie"
    assert second.page == 2
    assert second.url == f"{BASE_URL}/wojewodztwo/slaskie?strona=2"
    assert adapter.with_page(second, 1).url == f"{BASE_URL}/wojewodztwo/slaskie"


def test_parse_search_fixture() -> None:
    page = _search_page()

    assert page.current_page == 1
    assert page.total_pages == 6
    assert page.total_items == 332
    assert len(page.items) == 3

    flat = page.items[0]
    assert flat.source_id == "mapalicytacji"
    assert flat.external_id == "90169"
    assert flat.url == f"{BASE_URL}/licytacja/90169/lokal-katowice"
    assert flat.title == "lokal"
    assert flat.offer_type == "auction"
    assert flat.property_type == "flat"
    assert flat.price_kind == "starting"
    assert flat.price_pln == 403500
    assert flat.area_m2 == 44.0
    assert flat.voivodeship == "slaskie"
    assert flat.city == "Katowice"
    assert flat.street == "Al. Bolesława Krzywoustego"
    assert flat.address_text == "Al. Bolesława Krzywoustego, 40-870 Katowice"
    assert flat.auction_date == int(datetime(2026, 11, 26, 9, 0, tzinfo=WARSAW).timestamp() * 1000)
    assert flat.extra["mapalicytacji"]["postal_code"] == "40-870"
    assert flat.extra["mapalicytacji"]["mode"] == "e-licytacja"
    assert flat.extraction_method == "adapter"

    plot = page.items[1]
    assert plot.external_id == "86958"
    assert plot.property_type == "plot"
    assert plot.price_pln == 23250
    assert plot.city == "Dąbrowa Górnicza"
    assert plot.street == "Przelotowa"
    assert plot.extra["mapalicytacji"]["postal_code"] == "42-523"
    assert plot.auction_round == 2

    commercial = page.items[2]
    assert commercial.external_id == "89942"
    assert commercial.property_type == "commercial"
    assert commercial.city == "Częstochowa"
    assert commercial.extra["mapalicytacji"]["mode"] == "stacjonarna"
    assert commercial.price_pln == 294000


def test_parse_detail_fixture_enriches_from_json_ld() -> None:
    summary = _search_page().items[0]
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    # identity and search-page fields survive
    assert detail.external_id == "90169"
    assert detail.url == summary.url
    assert detail.title == "lokal"
    assert detail.offer_type == "auction"
    assert detail.market == "secondary"
    assert detail.price_unit == "total"

    # location from JSON-LD about.address / about.geo
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Katowice"
    assert detail.street == "Al. Bolesława Krzywoustego"
    assert detail.address_text == "Al. Bolesława Krzywoustego, 40-870 Katowice"
    assert detail.lat == pytest.approx(50.275)
    assert detail.lng == pytest.approx(18.983)
    assert detail.geo_source == "source"

    # auction terms from the detail table / JSON-LD
    assert detail.property_type == "flat"
    assert detail.price_kind == "starting"
    assert detail.price_pln == 403500
    assert detail.area_m2 == 44.17
    assert detail.auction_date == int(
        datetime(2026, 11, 26, 9, 0, tzinfo=WARSAW).timestamp() * 1000
    )
    assert detail.seller_type == "government"
    assert detail.seller_name == "Komornik sądowy"
    assert detail.case_number is None
    assert detail.description is not None
    assert "cena wywołania: 403 500 zł" in detail.description
    assert detail.extra["mapalicytacji"]["postal_code"] == "40-870"
    assert detail.extra["mapalicytacji"]["estimated_price_pln"] == 538000
    assert detail.extra["mapalicytacji"]["mode"] == "e-licytacja"


def test_detail_ignores_nearby_offer_badges() -> None:
    """The "in the area" cards on a detail page must not leak their round."""
    summary = _search_page().items[0]
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")
    nearby = (
        '<div class="wrap okolica"><article class="card"><a href="/licytacja/1/x">'
        '<div><span class="badge cat">Mieszkania</span>'
        '<span class="badge druga">II licytacja</span></div>'
        "<h3>inna</h3></a></article></div>"
    )

    detail = _adapter().parse_detail(html.replace("</main>", f"{nearby}</main>"), summary)

    assert detail.auction_round is None


def test_detail_handles_json_ld_list_and_main_entity_nesting() -> None:
    html = """
    <html><head>
      <script type="application/ld+json">
      [
        {"@type": "WebPage", "mainEntity": {
          "@type": "RealEstateListing",
          "name": "lokal testowy",
          "description": "Krótki opis licytacji.",
          "offers": [{"@type": "Offer", "price": "123 000 zł"}],
          "about": {
            "@type": "Apartment",
            "address": {
              "@type": "PostalAddress",
              "streetAddress": "Rynek 1",
              "postalCode": "31-042",
              "addressLocality": "Kraków",
              "addressRegion": "małopolskie"
            },
            "geo": {"@type": "GeoCoordinates", "latitude": "50.0614", "longitude": 19.9366}
          }
        }},
        {"@type": "SaleEvent", "endDate": "2026-12-10T10:00:00+01:00"}
      ]
      </script>
    </head><body><h1>lokal testowy</h1></body></html>
    """
    summary = Listing(
        source_id="mapalicytacji",
        external_id="777",
        url="https://mapalicytacji.pl/licytacja/777/lokal-testowy",
        title=None,
        offer_type="auction",
    )

    detail = _adapter().parse_detail(html, summary)

    assert detail.title == "lokal testowy"
    assert detail.description == "Krótki opis licytacji."
    assert detail.street == "Rynek 1"
    assert detail.city == "Kraków"
    assert detail.voivodeship == "malopolskie"
    assert detail.address_text == "Rynek 1, 31-042 Kraków"
    assert detail.lat == pytest.approx(50.0614)
    assert detail.lng == pytest.approx(19.9366)
    assert detail.geo_source == "source"
    assert detail.price_pln == 123000
    assert detail.auction_date == int(
        datetime(2026, 12, 10, 10, 0, tzinfo=WARSAW).timestamp() * 1000
    )


def test_detail_keeps_out_of_scope_listing_with_its_own_voivodeship() -> None:
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type": "RealEstateListing", "name": "mieszkanie", "description": "Opis",
       "about": {"@type": "Apartment",
         "address": {"streetAddress": "Marszałkowska", "postalCode": "00-001",
                     "addressLocality": "Warszawa", "addressRegion": "mazowieckie"},
         "geo": {"latitude": 52.2297, "longitude": 21.0122}}}
      </script>
    </head><body><h1>mieszkanie</h1></body></html>
    """
    summary = _search_page().items[0]

    detail = _adapter().parse_detail(html, summary)

    assert detail.voivodeship == "mazowieckie"
    assert detail.city == "Warszawa"
    assert detail.lat == pytest.approx(52.2297)
    assert detail.lng == pytest.approx(21.0122)


def test_empty_listing_page_is_not_an_error() -> None:
    html = "<html><body><h2>Lista licytacji (0)</h2></body></html>"

    page = _adapter().parse_search(html, _query())

    assert page.items == []
    assert page.total_pages == 1
    assert page.total_items == 0


def test_unrecognised_pages_raise_parse_error() -> None:
    adapter = _adapter()

    with pytest.raises(ParseError):
        adapter.parse_search("<html><body>przerwa techniczna</body></html>", _query())

    with pytest.raises(ParseError):
        adapter.parse_detail("<html><body>404</body></html>", _search_page().items[0])

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from plaszczaki_scraper.adapters.base import ParseError, Query, SearchPage
from plaszczaki_scraper.adapters.srk import SITE_ROOT, SrkAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.models import Listing
from plaszczaki_scraper.regions import RegionRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "srk"
WARSAW = ZoneInfo("Europe/Warsaw")


def _warsaw_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=WARSAW).timestamp() * 1000)


def _adapter() -> SrkAdapter:
    return SrkAdapter(
        source_id="srk",
        regions=RegionRegistry(load_regions_config()),
    )


def _query(key: str = "slaskie:przetargi-nieruchomosci") -> Query:
    return next(query for query in _adapter().queries(("slaskie",)) if query.key == key)


def _parse(name: str, key: str = "slaskie:przetargi-nieruchomosci") -> SearchPage:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return _adapter().parse_search(html, _query(key))


def _item(external_id: str) -> Listing:
    return next(item for item in _parse("search.html").items if item.external_id == external_id)


def test_adapter_identity() -> None:
    adapter = _adapter()

    assert adapter.id == "srk"
    assert adapter.source_id == "srk"


def test_queries_cover_slaskie_tender_lists() -> None:
    queries = _adapter().queries(("slaskie",))

    assert {query.key for query in queries} == {
        "slaskie:przetargi-nieruchomosci",
        "slaskie:przetargi-mieszkania",
        "slaskie:przetargi-najem-dzierzawa",
        "slaskie:oferty-nieruchomosci",
    }
    assert {query.region for query in queries} == {"slaskie"}
    assert {query.offer_type for query in queries} == {"sale", "lease"}
    land = next(query for query in queries if query.key == "slaskie:przetargi-nieruchomosci")
    assert land.url == f"{SITE_ROOT}/oferta/przetargi-nieruchomosci"
    lease = next(query for query in queries if query.key == "slaskie:przetargi-najem-dzierzawa")
    assert lease.offer_type == "lease"


def test_queries_are_empty_outside_slaskie() -> None:
    adapter = _adapter()

    assert adapter.queries(("malopolskie",)) == []
    assert adapter.queries(("slaskie", "malopolskie")) == adapter.queries(("slaskie",))


def test_with_page_uses_the_strona_parameter() -> None:
    query = _query()

    first = _adapter().with_page(query, 1)
    second = _adapter().with_page(query, 2)

    assert first.url == f"{SITE_ROOT}/oferta/przetargi-nieruchomosci"
    assert first.page == 1
    assert second.url == f"{SITE_ROOT}/oferta/przetargi-nieruchomosci?strona=2"
    assert second.page == 2


def test_parse_search_lists_tenders() -> None:
    page = _parse("search.html")

    assert page.current_page == 1
    assert page.total_items == 28
    assert page.total_pages == 6
    assert len(page.items) == 5

    land = page.items[0]
    assert land.external_id == "przetargi-nieruchomosci/2626"
    assert land.url == f"{SITE_ROOT}/oferta/przetargi-nieruchomosci/2626"
    assert land.title == (
        "Sprzedaż nieruchomości w Katowicach przy ul.Górniczego Dorobku o powierzchni 4,2049 ha"
    )
    assert land.offer_type == "sale"
    assert land.property_type == "plot"
    assert land.voivodeship == "slaskie"
    assert land.city == "Katowice"
    assert land.street == "Górniczego Dorobku"
    assert land.address_text == "Górniczego Dorobku, Katowice"
    # 4,2049 ha is exposed in square metres on the plot field.
    assert land.land_area_m2 == pytest.approx(42049.0)
    assert land.area_m2 is None
    assert land.auction_date == _warsaw_ms(2026, 12, 11, 12, 0)
    # The table has no price and no coordinates.
    assert land.price_pln is None
    assert land.lat is None
    assert land.lng is None
    assert land.geo_source is None

    flat = _item("przetargi-nieruchomosci/2623")
    assert flat.offer_type == "sale"
    assert flat.property_type == "other"
    assert flat.city == "Wodzisław Śląski"
    assert flat.street == "Św. Wawrzyńca"
    assert flat.auction_date == _warsaw_ms(2026, 12, 11, 10, 0)


def test_parse_search_handles_rejon_locations() -> None:
    item = _item("przetargi-nieruchomosci/2512")

    assert item.city == "Gliwice"
    assert item.street == "Szygarskiej"
    assert item.land_area_m2 == pytest.approx(2952.0)
    assert item.auction_date == _warsaw_ms(2026, 12, 8, 11, 0)


def test_parse_search_detects_flat_and_area_from_title() -> None:
    html = """
    <html><body>
      <table class="przetargi przetargi__lista rwd">
        <tr class="przetarg__item">
          <td data-title="Tytuł przetargu"><strong>Sprzedaż lokalu mieszkalnego w Bytomiu przy ul. Czajkowskiego 2/1</strong></td>
          <td data-title="Lokalizacja przedmiotu przetargu">Bytom ul. Czajkowskiego 2/1</td>
          <td data-title="Data przetargu">
            <span class="przetarg__data">2027-01-26</span>
            <span class="przetarg__godzina">11:30:00</span>
            <a href="https://srk.com.pl/oferta/przetargi-mieszkania/2621">szczegóły</a>
          </td>
        </tr>
      </table>
    </body></html>
    """

    page = _adapter().parse_search(html, _query("slaskie:przetargi-mieszkania"))

    assert len(page.items) == 1
    flat = page.items[0]
    assert flat.external_id == "przetargi-mieszkania/2621"
    assert flat.property_type == "flat"
    assert flat.city == "Bytom"
    assert flat.street == "Czajkowskiego"
    assert flat.address_text == "Czajkowskiego 2/1, Bytom"
    assert flat.auction_date == _warsaw_ms(2027, 1, 26, 11, 30)


def test_parse_search_maps_lease_tenders() -> None:
    html = """
    <html><body>
      <table class="przetargi przetargi__lista rwd">
        <tr class="przetarg__item">
          <td data-title="Tytuł przetargu"><strong>Najem lokalu użytkowego w Bytomiu przy ul. Dworcowej 1 o powierzchni 120 m2</strong></td>
          <td data-title="Lokalizacja przedmiotu przetargu">Bytom ul. Dworcowa 1</td>
          <td data-title="Data przetargu">
            <span class="przetarg__data">2026-12-20</span>
            <span class="przetarg__godzina">09:00:00</span>
            <a href="https://srk.com.pl/oferta/przetargi-najem-dzierzawa/3000">szczegóły</a>
          </td>
        </tr>
      </table>
    </body></html>
    """

    page = _adapter().parse_search(html, _query("slaskie:przetargi-najem-dzierzawa"))

    lease = page.items[0]
    assert lease.offer_type == "lease"
    assert lease.property_type == "commercial"
    assert lease.price_unit == "monthly"
    assert lease.area_m2 == pytest.approx(120.0)
    assert lease.land_area_m2 is None


def test_parse_search_reads_the_sale_catalogue() -> None:
    page = _parse("oferty.html", "slaskie:oferty-nieruchomosci")

    assert page.total_items == 274
    assert page.total_pages == 28
    assert len(page.items) == 3

    plot = page.items[0]
    assert plot.external_id == "oferty-nieruchomosci/2295"
    assert plot.url == f"{SITE_ROOT}/oferta/oferty-nieruchomosci/2295"
    assert plot.offer_type == "sale"
    assert plot.property_type == "plot"
    assert plot.price_pln == 8_670_000
    assert plot.price_kind == "starting"
    assert plot.land_area_m2 == pytest.approx(42049.0)
    assert plot.city == "Katowice"
    assert plot.street == "Górniczego Dorobku"
    assert plot.images == [
        f"{SITE_ROOT}/assets/pics/galerie/2303/mini/image6.jpeg",
    ]

    # The garage card carries no gmina, so no city is invented.
    garage = page.items[2]
    assert garage.property_type == "garage"
    assert garage.price_pln == 303_640
    assert garage.area_m2 == pytest.approx(473.0)
    assert garage.street == "Św. Wawrzyńca"
    assert garage.city is None


def test_parse_search_empty_tender_list_is_not_an_error() -> None:
    page = _parse("empty.html", "slaskie:przetargi-najem-dzierzawa")

    assert page.items == []
    assert page.total_pages == 1
    assert page.current_page == 1


def test_parse_search_unrecognised_page_raises() -> None:
    with pytest.raises(ParseError):
        _adapter().parse_search("<html><body>maintenance</body></html>", _query())


def test_parse_detail_enriches_a_tender() -> None:
    summary = _item("przetargi-nieruchomosci/2626")
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert detail.url == summary.url
    assert detail.title == summary.title
    assert detail.offer_type == "sale"
    assert detail.property_type == "plot"
    assert detail.voivodeship == "slaskie"
    assert detail.city == "Katowice"
    assert detail.street == "Górniczego Dorobku"
    assert detail.address_text == "Górniczego Dorobku, Katowice"
    assert detail.land_area_m2 == pytest.approx(42049.0)
    assert detail.auction_date == _warsaw_ms(2026, 12, 11, 12, 0)
    assert detail.published_at == _warsaw_ms(2026, 11, 24)
    assert detail.lat is None
    assert detail.lng is None
    assert detail.geo_source is None
    assert detail.price_pln is None
    assert detail.extra["srk"]["place"] == "portal aukcyjny"
    assert detail.extra["srk"]["files"] == [
        "https://srk.com.pl/download/"
        "OGLOSZENIE-11.12.2026-Katowice-ul.-Gorniczego-Dorobku,11389.pdf",
    ]


def test_parse_detail_enriches_a_sale_offer() -> None:
    summary = next(
        item
        for item in _parse("oferty.html", "slaskie:oferty-nieruchomosci").items
        if item.external_id == "oferty-nieruchomosci/2295"
    )
    html = (FIXTURES / "detail-oferta.html").read_text(encoding="utf-8")

    detail = _adapter().parse_detail(html, summary)

    assert detail.external_id == summary.external_id
    assert detail.price_pln == 8_670_000
    assert detail.price_kind == "asking"
    assert detail.city == "Katowice"
    assert detail.street == "Szopienicka"
    assert detail.address_text == "Szopienicka, Katowice"
    assert detail.land_area_m2 == pytest.approx(42049.0)
    assert detail.description is not None
    assert "Nikiszowiec" in detail.description
    assert detail.images == [
        f"{SITE_ROOT}/assets/pics/galerie/2303/Mapa1.jpg",
        f"{SITE_ROOT}/assets/pics/galerie/2303/IMG_20260422_131547.jpg",
    ]
    assert detail.lat is None
    assert detail.lng is None


def test_parse_detail_never_requires_the_page() -> None:
    summary = _item("przetargi-nieruchomosci/2626")

    assert _adapter().parse_detail("<html></html>", summary).to_json_dict() == (
        summary.to_json_dict()
    )
    assert _adapter().parse_detail("", summary).to_json_dict() == summary.to_json_dict()

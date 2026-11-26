from __future__ import annotations

from plaszczaki_scraper.adapters.kowr import KowrAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry


def _adapter() -> KowrAdapter:
    return KowrAdapter(
        source_id="kowr",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_use_region_location_ids() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert "location=88" in queries[0].url
    assert "location=94" in queries[1].url
    assert queries[0].region == "malopolskie"
    assert queries[1].region == "slaskie"


def test_with_page_is_one_based() -> None:
    query = _adapter().queries(("slaskie",))[0]
    paged = _adapter().with_page(query, 3)
    assert "page=3" in paged.url
    assert paged.page == 3


def test_parse_search_card() -> None:
    query = _adapter().queries(("slaskie",))[0]
    html = """
    <html><body><div class="immovables-list-item">
      <dl class="table-immovables-list">
        <dt>Cena wywoławcza:</dt><dd><b>362 000,00 zł</b></dd>
        <dt>Powierzchnia:</dt><dd><b>1,0200 ha</b></dd>
        <dt>Typ nieruchomości:</dt><dd><b>rolna</b></dd>
        <dt>Typ rozdysponowania:</dt><dd><b>sprzedaz</b></dd>
        <dt>Data przetargu:</dt><dd><b>2026-11-26</b></dd>
      </dl>
      <a href="https://www.nieruchomoscikowr.gov.pl/nieruchomosci/oferty/505585">
        <b>więcej</b>
        <span class="wcag-hide">o OT Częstochowa, działka nr 1533 obręb Zarębice, gmina Przyrów</span>
      </a>
    </div></body></html>
    """

    page = _adapter().parse_search(html, query)

    assert len(page.items) == 1
    item = page.items[0]
    assert item.external_id == "505585"
    assert item.voivodeship == "slaskie"
    assert item.property_type == "plot"
    assert item.price_pln == 362000
    assert item.land_area_m2 == 10200.0
    assert item.city == "Zarębice"
    assert item.auction_date is not None

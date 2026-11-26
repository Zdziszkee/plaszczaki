from __future__ import annotations

import json

from plaszczaki_scraper.adapters.pge import PgeAdapter
from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry


def _adapter() -> PgeAdapter:
    return PgeAdapter(
        source_id="pge",
        regions=RegionRegistry(load_regions_config()),
    )


def test_queries_use_voivodeship_codes() -> None:
    queries = _adapter().queries(("malopolskie", "slaskie"))

    assert len(queries) == 2
    assert "voivodeshipCode=12" in queries[0].url
    assert "voivodeshipCode=24" in queries[1].url


def test_parse_search_keeps_coordinates_and_address() -> None:
    query = _adapter().queries(("slaskie",))[0]
    payload = [
        {
            "id": "pge-1",
            "siteaccess": "pgeeo",
            "external_id": "14233",
            "name": "Sprzedaż budynku pokoi gościnnych w Międzybrodziu Żywieckim",
            "lat": 49.768143,
            "lng": 19.216313,
            "url": "//pgeeo.pl/nieruchomosci2/budynek",
            "media": ["//pgeeo.pl/image.webp"],
            "created_at": "2026-06-10T12:52:45+00:00",
            "type": "building",
            "purpose": "sale",
            "address": {
                "voivodeship": "ŚLĄSKIE",
                "voivodeship_code": "24",
                "county": "żywiecki",
                "city": "Międzybrodzie Żywieckie",
                "street": "ul. Beskidzka",
                "street_no": "43",
            },
            "plot_area": 1407.0,
            "price": 912589.0,
        },
        {
            "id": "pge-2",
            "external_id": "outer",
            "name": "Działka w Warszawie",
            "url": "//pge.pl/outer",
            "type": "plot",
            "purpose": "sale",
            "address": {"voivodeship": "MAZOWIECKIE", "voivodeship_code": "14"},
            "price": 1.0,
        },
    ]

    page = _adapter().parse_search(json.dumps(payload), query)

    assert len(page.items) == 1
    item = page.items[0]
    assert item.external_id == "14233"
    assert item.voivodeship == "slaskie"
    assert item.property_type == "commercial"
    assert item.price_pln == 912589
    assert item.lat == 49.768143
    assert item.lng == 19.216313
    assert item.geo_source == "source"
    assert item.street == "ul. Beskidzka 43"
    assert item.url.startswith("https://")

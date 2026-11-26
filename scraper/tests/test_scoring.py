from __future__ import annotations

import json
import sqlite3
from typing import Any

from plaszczaki_scraper.geodata import GeoContext
from plaszczaki_scraper.scoring import PriceModel, load_scoring_config, score_row

CONFIG = load_scoring_config()


def _row(**overrides: Any) -> sqlite3.Row:
    values: dict[str, Any] = {
        "id": 1,
        "source_id": "test",
        "external_id": "1",
        "offer_type": "sale",
        "property_type": "flat",
        "market": "secondary",
        "price_kind": "asking",
        "price_pln": 800_000,
        "rent_pln": None,
        "price_per_m2_pln": 20_000,
        "area_m2": 40,
        "land_area_m2": None,
        "rooms": 2,
        "build_year": 1925,
        "building_type": "tenement",
        "building_material": "brick",
        "heating": "district",
        "windows_type": "plastic",
        "ceiling_height_m": 3.2,
        "parking": None,
        "elevator": 0,
        "balcony": 1,
        "terrace": 0,
        "air_conditioning": 0,
        "condition": "good",
        "city": "Kraków",
        "district": "Stare Miasto",
        "county": "krakowski",
        "commune": "Kraków",
        "voivodeship": "małopolskie",
        "lat": 50.061,
        "lng": 19.936,
        "title": "Klimatyczna kamienica",
        "description": "Ocieplony budynek, nowe okna, przedwojenna kamienica z wysokimi sufitami.",
        "published_at": None,
        "first_seen_at": 1,
        "auction_round": None,
        "estimate_pln": None,
        "canonical_key": "test:1",
        "extra": "{}",
        "fenced": 0,
    }
    values.update(overrides)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(f"CREATE TABLE row ({', '.join(values)})")
    placeholders = ",".join("?" for _ in values)
    connection.execute(f"INSERT INTO row VALUES ({placeholders})", tuple(values.values()))
    cursor = connection.execute("SELECT * FROM row")
    result = cursor.fetchone()
    connection.close()
    assert result is not None
    return result


def _score(
    row: sqlite3.Row, rows: list[sqlite3.Row] | None = None, geo: GeoContext | None = None
) -> Any:
    return score_row(row, CONFIG, geo or GeoContext(), PriceModel(rows or [row]), {}, {})


def test_tenement_with_modern_windows_and_insulation_scores_high() -> None:
    result = _score(_row())
    assert result.score >= 85
    assert result.profile == "kamienica"
    reasons = {reason["code"] for reason in result.reasons}
    assert {"tenement", "district_heating", "insulation", "windows_new"} <= reasons


def test_prefabricated_block_gets_strong_penalty_and_cap() -> None:
    result = _score(
        _row(
            building_type="block",
            building_material="prefabricated_concrete",
            description="Duże mieszkanie w bloku z wielkiej płyty.",
        )
    )
    assert result.score <= CONFIG["prefab"]["cap"]
    assert any(reason["code"] == "prefab_penalty" for reason in result.reasons)


def test_modern_flat_is_rewarded_for_price_location_and_amenities() -> None:
    geo = GeoContext(pois=[{"lat": 50.061, "lng": 19.936, "cat": "grocery"}])
    geo.poi_index.add(0, 50.061, 19.936, 50.061, 19.936)
    result = _score(
        _row(
            build_year=2021,
            building_type="apartment_building",
            building_material="other",
            heating="gas",
            market="primary",
            elevator=1,
            parking="underground",
            title="Nowe mieszkanie",
            description="Nowoczesne mieszkanie w centrum.",
        ),
        geo=geo,
    )
    assert result.score >= 50
    assert result.profile == "nowe_mieszkanie"


def test_building_plot_with_target_station_beats_one_without() -> None:
    plot = _row(
        property_type="plot",
        price_pln=250_000,
        price_per_m2_pln=250,
        area_m2=None,
        land_area_m2=1000,
        build_year=None,
        building_type=None,
        building_material=None,
        heating=None,
        windows_type=None,
        title="Działka budowlana MPZP",
        description="Działka budowlana z MPZP, media przy działce.",
    )
    geo = GeoContext(
        stations=[
            {
                "name": "Kraków Główny",
                "lat": 50.067,
                "lng": 19.947,
                "destinations": {"warszawa": 40},
                "weekly_trips": 80,
            }
        ]
    )
    geo.station_index.add(0, 50.067, 19.947, 50.067, 19.947)
    with_station = _score(plot, geo=geo)
    without_station = _score(plot)
    assert with_station.score > without_station.score + 20
    assert with_station.profile == "dzialka_kolej"


def test_nature_plot_uses_airbnb_profile_when_no_station() -> None:
    plot = _row(
        property_type="plot",
        price_pln=180_000,
        price_per_m2_pln=120,
        area_m2=None,
        land_area_m2=1500,
        build_year=None,
        building_type=None,
        building_material=None,
        heating=None,
        windows_type=None,
        title="Działka przy jeziorze",
        description="Działka budowlana przy lesie nad jeziorem w górach.",
        lat=49.299,
        lng=19.95,
        city="Zakopane",
    )
    result = _score(plot)
    assert result.profile == "dzialka_airbnb"
    assert result.score >= 55
    assert any(reason["code"].startswith("nature_text_") for reason in result.reasons)


def test_comparable_sales_detect_undervalued_listing() -> None:
    target = _row(price_pln=500_000, price_per_m2_pln=12_500, canonical_key="target")
    comps = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=800_000,
            price_per_m2_pln=20_000,
            lat=50.061 + index * 0.001,
            canonical_key=f"comp:{index}",
        )
        for index in range(8)
    ]
    prices = PriceModel([target, *comps])
    comparable, count = prices.comparable(target, CONFIG["comps"])
    assert count >= 5
    assert comparable is not None and comparable > target["price_per_m2_pln"] * 1.2
    result = score_row(target, CONFIG, GeoContext(), prices, {}, {})
    assert any(reason["code"] == "comparable_discount" for reason in result.reasons)


def test_auction_discount_and_price_cut_are_explained() -> None:
    auction = _row(
        offer_type="auction",
        price_kind="starting",
        estimate_pln=400_000,
        auction_round=2,
        price_pln=267_000,
        price_per_m2_pln=6675,
    )
    result = score_row(
        auction,
        CONFIG,
        GeoContext(),
        PriceModel([auction]),
        {1: {"peak": 350_000, "drops": 1, "last": 267_000}},
        {},
    )
    reasons = {reason["code"] for reason in result.reasons}
    assert "auction_discount" in reasons
    assert "price_cut" in reasons


def test_scores_are_json_serializable_and_bounded() -> None:
    result = _score(_row())
    payload = json.dumps({"scores": result.scores, "reasons": result.reasons})
    assert "kamienica" in payload
    assert 0 <= result.score <= 100
    assert all(0 <= score <= 100 for score in result.scores.values())

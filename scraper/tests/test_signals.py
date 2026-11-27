from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

import pytest

from plaszczaki_scraper.geodata import GeoContext, PlannedSegment
from plaszczaki_scraper.signals import (
    PENALTY_CODES,
    PROFILE_NAMES,
    SIGNAL_CODES,
    SIGNALS_VERSION,
    PriceModel,
    extract_signals_row,
    load_signals_config,
)

CONFIG = load_signals_config()
NOW = 1_700_000_000_000
DAY_MS = 86_400_000


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
        "condition": "good",
        "building_type": "tenement",
        "building_material": "brick",
        "heating": "district",
        "windows_type": "plastic",
        "ceiling_height_m": 3.2,
        "parking": None,
        "elevator": 0,
        "balcony": 1,
        "terrace": 0,
        "garden": 0,
        "internet": 0,
        "air_conditioning": 0,
        "fenced": 0,
        "city": "Kraków",
        "district": "Stare Miasto",
        "county": "krakowski",
        "commune": "Kraków",
        "voivodeship": "małopolskie",
        "lat": 50.061,
        "lng": 19.936,
        "title": "Klimatyczna kamienica",
        "description": (
            "Ocieplony budynek, nowe okna, przedwojenna kamienica z wysokimi sufitami."
        ),
        "published_at": None,
        "first_seen_at": NOW - 10 * DAY_MS,
        "auction_round": None,
        "estimate_pln": None,
        "canonical_key": "test:1",
        "extra": "{}",
    }
    values.update(overrides)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(f"CREATE TABLE row ({', '.join(values)})")
    placeholders = ",".join("?" for _ in values)
    connection.execute(f"INSERT INTO row VALUES ({placeholders})", tuple(values.values()))
    result = connection.execute("SELECT * FROM row").fetchone()
    connection.close()
    assert result is not None
    return result


def _rcn_row(**overrides: Any) -> sqlite3.Row:
    values: dict[str, Any] = {
        "key": "rcn:1",
        "kind": "flat",
        "lat": 50.061,
        "lng": 19.936,
        "area": 40,
        "ppm": 20_000,
        "market": "secondary",
        "rooms": 2,
        "document_date": date.today().isoformat(),
    }
    values.update(overrides)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(f"CREATE TABLE row ({', '.join(values)})")
    placeholders = ",".join("?" for _ in values)
    connection.execute(f"INSERT INTO row VALUES ({placeholders})", tuple(values.values()))
    result = connection.execute("SELECT * FROM row").fetchone()
    connection.close()
    assert result is not None
    return result


def _extract(
    row: sqlite3.Row,
    *,
    rows: list[sqlite3.Row] | None = None,
    geo: GeoContext | None = None,
    history: dict[int, dict[str, Any]] | None = None,
    rent_ppm: dict[tuple[str, str], list[float]] | None = None,
    now_ms: int = NOW,
) -> dict[str, Any]:
    return extract_signals_row(
        row,
        CONFIG,
        geo or GeoContext(),
        PriceModel(rows or [row]),
        history or {},
        rent_ppm or {},
        now_ms=now_ms,
    )


def _profile(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return payload["profiles"][name]


def test_payload_has_all_profiles_and_only_bounded_normalized_factors() -> None:
    payload = _extract(_row())

    assert payload["version"] == SIGNALS_VERSION
    assert tuple(payload["profiles"]) == PROFILE_NAMES
    assert "score" not in payload
    assert "winner" not in payload
    assert json.loads(json.dumps(payload)) == payload
    for profile in payload["profiles"].values():
        assert set(profile) == {"signals", "penalties"}
        assert set(profile["signals"]) == set(SIGNAL_CODES)
        assert set(profile["penalties"]) == set(PENALTY_CODES)
        for group in profile.values():
            for value in group.values():
                assert value is None or 0.0 <= value <= 1.0


def test_tenement_extracts_character_insulation_windows_and_outdoor_signals() -> None:
    signals = _profile(_extract(_row()), "kamienica")["signals"]

    assert signals["building_character"] == 1.0
    assert signals["building_age"] == 1.0
    assert signals["district_heating"] == 1.0
    assert signals["insulation"] == 1.0
    assert signals["windows"] == 1.0
    assert signals["outdoor"] == 1.0
    assert signals["ceiling"] == 0.6


def test_prefabricated_material_produces_normalized_penalty_for_both_flat_profiles() -> None:
    payload = _extract(
        _row(
            building_type="block",
            building_material="prefabricated_concrete",
            title="Mieszkanie",
            description="Mieszkanie w bloku z wielkiej płyty.",
        )
    )

    assert _profile(payload, "kamienica")["penalties"]["prefab_penalty"] == 1.0
    assert _profile(payload, "nowe_mieszkanie")["penalties"]["prefab_penalty"] == 1.0


def test_ordinary_block_is_not_marked_as_prefabricated() -> None:
    payload = _extract(
        _row(
            build_year=2021,
            building_type="block",
            building_material="other",
            heating="gas",
            title="Mieszkanie w bloku",
            description="Mieszkanie w centrum.",
        )
    )

    assert _profile(payload, "kamienica")["penalties"]["prefab_penalty"] == 0.0
    assert _profile(payload, "nowe_mieszkanie")["penalties"]["prefab_penalty"] == 0.0
    assert _profile(payload, "nowe_mieszkanie")["signals"]["building_age"] == 1.0


def test_new_flat_extracts_age_market_and_amenities() -> None:
    payload = _extract(
        _row(
            build_year=2021,
            building_type="apartment_building",
            building_material="other",
            heating="gas",
            market="primary",
            condition="high_standard",
            elevator=1,
            parking="underground",
            air_conditioning=1,
            title="Nowe mieszkanie",
            description="Nowoczesne mieszkanie w centrum.",
        )
    )
    signals = _profile(payload, "nowe_mieszkanie")["signals"]

    assert signals["building_age"] == 1.0
    assert signals["condition"] == 1.0
    assert signals["primary_market"] == 1.0
    assert signals["parking"] == 1.0
    assert signals["elevator"] == 1.0
    assert signals["air_conditioning"] == 1.0
    assert _profile(payload, "nowe_mieszkanie")["penalties"]["old_building_penalty"] == 0.0


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("wysokosc sufitow 3,2 m", 3.2),
        ("sufity o wysokosci 3.1 m", 3.1),
        ("wysokie sufity (3,4 m)", 3.4),
        ("wysokość sufitów wynosi 3,25 m", 3.25),
    ],
)
def test_explicit_ceiling_dimensions_are_parsed(description: str, expected: float) -> None:
    payload = _extract(
        _row(
            ceiling_height_m=None,
            title="Mieszkanie",
            description=description,
        )
    )

    assert payload["metrics"]["ceiling_height_m"] == expected
    assert _profile(payload, "kamienica")["signals"]["ceiling"] == pytest.approx(
        (expected - 2.9) / 0.5
    )


def test_canonical_ceiling_height_takes_precedence_over_description() -> None:
    payload = _extract(
        _row(
            ceiling_height_m=3.2,
            title="Mieszkanie",
            description="wysokosc sufitow 2,5 m",
        )
    )

    assert payload["metrics"]["ceiling_height_m"] == 3.2
    assert _profile(payload, "kamienica")["signals"]["ceiling"] == 0.6


def test_generic_high_ceiling_text_uses_half_factor_without_inventing_height_or_charm() -> None:
    payload = _extract(
        _row(
            ceiling_height_m=None,
            title="Mieszkanie",
            description="Wysokie sufity w salonie.",
        )
    )

    assert payload["metrics"]["ceiling_height_m"] is None
    assert _profile(payload, "kamienica")["signals"]["ceiling"] == 0.5
    assert _profile(payload, "kamienica")["signals"]["charm"] is None


def test_numeric_high_ceiling_does_not_create_charm() -> None:
    payload = _extract(
        _row(
            ceiling_height_m=None,
            title="Mieszkanie",
            description="wysokosc sufitow 3,4 m",
        )
    )

    assert payload["metrics"]["ceiling_height_m"] == 3.4
    assert _profile(payload, "kamienica")["signals"]["ceiling"] == 1.0
    assert _profile(payload, "kamienica")["signals"]["charm"] is None


@pytest.mark.parametrize(
    ("height", "expected"),
    [(2.8, 0.0), (2.9, 0.0), (3.15, 0.5), (3.4, 1.0), (3.8, 1.0)],
)
def test_ceiling_signal_ramps_from_2_9_to_3_4_metres(height: float, expected: float) -> None:
    payload = _extract(_row(ceiling_height_m=height))

    assert _profile(payload, "kamienica")["signals"]["ceiling"] == expected


@pytest.mark.parametrize(
    ("description", "metric", "factor"),
    [
        ("bardzo duze okna", True, 1.0),
        ("okna od podlogi", True, 1.0),
        ("portfenetrowe", True, 1.0),
        ("panoramiczne okna", True, 1.0),
        ("wysokie okna", True, 1.0),
        ("duze okna", True, 0.5),
        ("standardowe okna", False, 0.0),
        ("niewielkie okna", False, 0.0),
        ("nowe okna", None, None),
    ],
)
def test_large_windows_require_explicit_text_evidence(
    description: str, metric: bool | None, factor: float | None
) -> None:
    payload = _extract(_row(title="Mieszkanie", description=description))

    assert payload["metrics"]["large_windows"] is metric
    assert _profile(payload, "kamienica")["signals"]["large_windows"] == factor


def test_old_charming_house_has_traditional_material_and_no_modern_penalty() -> None:
    payload = _extract(
        _row(
            property_type="house",
            price_pln=900_000,
            price_per_m2_pln=9000,
            area_m2=100,
            build_year=1920,
            building_type="house",
            building_material="wood",
            title="Przedwojenny dom",
            description="Zabytkowy drewniany dom z klimatem do odnowienia.",
            city="Zakopane",
            district="Zakopane",
            lat=49.299,
            lng=19.95,
        )
    )
    signals = _profile(payload, "dom_airbnb")["signals"]
    penalties = _profile(payload, "dom_airbnb")["penalties"]

    assert signals["old_house"] == 1.0
    assert signals["traditional_material"] == 1.0
    assert signals["charm"] == 1.0
    assert signals["mountains"] == 1.0
    assert signals["tourist_area"] == 1.0
    assert penalties["modern_house"] == 0.0
    assert penalties["no_charm"] == 0.0


def test_old_house_wording_produces_strong_but_not_full_charm_evidence() -> None:
    payload = _extract(
        _row(
            property_type="house",
            area_m2=100,
            build_year=None,
            building_material=None,
            title="Stary dom",
            description="Dom do odnowienia.",
        )
    )

    assert _profile(payload, "dom_airbnb")["signals"]["charm"] == 0.85
    assert _profile(payload, "dom_airbnb")["penalties"]["no_charm"] == 0.0


def test_modern_house_without_other_charm_has_normalized_penalties() -> None:
    payload = _extract(
        _row(
            property_type="house",
            price_pln=900_000,
            price_per_m2_pln=9000,
            area_m2=100,
            build_year=2015,
            building_type="house",
            building_material="other",
            title="Nowoczesny dom",
            description="Nowoczesny dom z garażem.",
            city="Zakopane",
            district="Zakopane",
            lat=49.299,
            lng=19.95,
        )
    )
    penalties = _profile(payload, "dom_airbnb")["penalties"]

    assert penalties["modern_house"] == 1.0
    assert penalties["no_charm"] == 1.0


def test_flat_walkability_and_commute_are_normalized_geo_signals() -> None:
    row = _row(build_year=2021, title="Nowe mieszkanie", description="Mieszkanie w centrum.")
    without = _extract(row)
    geo = GeoContext(
        pois=[
            {"lat": 50.061, "lng": 19.936, "cat": "grocery"},
            {"lat": 50.0611, "lng": 19.9361, "cat": "transit"},
        ],
        stations=[
            {
                "name": "Kraków Główny",
                "lat": 50.062,
                "lng": 19.937,
                "destinations": {"warszawa": 20},
                "weekly_trips": 40,
            }
        ],
    )
    geo.poi_index.add(0, 50.061, 19.936, 50.061, 19.936)
    geo.poi_index.add(1, 50.0611, 19.9361, 50.0611, 19.9361)
    geo.station_index.add(0, 50.062, 19.937, 50.062, 19.937)
    nearby = _extract(row, geo=geo)

    assert _profile(without, "nowe_mieszkanie")["signals"]["walkability"] is None
    assert _profile(nearby, "nowe_mieszkanie")["signals"]["walkability"] > 0
    assert _profile(nearby, "nowe_mieszkanie")["signals"]["commute"] == 1.0


def test_house_rail_signals_include_distance_frequency_gate_near_and_planned_rail() -> None:
    row = _row(
        property_type="house",
        price_pln=900_000,
        price_per_m2_pln=9000,
        area_m2=100,
        build_year=2015,
        building_type="house",
        title="Dom blisko stacji",
        description="Rodzinny dom z ogrodem.",
    )
    geo = GeoContext(
        stations=[
            {
                "name": "Kraków Główny",
                "lat": 50.08,
                "lng": 19.936,
                "destinations": {"warszawa": 40},
                "weekly_trips": 70,
            }
        ],
        planned=[
            PlannedSegment(
                status="construction",
                stage=None,
                target=None,
                program=None,
                project="Nowa linia",
                a=(50.061, 19.936),
                b=(50.061, 19.936),
            )
        ],
    )
    geo.station_index.add(0, 50.08, 19.936, 50.08, 19.936)
    geo.planned_index.add(0, 50.061, 19.936, 50.061, 19.936)
    signals = _profile(_extract(row, geo=geo), "dom_kolej")["signals"]

    assert signals["station"] == 1.0
    assert signals["station_frequency"] == 0.5
    assert signals["station_gate"] == 1.0
    assert signals["station_data_available"] == 1.0
    assert signals["planned_rail"] == 1.0


def test_missing_station_cache_leaves_rail_signals_unknown() -> None:
    row = _row(property_type="house", area_m2=100, title="Dom", description="Dom z ogrodem.")
    signals = _profile(_extract(row), "dom_kolej")["signals"]

    assert signals["station"] is None
    assert signals["station_frequency"] is None
    assert signals["station_gate"] is None
    assert signals["station_data_available"] == 0.0


def test_nature_geo_and_elevation_are_normalized() -> None:
    row = _row(
        property_type="house",
        area_m2=100,
        title="Dom",
        description="Dom w okolicy.",
        city="Zakopane",
        district="Zakopane",
        lat=49.299,
        lng=19.95,
    )
    ring = [[19.95, 49.299], [19.951, 49.299], [19.951, 49.3], [19.95, 49.3]]
    geo = GeoContext(
        forest=[ring],
        water=[ring],
        elevation={"49.2990,19.9500": 600.0},
    )
    geo.forest_index.add(0, 49.299, 19.95, 49.3, 19.951)
    geo.water_index.add(0, 49.299, 19.95, 49.3, 19.951)
    signals = _profile(_extract(row, geo=geo), "dom_airbnb")["signals"]

    assert signals["forest"] == 1.0
    assert signals["water"] == 1.0
    assert signals["mountains"] == 1.0
    assert signals["tourist_area"] == 1.0
    assert signals["elevation"] == 0.5


def test_building_plot_extracts_building_rights_utilities_fence_and_rail() -> None:
    row = _row(
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
        ceiling_height_m=None,
        fenced=1,
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
    payload = _extract(row, geo=geo)
    signals = _profile(payload, "dzialka_kolej")["signals"]

    assert signals["building_plot"] == 1.0
    assert signals["utilities"] == 1.0
    assert signals["fenced"] == 1.0
    assert signals["station_gate"] == 1.0
    assert _profile(payload, "dzialka_kolej")["penalties"]["non_building_plot"] == 0.0
    assert _profile(payload, "dzialka_airbnb")["penalties"]["build_unconfirmed"] == 0.0


def test_plot_without_confirmed_building_rights_has_penalties() -> None:
    unknown = _extract(
        _row(
            property_type="plot",
            area_m2=None,
            land_area_m2=1500,
            title="Działka w Tatrach",
            description="Działka w górach, blisko lasu i jeziora.",
            lat=49.299,
            lng=19.95,
            city="Zakopane",
        )
    )
    negative = _extract(
        _row(
            property_type="plot",
            area_m2=None,
            land_area_m2=1500,
            title="Działka rolna",
            description="Działka rolna przy lesie.",
        )
    )

    assert _profile(unknown, "dzialka_airbnb")["penalties"]["build_unconfirmed"] == 1.0
    assert _profile(unknown, "dzialka_kolej")["signals"]["building_plot"] is None
    assert _profile(unknown, "dzialka_kolej")["penalties"]["non_building_plot"] is None
    assert _profile(unknown, "dzialka_airbnb")["signals"]["forest"] == 0.65
    assert _profile(unknown, "dzialka_airbnb")["signals"]["water"] == 0.65
    assert _profile(unknown, "dzialka_airbnb")["signals"]["mountains"] == 1.0
    assert _profile(negative, "dzialka_airbnb")["penalties"]["non_building_plot"] == 1.0
    assert _profile(negative, "dzialka_airbnb")["signals"]["building_plot"] is None
    assert _profile(negative, "dzialka_airbnb")["penalties"]["build_unconfirmed"] == 0.0


def test_comparable_sales_produce_monotonic_discount_and_overpriced_penalty() -> None:
    comps = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=800_000,
            price_per_m2_pln=20_000,
            canonical_key=f"comp:{index}",
        )
        for index in range(8)
    ]
    cheap = _row(
        id=100,
        external_id="cheap",
        price_pln=400_000,
        price_per_m2_pln=10_000,
        canonical_key="price-cheap",
    )
    expensive = _row(
        id=101,
        external_id="expensive",
        price_pln=1_200_000,
        price_per_m2_pln=30_000,
        canonical_key="price-expensive",
    )
    prices = PriceModel([cheap, expensive, *comps])
    cheap_payload = extract_signals_row(cheap, CONFIG, GeoContext(), prices, {}, {}, now_ms=NOW)
    expensive_payload = extract_signals_row(
        expensive, CONFIG, GeoContext(), prices, {}, {}, now_ms=NOW
    )

    cheap_value = _profile(cheap_payload, "nowe_mieszkanie")["signals"]["comparable_discount"]
    expensive_value = _profile(expensive_payload, "nowe_mieszkanie")["signals"][
        "comparable_discount"
    ]
    assert cheap_value == 1.0
    assert expensive_value == 0.0
    assert _profile(cheap_payload, "nowe_mieszkanie")["signals"]["price_quantile"] == 1.0
    assert cheap_payload["priceEvidence"]["listing"] is not None
    assert expensive_payload["priceEvidence"]["overpriced"] is True
    assert _profile(expensive_payload, "nowe_mieszkanie")["penalties"]["overpriced"] == 1.0


def test_comparables_prefer_nearest_similar_offers_over_cheapest() -> None:
    target = _row(
        price_pln=600_000,
        price_per_m2_pln=15_000,
        canonical_key="target",
    )
    cheap_far = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=400_000,
            price_per_m2_pln=10_000,
            lat=50.08,
            lng=19.936,
            canonical_key=f"far:{index}",
        )
        for index in range(35)
    ]
    expensive_near = [
        _row(
            id=index + 40,
            external_id=str(index + 40),
            price_pln=840_000,
            price_per_m2_pln=21_000,
            lat=50.062,
            lng=19.936,
            canonical_key=f"near:{index}",
        )
        for index in range(10)
    ]
    prices = PriceModel([target, *cheap_far, *expensive_near])

    comparable, count = prices.comparable(target, CONFIG["comps"])
    assert count == 30
    assert comparable == 21_000


def test_auction_discount_market_time_and_price_cut_are_normalized() -> None:
    auction = _row(
        offer_type="auction",
        price_kind="starting",
        estimate_pln=400_000,
        auction_round=2,
        price_pln=267_000,
        price_per_m2_pln=6675,
        published_at=NOW - 40 * DAY_MS,
    )
    signals = _profile(
        _extract(auction, history={1: {"peak": 350_000, "drops": 1, "last": 267_000}}),
        "kamienica",
    )["signals"]

    assert signals["auction_discount"] == 1.0
    assert signals["market_time"] == 1.0
    assert signals["price_cut"] == 1.0


def test_rental_yield_uses_local_rent_samples_and_normalizes_to_zero_one() -> None:
    row = _row()
    samples = {("flat", "krakow"): [100.0] * 10}
    signals = _profile(_extract(row, rent_ppm=samples), "kamienica")["signals"]

    assert signals["rental_yield"] == 0.6


def test_rcn_transactions_are_used_for_price_evidence() -> None:
    target = _row(price_pln=600_000, price_per_m2_pln=15_000, canonical_key="target")
    listings = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=800_000,
            price_per_m2_pln=20_000,
            canonical_key=f"listing:{index}",
        )
        for index in range(8)
    ]
    prices = PriceModel([target, *listings])
    prices.load_rcn(_rcn_row(key=f"rcn:{index}", ppm=20_000) for index in range(8))

    evidence = prices.evidence(target, CONFIG["comps"], CONFIG["rcn"], CONFIG["value"])
    assert evidence.rcn is not None
    assert evidence.rcn.count == 8
    assert evidence.benchmark_source == "rcn"
    assert evidence.effective_discount == 0.25
    assert evidence.top_eligible is True
    assert evidence.as_json()["topEligible"] is True

    payload = extract_signals_row(target, CONFIG, GeoContext(), prices, {}, {}, now_ms=NOW)
    assert payload["priceEvidence"] == evidence.as_json()


def test_rcn_comparables_match_market_and_room_count() -> None:
    target = _row(market="secondary", rooms=2, canonical_key="target")
    prices = PriceModel([target])
    rows = [
        *(_rcn_row(key=f"primary:{index}", market="primary", ppm=5_000) for index in range(5)),
        *(_rcn_row(key=f"rooms:{index}", rooms=4, ppm=7_000) for index in range(5)),
        *(_rcn_row(key=f"match:{index}", ppm=20_000) for index in range(5)),
    ]
    prices.load_rcn(rows)

    evidence = prices.evidence(target, CONFIG["comps"], CONFIG["rcn"], CONFIG["value"])
    assert evidence.rcn is not None
    assert evidence.rcn.count == 5
    assert evidence.rcn.median == 20_000


def test_effective_discount_uses_the_smaller_verified_advantage() -> None:
    target = _row(price_pln=600_000, price_per_m2_pln=15_000, canonical_key="target")
    listings = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=1_000_000,
            price_per_m2_pln=25_000,
            canonical_key=f"listing:{index}",
        )
        for index in range(5)
    ]
    prices = PriceModel([target, *listings])
    prices.load_rcn(_rcn_row(key=f"rcn:{index}", ppm=20_000) for index in range(5))

    evidence = prices.evidence(target, CONFIG["comps"], CONFIG["rcn"], CONFIG["value"])
    assert evidence.listing is not None and evidence.listing.median == 25_000
    assert evidence.rcn is not None and evidence.rcn.median == 20_000
    assert evidence.effective_discount == 0.25


def test_rcn_plot_and_house_kinds_use_property_specific_matching() -> None:
    plot = _row(
        property_type="plot",
        area_m2=None,
        land_area_m2=1000,
        price_pln=200_000,
        price_per_m2_pln=200,
        canonical_key="plot",
    )
    prices = PriceModel([plot])
    prices.load_rcn(
        _rcn_row(key=f"plot:{index}", kind="plot", area=1800, ppm=300) for index in range(5)
    )
    plot_evidence = prices.evidence(plot, CONFIG["comps"], CONFIG["rcn"], CONFIG["value"])
    assert plot_evidence.rcn is not None
    assert plot_evidence.rcn.count == 5
    assert plot_evidence.rcn.median == 300

    house = _row(
        property_type="house",
        area_m2=100,
        price_pln=800_000,
        price_per_m2_pln=8_000,
        canonical_key="house",
    )
    prices.load_rcn(
        _rcn_row(key=f"house:{index}", kind="building", area=100, ppm=10_000) for index in range(5)
    )
    house_evidence = prices.evidence(house, CONFIG["comps"], CONFIG["rcn"], CONFIG["value"])
    assert house_evidence.rcn is not None
    assert house_evidence.rcn.count == 5
    assert house_evidence.rcn.median == 10_000


def test_baseline_excludes_the_target_listing() -> None:
    target = _row(price_pln=4_000_000, price_per_m2_pln=100_000, canonical_key="target")
    comps = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=800_000,
            price_per_m2_pln=20_000,
            canonical_key=f"comp:{index}",
        )
        for index in range(20)
    ]
    prices = PriceModel([target, *comps])
    baseline = prices.baseline(target)

    assert baseline is not None
    assert baseline.count == 20
    assert baseline.median == 20_000
    payload = extract_signals_row(target, CONFIG, GeoContext(), prices, {}, {}, now_ms=NOW)
    assert _profile(payload, "nowe_mieszkanie")["signals"]["price_quantile"] == 0.0


def test_price_quantile_rewards_prices_below_the_local_lower_quartile() -> None:
    target = _row(
        price_pln=400_000,
        price_per_m2_pln=10_000,
        canonical_key="target",
    )
    comps = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=800_000,
            price_per_m2_pln=20_000,
            canonical_key=f"comp:{index}",
        )
        for index in range(20)
    ]
    payload = _extract(target, rows=[target, *comps])

    assert _profile(payload, "nowe_mieszkanie")["signals"]["price_quantile"] == 1.0


def test_unsupported_property_has_empty_profiles_and_no_price_evidence() -> None:
    payload = _extract(_row(property_type="commercial"))

    assert payload["priceEvidence"] is None
    for profile in payload["profiles"].values():
        assert all(value is None for value in profile["signals"].values())
        assert all(value is None for value in profile["penalties"].values())

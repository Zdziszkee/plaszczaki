from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from typing import Any

from plaszczaki_scraper.geodata import GeoContext
from plaszczaki_scraper.scoring import PriceModel, ScoreParts, load_scoring_config, score_row

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
        "garden": 0,
        "internet": 0,
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


def _score(
    row: sqlite3.Row, rows: list[sqlite3.Row] | None = None, geo: GeoContext | None = None
) -> Any:
    return score_row(row, CONFIG, geo or GeoContext(), PriceModel(rows or [row]), {}, {})


def test_tenement_with_modern_windows_and_insulation_scores_high() -> None:
    result = _score(_row())
    assert result.profile == "kamienica"
    assert result.score >= 40
    reasons = {reason["code"] for reason in result.reasons}
    assert {"building_character", "district_heating", "insulation", "windows"} <= reasons


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


def test_prefabricated_material_is_detected_from_canonical_value() -> None:
    result = _score(
        _row(
            building_type="block",
            building_material="prefabricated_concrete",
            title="Mieszkanie",
            description="Mieszkanie w centrum.",
        )
    )
    assert result.score <= CONFIG["prefab"]["cap"]
    assert any(reason["code"] == "prefab_penalty" for reason in result.reasons)


def test_ordinary_block_is_not_penalized_as_prefabricated() -> None:
    result = _score(
        _row(
            build_year=2021,
            building_type="block",
            building_material="other",
            heating="gas",
            title="Mieszkanie w bloku",
            description="Mieszkanie w centrum.",
        )
    )
    assert result.profile == "nowe_mieszkanie"
    assert all(reason["code"] != "prefab_penalty" for reason in result.reasons)


def test_base_fit_points_are_not_counted() -> None:
    result = _score(
        _row(
            build_year=None,
            building_type="block",
            building_material="other",
            heating="gas",
            windows_type=None,
            ceiling_height_m=None,
            balcony=0,
            terrace=0,
            title="Mieszkanie",
            description="Mieszkanie do wynajęcia.",
            first_seen_at=None,
        )
    )
    assert result.scores["nowe_mieszkanie"] == 0
    assert all(
        reason["code"] != "fit"
        for profile in result.breakdown["profiles"].values()
        for reason in profile["contributions"]
    )


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
    assert result.score >= 20
    assert result.profile == "nowe_mieszkanie"
    reason_codes = {reason["code"] for reason in result.reasons}
    assert {"building_age", "primary_market", "parking", "elevator"} <= reason_codes


def test_new_flat_commute_rewards_nearby_station() -> None:
    row = _row(
        build_year=2021,
        building_type="apartment_building",
        building_material="other",
        heating="gas",
        title="Nowe mieszkanie",
        description="Mieszkanie w centrum.",
    )
    without = _score(row)
    geo = GeoContext(
        stations=[
            {
                "name": "Kraków Główny",
                "lat": 50.062,
                "lng": 19.937,
                "destinations": {"warszawa": 20},
                "weekly_trips": 40,
            }
        ]
    )
    geo.station_index.add(0, 50.062, 19.937, 50.062, 19.937)
    nearby = _score(row, geo=geo)
    assert nearby.scores["nowe_mieszkanie"] > without.scores["nowe_mieszkanie"]
    assert any(reason["code"] == "commute" for reason in nearby.reasons)


def test_modern_house_without_charm_is_capped() -> None:
    geo = GeoContext(
        forest=[[[19.936, 50.061], [19.937, 50.061], [19.937, 50.062], [19.936, 50.062]]],
        water=[[[19.936, 50.061], [19.937, 50.061], [19.937, 50.062], [19.936, 50.062]]],
        elevation={"50.0610,19.9360": 850.0},
    )
    geo.forest_index.add(0, 50.061, 19.936, 50.062, 19.937)
    geo.water_index.add(0, 50.061, 19.936, 50.062, 19.937)
    result = _score(
        _row(
            property_type="house",
            price_pln=900_000,
            price_per_m2_pln=9000,
            area_m2=100,
            build_year=2015,
            building_type="house",
            building_material="other",
            heating="gas",
            windows_type="plastic",
            title="Nowoczesny dom",
            description="Nowoczesny dom z garażem.",
            city="Zakopane",
            district="Zakopane",
            lat=49.299,
            lng=19.95,
        ),
        geo=geo,
    )
    assert result.score <= CONFIG["house"]["no_charm_cap"]
    airbnb = result.breakdown["profiles"]["dom_airbnb"]
    assert airbnb["cap"] == CONFIG["house"]["no_charm_cap"]
    assert any(reason["code"] == "no_charm" for reason in airbnb["penalties"])


def test_old_charming_house_is_not_capped() -> None:
    result = _score(
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
            lat=49.299,
            lng=19.95,
        )
    )
    assert result.profile == "dom_airbnb"
    assert result.breakdown["profiles"]["dom_airbnb"]["capLabel"] is None
    assert any(reason["code"] == "charm" for reason in result.reasons)


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
    assert with_station.score > without_station.score + 15
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
    assert result.score >= 40
    assert any(reason["code"] in {"forest", "water", "mountains"} for reason in result.reasons)


def test_plot_with_unconfirmed_building_rights_is_capped() -> None:
    result = _score(
        _row(
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
            title="Działka w Tatrach",
            description="Działka w górach, blisko lasu i jeziora.",
            lat=49.299,
            lng=19.95,
            city="Zakopane",
        )
    )
    assert result.score <= CONFIG["plot_airbnb"]["unconfirmed_build_cap"]
    assert any(reason["code"] == "build_unconfirmed" for reason in result.reasons)


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


def test_profile_weights_sum_to_100() -> None:
    for weights in CONFIG["profile_weights"].values():
        assert sum(weights.values()) == 100


def test_final_score_uses_half_up_rounding() -> None:
    parts = ScoreParts("nowe_mieszkanie", {"price": 100})
    parts.add(
        "test",
        "test",
        64.5,
        category="price",
        max_points=100,
        source="test",
    )
    assert parts.finish() == 65


def test_breakdown_reconciles_with_every_profile_score() -> None:
    result = _score(_row())
    assert result.breakdown["version"] == CONFIG["version"]
    for profile, detail in result.breakdown["profiles"].items():
        raw = round(
            sum(item["points"] for item in detail["contributions"])
            + sum(item["points"] for item in detail["penalties"]),
            1,
        )
        assert detail["rawScore"] == raw
        cap = detail["cap"] if detail["cap"] is not None else 100
        expected = 0 if detail["eligible"] is False else min(raw, cap)
        assert detail["score"] == max(0, min(100, math.floor(expected + 0.5)))
        assert result.scores[profile] == detail["score"]


def test_house_has_airbnb_and_rail_profiles() -> None:
    row = _row(
        property_type="house",
        price_pln=900_000,
        price_per_m2_pln=9000,
        area_m2=100,
        build_year=2015,
        building_type="house",
        building_material="brick",
        title="Dom blisko stacji",
        description="Rodzinny dom z ogrodem i parkingiem.",
    )
    geo = GeoContext(
        stations=[
            {
                "name": "Kraków Główny",
                "lat": 50.062,
                "lng": 19.937,
                "destinations": {"warszawa": 40},
                "weekly_trips": 80,
            }
        ]
    )
    geo.station_index.add(0, 50.062, 19.937, 50.062, 19.937)
    result = _score(row, geo=geo)
    assert set(result.scores) == {"dom_airbnb", "dom_kolej"}
    assert result.breakdown["profiles"]["dom_kolej"]["eligible"] is True
    assert result.scores["dom_kolej"] > 0


def test_rail_profile_is_ineligible_without_station_gate() -> None:
    row = _row(
        property_type="house",
        price_pln=900_000,
        price_per_m2_pln=9000,
        area_m2=100,
        build_year=2015,
        building_type="house",
        title="Dom poza zasięgiem stacji",
        description="Dom z ogrodem.",
    )
    geo = GeoContext(
        stations=[
            {
                "name": "Daleka stacja",
                "lat": 50.12,
                "lng": 19.936,
                "destinations": {"warszawa": 10},
                "weekly_trips": 20,
            }
        ]
    )
    geo.station_index.add(0, 50.12, 19.936, 50.12, 19.936)
    result = _score(row, geo=geo)
    rail = result.breakdown["profiles"]["dom_kolej"]
    assert rail["eligible"] is False
    assert rail["score"] == 0
    assert rail["cap"] == 0


def test_price_points_are_monotonic_and_profile_scaled() -> None:
    comps = [
        _row(
            id=index + 2,
            external_id=str(index + 2),
            price_pln=800_000,
            price_per_m2_pln=20_000,
            canonical_key=f"price-comp:{index}",
        )
        for index in range(20)
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
    cheap_result = score_row(cheap, CONFIG, GeoContext(), prices, {}, {})
    expensive_result = score_row(expensive, CONFIG, GeoContext(), prices, {}, {})

    def quantile_points(result: Any, profile: str) -> float:
        detail = result.breakdown["profiles"][profile]
        return next(
            item["points"]
            for item in [*detail["contributions"], *detail["zero"]]
            if item["code"] == "price_quantile"
        )

    assert quantile_points(cheap_result, "nowe_mieszkanie") == 12.0
    assert quantile_points(expensive_result, "nowe_mieszkanie") == 0.0
    assert quantile_points(cheap_result, "nowe_mieszkanie") <= (
        CONFIG["profile_weights"]["nowe_mieszkanie"]["price"] * CONFIG["value"]["quantile_share"]
    )


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

    result = score_row(target, CONFIG, GeoContext(), prices, {}, {})
    assert result.breakdown["priceEvidence"] == evidence.as_json()
    profile = result.breakdown["profiles"][result.profile]
    comparable = next(
        item for item in profile["contributions"] if item["code"] == "comparable_discount"
    )
    assert comparable["source"] == "rcn"
    assert "RCN" in comparable["label"]


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
    baseline = PriceModel([target, *comps]).baseline(target)
    assert baseline is not None
    assert baseline.count == 20
    assert baseline.median == 20_000


def test_overpriced_listing_is_penalized_and_capped() -> None:
    target = _row(price_pln=1_200_000, price_per_m2_pln=30_000, canonical_key="target")
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
    result = score_row(target, CONFIG, GeoContext(), PriceModel([target, *comps]), {}, {})

    assert result.score <= CONFIG["value"]["overpriced_cap"]
    assert result.breakdown["priceEvidence"]["overpriced"] is True
    assert result.breakdown["priceEvidence"]["topEligible"] is False
    assert any(
        reason["code"] == "overpriced"
        for profile in result.breakdown["profiles"].values()
        for reason in profile["penalties"]
    )


def test_missing_comparables_do_not_earn_full_price_points_or_top_eligibility() -> None:
    result = _score(_row())
    evidence = result.breakdown["priceEvidence"]
    assert evidence["listing"] is None
    assert evidence["rcn"] is None
    assert evidence["topEligible"] is False
    for profile in result.breakdown["profiles"].values():
        price_points = sum(
            reason["points"]
            for reason in [*profile["contributions"], *profile["zero"]]
            if reason["category"] == "price"
        )
        assert price_points == 0

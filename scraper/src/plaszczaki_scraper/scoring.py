"""Explainable, training-free attractiveness scoring for stored listings.

Five profiles produce independent 0-100 scores. The listing's primary score is
the best matching profile. All signals come from canonical fields, listing text,
the listing database itself, or open reference geodata — no trained models.

Price uses local quantiles and distance-weighted comparable sales. Opportunity
signals add auction discount, days on market, price cuts, and gross rental yield.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.geodata import GeoContext, haversine_km
from plaszczaki_scraper.normalize import normalize_text

PROFILE_LABELS = {
    "kamienica": "Klimatyczna kamienica",
    "nowe_mieszkanie": "Nowe mieszkanie",
    "dzialka_kolej": "Działka pod kolej",
    "dom_airbnb": "Dom pod Airbnb",
    "dzialka_airbnb": "Działka przy naturze",
}


@dataclass(slots=True)
class Reason:
    code: str
    label: str
    points: float


@dataclass(slots=True)
class ScoreParts:
    profile: str
    points: float = 0.0
    reasons: list[Reason] = field(default_factory=list)

    def add(self, code: str, label: str, points: float) -> None:
        self.points += points
        if abs(points) >= 0.05:
            self.reasons.append(Reason(code, label, round(points, 1)))

    def finish(self, *, cap: float | None = None, multiplier: float = 1.0) -> int:
        value = self.points * multiplier
        if cap is not None:
            value = min(value, cap)
        return int(max(0, min(100, round(value))))


@dataclass(frozen=True, slots=True)
class Comparable:
    key: str
    lat: float
    lng: float
    area: float
    ppm: float
    building_type: str


@dataclass(slots=True)
class PriceBaseline:
    low: float
    median: float
    high: float
    count: int
    level: str


@dataclass(slots=True)
class GeoSignals:
    station_name: str | None = None
    station_km: float | None = None
    station_destinations: tuple[str, ...] = ()
    weekly_trips: float = 0.0
    station_gate: bool = False
    station_points: float = 0.0
    planned_points: float = 0.0
    planned_label: str | None = None
    forest_km: float | None = None
    water_km: float | None = None
    forest_points: float = 0.0
    water_points: float = 0.0
    mountain: str | None = None
    mountain_points: float = 0.0
    tourist: bool = False
    walkability: float | None = None
    elevation_m: float | None = None


@dataclass(slots=True)
class ScoredListing:
    row_id: int
    score: int
    profile: str
    scores: dict[str, int]
    reasons: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


def _json(row: sqlite3.Row, column: str) -> Any:
    value = row[column]
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _text(row: sqlite3.Row, column: str) -> str:
    value = row[column]
    return str(value or "").strip()


def _number(row: sqlite3.Row, column: str) -> float | None:
    value = row[column]
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except TypeError, ValueError:
        return None
    return result if math.isfinite(result) else None


def _area(row: sqlite3.Row) -> float | None:
    area = _number(row, "area_m2")
    if area is None and _text(row, "property_type") == "plot":
        area = _number(row, "land_area_m2")
    return area if area is not None and area > 0 else None


def _price(row: sqlite3.Row) -> float | None:
    price = _number(row, "price_pln")
    if price is None and _text(row, "offer_type") == "rent":
        price = _number(row, "rent_pln")
    return price if price is not None and price > 0 else None


def _ppm(row: sqlite3.Row) -> float | None:
    value = _number(row, "price_per_m2_pln")
    if value is not None and value > 0:
        return value
    price, area = _price(row), _area(row)
    return price / area if price is not None and area is not None else None


def _sale(row: sqlite3.Row) -> bool:
    return _text(row, "offer_type") in {"sale", "auction"}


def _building_type(row: sqlite3.Row) -> str:
    value = _text(row, "building_type")
    normalized = normalize_text(value)
    aliases = {
        "blok": "block",
        "niski blok": "block",
        "blok mieszkalny": "block",
        "dom wielorodzinny": "apartment_building",
        "apartment building": "apartment_building",
        "inny": "other",
    }
    return aliases.get(normalized, value or normalized)


def _heating(row: sqlite3.Row) -> str:
    value = normalize_text(_text(row, "heating"))
    return {"co_miejskie": "district", "miejskie": "district", "siec_miejska": "district"}.get(
        value, value
    )


def _windows(row: sqlite3.Row) -> str:
    value = normalize_text(_text(row, "windows_type"))
    return {
        "nowe_wymienione": "new",
        "czesciowo_wymienione": "partially_new",
        "nowe": "new",
    }.get(value, value)


def _material(row: sqlite3.Row) -> str:
    value = normalize_text(_text(row, "building_material"))
    return {
        "cegla": "brick",
        "wielka_plyta": "prefabricated_concrete",
        "prefabrykowan": "prefabricated_concrete",
    }.get(value, value)


def _description(row: sqlite3.Row) -> str:
    return normalize_text(f"{_text(row, 'title')} {_text(row, 'description')}")


def _matches(text: str, keyword: str | list[str]) -> bool:
    if isinstance(keyword, list):
        return all(part in text for part in keyword)
    return keyword in text


def _has(text: str, keywords: list[Any]) -> bool:
    return any(_matches(text, keyword) for keyword in keywords)


def _city(row: sqlite3.Row) -> str:
    return _text(row, "city") or _text(row, "commune") or ""


def _district(row: sqlite3.Row) -> str:
    return _text(row, "district") or _text(row, "city")


def _extra_scoring_features(row: sqlite3.Row) -> dict[str, Any]:
    extra = _json(row, "extra") or {}
    if not isinstance(extra, dict):
        return {}
    features = extra.get("scoring_features")
    return features if isinstance(features, dict) else {}


# ---------------------------------------------------------------------------
# Baseline and comparable-sales pricing
# ---------------------------------------------------------------------------


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


class PriceModel:
    """Hierarchical local price quantiles + training-free comparable sales."""

    COMP_CELL = 0.02  # ~2.2 km; avoids scanning whole city blocks for every offer.

    def __init__(self, rows: list[sqlite3.Row]) -> None:
        self.groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        self.comps: dict[str, list[sqlite3.Row]] = defaultdict(list)
        self.comp_index: dict[str, dict[tuple[int, int], list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        seen: set[str] = set()
        for row in rows:
            value = _ppm(row)
            if not _sale(row) or value is None or value <= 0:
                continue
            property_type = _text(row, "property_type")
            key = row["canonical_key"] or f"{row['source_id']}:{row['external_id']}"
            dedupe_key = f"{property_type}:{key}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            city = normalize_text(_city(row))
            district = normalize_text(_district(row))
            county = normalize_text(_text(row, "county"))
            voivodeship = normalize_text(_text(row, "voivodeship"))
            for group in (
                (property_type, city, district),
                (property_type, city, ""),
                (property_type, county, ""),
                (property_type, voivodeship, ""),
                (property_type, "", ""),
            ):
                self.groups[group].append(value)
            lat, lng = _number(row, "lat"), _number(row, "lng")
            if lat is not None and lng is not None:
                index = len(self.comps[property_type])
                self.comps[property_type].append(row)
                cell = (int(lat // self.COMP_CELL), int(lng // self.COMP_CELL))
                self.comp_index[property_type][cell].append(index)

    def baseline(self, row: sqlite3.Row) -> PriceBaseline | None:
        property_type = _text(row, "property_type")
        candidates = (
            (
                (property_type, normalize_text(_city(row)), normalize_text(_district(row))),
                8,
                "dzielnica",
            ),
            ((property_type, normalize_text(_city(row)), ""), 15, "miasto"),
            ((property_type, normalize_text(_text(row, "county")), ""), 12, "powiat"),
            ((property_type, normalize_text(_text(row, "voivodeship")), ""), 25, "województwo"),
            ((property_type, "", ""), 20, "kraj"),
        )
        for key, minimum, level in candidates:
            values = self.groups.get(key, [])
            if len(values) >= minimum:
                return PriceBaseline(
                    _quantile(values, 0.25),
                    _quantile(values, 0.5),
                    _quantile(values, 0.75),
                    len(values),
                    level,
                )
        return None

    def comparable(self, row: sqlite3.Row, config: dict[str, Any]) -> tuple[float | None, int]:
        property_type = _text(row, "property_type")
        lat, lng, area, value = _number(row, "lat"), _number(row, "lng"), _area(row), _ppm(row)
        if lat is None or lng is None or area is None or value is None:
            return None, 0
        radius = float(config.get("radius_km", 2.5))
        ratio_low, ratio_high = config.get("area_ratio", [0.7, 1.4])
        candidates: list[tuple[float, float]] = []
        cx, cy = int(lat // self.COMP_CELL), int(lng // self.COMP_CELL)
        cells = self.comp_index[property_type]
        source_key = row["canonical_key"] or f"{row['source_id']}:{row['external_id']}"
        span = max(1, math.ceil(radius / (self.COMP_CELL * 111.0)))
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                for index in cells.get((cx + dx, cy + dy), []):
                    candidate = self.comps[property_type][index]
                    other_area, other_value = _area(candidate), _ppm(candidate)
                    if other_area is None or other_value is None:
                        continue
                    if not (ratio_low <= other_area / area <= ratio_high):
                        continue
                    other_lat, other_lng = _number(candidate, "lat"), _number(candidate, "lng")
                    if other_lat is None or other_lng is None:
                        continue
                    distance = haversine_km(lat, lng, other_lat, other_lng)
                    if distance > radius:
                        continue
                    key = (
                        candidate["canonical_key"]
                        or f"{candidate['source_id']}:{candidate['external_id']}"
                    )
                    if key == source_key:
                        continue
                    # Similar material/build type makes the comparison stronger.
                    similarity = 1.0
                    own_type, other_type = _building_type(row), _building_type(candidate)
                    if own_type and other_type and own_type != other_type:
                        similarity *= 0.75
                    weight = (1.0 / (1.0 + distance)) * similarity
                    candidates.append((other_value, weight))
        if len(candidates) < int(config.get("min_count", 5)):
            return None, len(candidates)
        candidates = sorted(candidates, key=lambda item: item[0])[:30]
        total = sum(weight for _, weight in candidates)
        if total <= 0:
            return None, len(candidates)
        cumulative = 0.0
        for value, weight in candidates:
            cumulative += weight
            if cumulative >= total / 2:
                return value, len(candidates)
        return candidates[-1][0], len(candidates)


def _rent_yield(row: sqlite3.Row, rent_ppm: dict[tuple[str, str], list[float]]) -> float | None:
    if not _sale(row) or _text(row, "property_type") not in {"flat", "house"}:
        return None
    price, area = _price(row), _area(row)
    if price is None or area is None:
        return None
    property_type = _text(row, "property_type")
    samples = rent_ppm.get((property_type, normalize_text(_city(row))))
    if not samples or len(samples) < 10:
        samples = rent_ppm.get((property_type, normalize_text(_text(row, "county"))))
    if not samples or len(samples) < 6:
        samples = rent_ppm.get((property_type, normalize_text(_text(row, "voivodeship"))))
    if not samples:
        return None
    monthly_per_m2 = _quantile(samples, 0.5)
    return monthly_per_m2 * area * 12 / price * 100


def _price_points(value: float, baseline: PriceBaseline, maximum: float) -> tuple[float, str]:
    if baseline.median <= 0:
        return 0.0, ""
    if value <= baseline.low:
        points, label = maximum, f"cena poniżej dolnego kwantyla ({baseline.level})"
    else:
        discount = (baseline.median - value) / baseline.median
        factor = max(0.05, min(1.0, 0.5 + discount * 5.0))
        points = maximum * factor
        if value <= baseline.median:
            label = f"{-discount * 100:.0f}% poniżej mediany ({baseline.level}, n={baseline.count})"
        elif value <= baseline.high:
            label = f"cena w zakresie mediany-górnego kwantyla ({baseline.level})"
        else:
            label = f"cena powyżej górnego kwantyla ({baseline.level})"
    return round(points, 1), label


def _weighted_comparable_points(
    value: float, comparable: float | None, count: int, maximum: float
) -> tuple[float, str]:
    if comparable is None or count < 5:
        return 0.0, ""
    discount = (comparable - value) / comparable
    if discount < 0.08:
        return 0.0, ""
    points = maximum * min(1.0, (discount - 0.05) / 0.25)
    return round(points, 1), f"{discount * 100:.0f}% poniżej mediany {count} podobnych ofert"


# ---------------------------------------------------------------------------
# Geo signals
# ---------------------------------------------------------------------------


def _geo_signals(row: sqlite3.Row, config: dict[str, Any], geo: GeoContext) -> GeoSignals:
    result = GeoSignals()
    property_type = _text(row, "property_type")
    if property_type not in {"flat", "house", "plot"}:
        return result
    lat, lng = _number(row, "lat"), _number(row, "lng")
    if lat is None or lng is None:
        return result

    if property_type == "plot":
        rail = config["rail"]
        nearest = geo.nearest_station(lat, lng, destinations=set(rail["destinations"]))
        if nearest is not None:
            station, distance = nearest
            destinations = tuple(
                city
                for city in rail["destinations"]
                if float(station.get("destinations", {}).get(city, 0.0)) > 0
            )
            result.station_name = station.get("name")
            result.station_km = round(distance, 2)
            result.station_destinations = destinations
            result.weekly_trips = float(station.get("weekly_trips", 0.0))
            result.station_gate = distance <= float(rail["gate_km"]) and bool(destinations)
            if result.station_gate:
                result.station_points += float(rail["gate_points"])
            elif distance <= float(rail["decay_km"]):
                decay = max(0.0, 1.0 - distance / float(rail["decay_km"]))
                result.station_points += float(rail["far_points"]) * decay
            frequency_cap = float(rail["frequency_cap_trips_per_week"])
            result.station_points += float(rail["frequency_points"]) * min(
                1.0, result.weekly_trips / frequency_cap
            )
            result.station_points = round(result.station_points, 1)
        planned_points, planned_label = geo.planned_rail_bonus(lat, lng, config["planned_rail"])
        result.planned_points = planned_points
        result.planned_label = planned_label

    if property_type in {"house", "plot"}:
        nature = config["nature"]
        for kind in ("forest", "water"):
            settings = nature[kind]
            distance = geo.nature_distance_km(lat, lng, kind)
            distance_attr = f"{kind}_km"
            points_attr = f"{kind}_points"
            setattr(result, distance_attr, round(distance, 2) if distance is not None else None)
            if distance is not None:
                full, zero = float(settings["full_m"]) / 1000, float(settings["zero_m"]) / 1000
                if distance <= full:
                    points = float(settings["points"])
                elif distance >= zero:
                    points = 0.0
                else:
                    points = float(settings["points"]) * (1.0 - (distance - full) / (zero - full))
                setattr(result, points_attr, round(points, 1))

        for region in config["mountain_regions"]:
            min_lng, min_lat, max_lng, max_lat = region["bbox"]
            if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat:
                result.mountain = region["name"]
                result.mountain_points = float(region["points"])
                break
        location = normalize_text(f"{_city(row)} {_district(row)} {_text(row, 'commune')}")
        result.tourist = any(normalize_text(city) in location for city in config["tourist_cities"])
        result.elevation_m = geo.elevation_m(lat, lng)

    if property_type == "flat":
        result.walkability = geo.walkability(lat, lng) if geo.pois else None
    return result


# ---------------------------------------------------------------------------
# Profile scoring
# ---------------------------------------------------------------------------


def _price_signal(
    parts: ScoreParts,
    row: sqlite3.Row,
    config: dict[str, Any],
    prices: PriceModel,
) -> None:
    value = _ppm(row)
    baseline = prices.baseline(row)
    if value is None or baseline is None or not _sale(row):
        if value is None:
            parts.add("price_missing", "brak ceny do oceny", 0.0)
        return
    maximum = float(config["price"].get(parts.profile, 10))
    points, label = _price_points(value, baseline, maximum)
    if points:
        parts.add("price_quantile", label, points)


def _comparable_signal(
    parts: ScoreParts, row: sqlite3.Row, config: dict[str, Any], prices: PriceModel
) -> None:
    if not _sale(row):
        return
    value = _ppm(row)
    if value is None:
        return
    comparable, count = prices.comparable(row, config["comps"])
    points, label = _weighted_comparable_points(
        value, comparable, count, float(config["comps"]["max_points"])
    )
    if points:
        parts.add("comparable_discount", label, points)


def _opportunity_signal(
    parts: ScoreParts,
    row: sqlite3.Row,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> None:
    if not _sale(row):
        return
    price, estimate = _price(row), _number(row, "estimate_pln")
    round_number = _number(row, "auction_round") or 1
    if (
        _text(row, "offer_type") == "auction"
        and price is not None
        and estimate is not None
        and estimate > 0
    ):
        discount = (estimate - price) / estimate
        if round_number >= 2 or discount >= 0.15:
            parts.add(
                "auction_discount",
                f"II runda licytacji / dyskont {-discount * 100:.0f}% do wyceny",
                8.0,
            )
        elif discount >= 0.08:
            parts.add("auction_discount", f"dyskont {discount * 100:.0f}% do wyceny", 4.0)
    published = _number(row, "published_at") or _number(row, "first_seen_at")
    if published is not None:
        days = max(0.0, (time.time() * 1000 - published) / 86_400_000)
        if days >= 30:
            parts.add("days_on_market", f"oferta na rynku {int(days)} dni", 6.0)
        elif days >= 7:
            parts.add("days_on_market", f"oferta na rynku {int(days)} dni", 3.0)
        elif days <= 1:
            parts.add("fresh", "oferta dodana dziś", 3.0)
    snapshot = history.get(int(row["id"]))
    if snapshot and snapshot.get("drops", 0) > 0:
        peak = float(snapshot.get("peak") or 0)
        drop = (peak - price) / peak if price is not None and peak > 0 else 0.0
        parts.add(
            "price_cut",
            f"obniżka ceny{(' o ' + str(round(drop * 100)) + '%') if drop > 0 else ''}",
            6.0 + min(4.0, drop * 40),
        )
    gross_yield = _rent_yield(row, rent_ppm)
    if gross_yield is not None and gross_yield >= 5:
        points = 10.0 if gross_yield >= 7 else 6.0 if gross_yield >= 6 else 4.0
        parts.add("rental_yield", f"szacowany gross yield {gross_yield:.1f}%", points)


def _nature_text_signal(
    parts: ScoreParts,
    text: str,
    geo: GeoSignals,
    keywords: dict[str, list[Any]],
    cap: float = 15.0,
) -> None:
    labels = (
        (
            "forest",
            "nature_forest",
            "w opisie: las / Puszcza",
            min(7.0, geo.forest_points * 0.4 + 7.0),
        ),
        (
            "water",
            "nature_water",
            "w opisie: woda / jezioro",
            min(7.0, geo.water_points * 0.4 + 7.0),
        ),
        ("mountain", "nature_mountains", "w opisie: góry / region górski", 6.0),
    )
    found = False
    for kind, key, label, points in labels:
        if _has(text, keywords[key]):
            parts.add(f"nature_text_{kind}", label, points)
            found = True
    if found and parts.profile in {"dom_airbnb", "dzialka_airbnb"}:
        total = sum(
            reason.points for reason in parts.reasons if reason.code.startswith("nature_text_")
        )
        if total > cap:
            text_reasons = [
                reason for reason in parts.reasons if reason.code.startswith("nature_text_")
            ]
            scale = cap / total
            delta = 0.0
            for reason in text_reasons:
                old = reason.points
                reason.points = max(0.0, round(old * scale, 1))
                delta += reason.points - old
            parts.points += delta


def _geo_nature_signal(
    parts: ScoreParts, geo: GeoSignals, config: dict[str, Any], *, plots: bool = False
) -> None:
    if geo.forest_points:
        parts.add("forest", f"las {geo.forest_km:.2f} km", geo.forest_points)
    if geo.water_points:
        parts.add("water", f"woda {geo.water_km:.2f} km", geo.water_points)
    if geo.mountain:
        parts.add("mountains", f"region górski: {geo.mountain}", geo.mountain_points)
    if geo.tourist:
        parts.add("tourist_area", "okolica turystyczna", 7.0)
    if geo.elevation_m is not None and geo.elevation_m >= 400:
        points = 10.0 if geo.elevation_m >= 800 else 6.0
        parts.add("elevation", f"wysokość {geo.elevation_m:.0f} m n.p.m.", points)
    if (
        plots
        and geo.forest_km is None
        and geo.water_km is None
        and not geo.mountain
        and geo.elevation_m is None
    ):
        # No nature cache/geo data: neutral, rather than treating it as no nature.
        return


def _llm_feature_signal(parts: ScoreParts, row: sqlite3.Row) -> None:
    features = _extra_scoring_features(row)
    if not features:
        return
    insulation = features.get("insulation")
    windows = features.get("new_windows")
    charm = features.get("charm")
    airbnb = features.get("airbnb_potential")
    building_plot = features.get("building_plot")
    if parts.profile == "kamienica":
        if insulation is True and not any(reason.code == "insulation" for reason in parts.reasons):
            parts.add("llm_insulation", "AI: potwierdzone ocieplenie", 8.0)
        if windows is True and not any(reason.code == "windows_new" for reason in parts.reasons):
            parts.add("llm_windows", "AI: potwierdzone nowe okna", 6.0)
        if charm is True:
            parts.add("llm_charm", "AI: wyczuwalny klimat / urok", 6.0)
    if parts.profile == "dom_airbnb" and airbnb is True:
        parts.add("llm_airbnb", "AI: dobry potencjał na klimatyczne Airbnb", 8.0)
    if parts.profile in {"dzialka_kolej", "dzialka_airbnb"} and building_plot is True:
        parts.add("llm_building_plot", "AI: potwierdzona możliwość zabudowy", 8.0)


def _profile_kamienica(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoreParts:
    parts = ScoreParts("kamienica", 10.0)
    parts.add("fit", "mieszkanie / lokal", 10.0)
    building = _building_type(row)
    year = _number(row, "build_year")
    if building == "tenement":
        parts.add("tenement", "kamienica / budynek zabytkowy", 25.0)
    if building == "apartment_building":
        parts.add("apartment_building", "budynek mieszkalny, nie wielka płyta", 8.0)
    if _material(row) == "brick":
        parts.add("brick", "ceglana zabudowa", 8.0)
    if _has(text, config["keywords"]["tenement"]):
        parts.add("tenement_text", "w opisie: kamienica", 10.0)
    if year is not None:
        if year < 1939:
            parts.add("old_building", f"przedwojenny budynek ({int(year)})", 12.0)
        elif year < 1970:
            parts.add("old_building", f"starszy budynek ({int(year)})", 6.0)
        else:
            parts.add("newer_building", "budynek po 1970 r.", -4.0)
    if _has(text, config["keywords"]["charm"]):
        parts.add("charm", "oryginalne detale / klimat / wysokość", 12.0)
    if _heating(row) == "district":
        parts.add("district_heating", "ogrzewanie miejskie", 20.0)
    if _has(text, config["keywords"]["insulation"]):
        parts.add("insulation", "ocieplenie / termomodernizacja", 15.0)
    windows = _windows(row)
    if windows in {"new", "partially_new"}:
        parts.add("windows_new", "nowa / wymieniona stolarka okienna", 12.0)
    elif _has(text, config["keywords"]["new_windows"]):
        parts.add("windows_new", "w opisie: nowe / wymienione okna", 12.0)
    elif windows == "plastic":
        parts.add("windows_plastic", "plastikowa stolarka", 4.0)
    ceiling = _number(row, "ceiling_height_m")
    if ceiling is not None:
        if ceiling >= 3.0:
            parts.add("ceiling", f"wysokie sufity ({ceiling:g} m)", 8.0)
        elif ceiling >= 2.8:
            parts.add("ceiling", f"sufity {ceiling:g} m", 4.0)
    if row["balcony"] or row["terrace"]:
        parts.add("outdoor", "balkon / taras", 3.0)
    _price_signal(parts, row, config, prices)
    _comparable_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    _llm_feature_signal(parts, row)
    if _material(row) == "prefabricated_concrete" or _has(text, config["keywords"]["prefab"]):
        parts.add(
            "prefab_penalty",
            "wielka płyta / budownictwo systemowe",
            -float(config["prefab"]["penalty"]),
        )
    elif building == "block":
        parts.add("block_penalty", "blok, nie kamienica", -float(config["prefab"]["block_penalty"]))
    return parts


def _profile_new_flat(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoreParts:
    parts = ScoreParts("nowe_mieszkanie", 5.0)
    parts.add("fit", "mieszkanie", 5.0)
    year = _number(row, "build_year")
    condition = _text(row, "condition")
    market = _text(row, "market")
    if year is not None:
        if year >= 2010:
            parts.add("new_building", f"nowy budynek ({int(year)})", 15.0)
        elif year >= 1990:
            parts.add("new_building", f"budynek z ostatnich dekad ({int(year)})", 8.0)
        elif year < 1939:
            parts.add("old_building_penalty", "przedwojenny budynek, nie nowy standard", -8.0)
    if condition in {
        "developer",
        "to_finish",
        "move_in_ready",
        "very_high_standard",
        "high_standard",
    }:
        parts.add("condition", "wysoki standard / gotowe do zamieszkania", 8.0)
    if market == "primary":
        parts.add("primary_market", "rynek pierwotny", 6.0)
    walkability = geo.walkability
    parts.add(
        "walkability",
        f"użyteczność okolicy: {walkability:.0f}/100"
        if walkability is not None
        else "dane okolicy niedostępne",
        (walkability or 10.0) / 5.0,
    )
    if row["parking"] and row["parking"] != "none":
        parts.add("parking", "parking", 8.0)
    if row["elevator"]:
        parts.add("elevator", "winda", 4.0)
    if row["balcony"] or row["terrace"]:
        parts.add("outdoor", "balkon / taras", 4.0)
    if row["air_conditioning"]:
        parts.add("air_conditioning", "klimatyzacja", 3.0)
    _price_signal(parts, row, config, prices)
    _comparable_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    if _material(row) == "prefabricated_concrete" or _has(text, config["keywords"]["prefab"]):
        parts.add(
            "prefab_penalty",
            "wielka płyta / budownictwo systemowe",
            -float(config["prefab"]["penalty"]),
        )
    elif _building_type(row) == "block":
        parts.add(
            "block_penalty",
            "blok, nie nowoczesny budynek",
            -float(config["prefab"]["block_penalty"]),
        )
    return parts


def _profile_plot_rail(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
) -> ScoreParts:
    parts = ScoreParts("dzialka_kolej", 0.0)
    building = _has(text, config["keywords"]["plot_building"])
    negative = _has(text, config["keywords"]["plot_negative"])
    if building:
        parts.add("building_plot", "działka budowlana / MPZP / WZ", 30.0)
    elif not negative:
        parts.add("plot_unknown", "przeznaczenie do sprawdzenia", 10.0)
    if negative:
        parts.add("non_building_plot", "działka rolna / leśna / rekreacyjna", -30.0)
    if geo.station_gate:
        parts.add(
            "station",
            f"{geo.station_name} — {geo.station_km:.2f} km",
            float(config["rail"]["gate_points"]),
        )
        parts.add(
            "station_destinations",
            "kursy do " + ", ".join(geo.station_destinations),
            min(float(config["rail"]["frequency_points"]), geo.weekly_trips / 10.0),
        )
    else:
        parts.add(
            "station_gate_missing",
            f"brak stacji z kursami do celu w promieniu {config['rail']['gate_km']} km",
            -15.0,
        )
    if geo.station_points and not geo.station_gate:
        parts.add(
            "station_near",
            f"stacja {geo.station_km:.2f} km" if geo.station_km is not None else "stacja w pobliżu",
            geo.station_points,
        )
    if geo.planned_points:
        parts.add("planned_rail", f"planowana inwestycja: {geo.planned_label}", geo.planned_points)
    if row["fenced"] or _has(text, ["media ", "prąd ", "woda ", "gaz "]):
        parts.add("utilities", "media / ogrodzenie", 5.0)
    _price_signal(parts, row, config, prices)
    _comparable_signal(parts, row, config, prices)
    _llm_feature_signal(parts, row)
    return parts


def _profile_house_airbnb(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoreParts:
    parts = ScoreParts("dom_airbnb", 10.0)
    parts.add("fit", "dom / zabudowa", 10.0)
    year = _number(row, "build_year")
    if year is not None:
        if year < 1939:
            parts.add("old_house", f"przedwojenny dom ({int(year)})", 22.0)
        elif year < 1970:
            parts.add("old_house", f"starszy dom ({int(year)})", 14.0)
        else:
            parts.add("modern_house", "nowoczesny dom, bez starszego klimatu", -4.0)
    if _material(row) in {"wood", "brick"}:
        parts.add("traditional_material", "drewno / cegła — tradycyjna zabudowa", 10.0)
    if _has(text, config["keywords"]["charm"]):
        parts.add("charm", "klimat / charakter / zabytkowe detale", 15.0)
    if _has(text, config["keywords"]["old_house"]):
        parts.add("old_house_text", "stary / drewniany / do odnowienia dom", 10.0)
    if _has(text, config["keywords"]["airbnb"]):
        parts.add("airbnb", "potencjał noclegowy / inwestycyjny", 10.0)
    _geo_nature_signal(parts, geo, config)
    _nature_text_signal(parts, text, geo, config["keywords"])
    _price_signal(parts, row, config, prices)
    _comparable_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    _llm_feature_signal(parts, row)
    return parts


def _profile_plot_airbnb(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
) -> ScoreParts:
    parts = ScoreParts("dzialka_airbnb", 5.0)
    parts.add("fit", "działka / teren", 5.0)
    building = _has(text, config["keywords"]["plot_building"])
    negative = _has(text, config["keywords"]["plot_negative"])
    if building:
        parts.add("building_plot", "możliwość zabudowy / MPZP / WZ", 20.0)
    elif not negative:
        parts.add("plot_unknown", "przeznaczenie do sprawdzenia", 6.0)
    if negative:
        parts.add("non_building_plot", "działka rolna / leśna", -20.0)
    _geo_nature_signal(parts, geo, config, plots=True)
    _nature_text_signal(parts, text, geo, config["keywords"])
    if _has(text, config["keywords"]["airbnb"]):
        parts.add("airbnb", "potencjał inwestycyjny / turystyczny", 8.0)
    _price_signal(parts, row, config, prices)
    _comparable_signal(parts, row, config, prices)
    _llm_feature_signal(parts, row)
    return parts


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------


def load_scoring_config(root: Path | None = None) -> dict[str, Any]:
    base = root or repo_root()
    return json.loads((base / "scraper" / "config" / "scoring.json").read_text(encoding="utf-8"))


def _reason_json(parts: ScoreParts, score: int) -> list[dict[str, Any]]:
    if score <= 0:
        return [{"code": "low_fit", "label": "niska zgodność z preferencjami", "points": 0}]
    positive = sorted(
        (reason for reason in parts.reasons if reason.points > 0),
        key=lambda item: item.points,
        reverse=True,
    )
    negative = sorted(
        (reason for reason in parts.reasons if reason.points < 0), key=lambda item: item.points
    )
    selected = positive[:6]
    selected_codes = {reason.code for reason in selected}
    # Value evidence is often a smaller contribution, but must remain visible
    # even when six stronger fit signals fill the normal explanation budget.
    priority_codes = {
        "price_quantile",
        "comparable_discount",
        "auction_discount",
        "price_cut",
        "rental_yield",
        "planned_rail",
    }
    selected.extend(
        reason
        for reason in positive
        if reason.code in priority_codes and reason.code not in selected_codes
    )
    selected.extend(negative[:2])
    return [
        {"code": reason.code, "label": reason.label, "points": reason.points}
        for reason in selected[:10]
    ]


def score_row(
    row: sqlite3.Row,
    config: dict[str, Any],
    geo: GeoContext,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoredListing:
    text = _description(row)
    property_type = _text(row, "property_type")
    if property_type not in {"flat", "house", "plot"}:
        return ScoredListing(int(row["id"]), 0, "other", {}, [])
    signals = _geo_signals(row, config, geo)
    profiles: list[ScoreParts] = []
    if property_type == "flat":
        profiles = [
            _profile_kamienica(row, text, config, signals, prices, history, rent_ppm),
            _profile_new_flat(row, text, config, signals, prices, history, rent_ppm),
        ]
    elif property_type == "house":
        profiles = [_profile_house_airbnb(row, text, config, signals, prices, history, rent_ppm)]
    elif property_type == "plot":
        profiles = [
            _profile_plot_rail(row, text, config, signals, prices),
            _profile_plot_airbnb(row, text, config, signals, prices),
        ]
    if not profiles:
        return ScoredListing(int(row["id"]), 0, "other", {}, [])

    scored: list[tuple[ScoreParts, int]] = []
    for parts in profiles:
        if parts.profile == "dzialka_kolej" and not signals.station_gate:
            score = parts.finish(multiplier=0.45)
        elif parts.profile in {"kamienica", "nowe_mieszkanie"}:
            cap = None
            if any(reason.code == "prefab_penalty" for reason in parts.reasons):
                cap = float(config["prefab"]["cap"])
            score = parts.finish(cap=cap)
        else:
            score = parts.finish()
        scored.append((parts, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    winner, score = scored[0]
    scores = {parts.profile: value for parts, value in scored}
    return ScoredListing(int(row["id"]), score, winner.profile, scores, _reason_json(winner, score))


def score_database(
    db: Any,
    *,
    source_id: str | None = None,
    limit: int | None = None,
    pending_only: bool = False,
    dry_run: bool = False,
    config: dict[str, Any] | None = None,
    geo: GeoContext | None = None,
) -> dict[str, Any]:
    """Score all (or a subset of) stored listings and persist the results."""
    config = config or load_scoring_config()
    rows = list(db.iter_score_rows(source_id=source_id, limit=limit, pending_only=pending_only))
    if not rows:
        return {
            "processed": 0,
            "updated": 0,
            "dry_run": dry_run,
            "profiles": {},
        }
    geo = geo or GeoContext.load()
    # Price/yield baselines are global. A pending/source subset must not train
    # (or rebuild) them on a much smaller, biased slice of the database.
    price_rows = list(db.iter_score_rows()) if pending_only or source_id is not None else rows
    prices = PriceModel(price_rows)
    history = db.price_history_stats()
    rent_ppm = _rent_price_samples(price_rows)
    updates: list[tuple[Any, ...]] = []
    distributions: dict[str, list[int]] = defaultdict(list)
    now = int(time.time() * 1000)
    for row in rows:
        result = score_row(row, config, geo, prices, history, rent_ppm)
        distributions[result.profile].append(result.score)
        updates.append(
            (
                result.score,
                result.profile,
                json.dumps(result.scores, ensure_ascii=False, sort_keys=True),
                json.dumps(result.reasons, ensure_ascii=False, sort_keys=True),
                now,
                str(config["version"]),
                result.row_id,
            )
        )
    if not dry_run:
        db.update_scores(updates)
    profiles: dict[str, dict[str, Any]] = {}
    for profile, values in sorted(distributions.items()):
        profiles[profile] = {
            "count": len(values),
            "average": round(statistics.mean(values), 1),
            "top": sorted(values, reverse=True)[:5],
        }
    return {
        "processed": len(rows),
        "updated": 0 if dry_run else len(updates),
        "dry_run": dry_run,
        "profiles": profiles,
    }


def _rent_price_samples(rows: list[sqlite3.Row]) -> dict[tuple[str, str], list[float]]:
    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        if _text(row, "offer_type") != "rent" or _text(row, "property_type") not in {
            "flat",
            "house",
        }:
            continue
        price, area = _price(row), _area(row)
        if price is None or area is None:
            continue
        samples[(_text(row, "property_type"), normalize_text(_city(row)))].append(price / area)
        samples[(_text(row, "property_type"), normalize_text(_text(row, "county")))].append(
            price / area
        )
        samples[(_text(row, "property_type"), normalize_text(_text(row, "voivodeship")))].append(
            price / area
        )
    return samples

"""Deterministic signal extraction for stored listings.

The Python side extracts normalized evidence only. It does not assign profile
weights, choose a winning profile, calculate final scores, or persist the legacy
score columns. The web application owns ranking and score calculation from the
``listing.score_signals`` JSON payload.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import re
import sqlite3
import time
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.geodata import GeoContext, haversine_km
from plaszczaki_scraper.normalize import normalize_text

logger = logging.getLogger(__name__)

SIGNALS_VERSION = "signals-v2"

PROFILE_NAMES = (
    "kamienica",
    "nowe_mieszkanie",
    "dom_airbnb",
    "dom_kolej",
    "dzialka_kolej",
    "dzialka_airbnb",
)

SIGNAL_CODES = (
    "price_quantile",
    "comparable_discount",
    "walkability",
    "commute",
    "station",
    "station_frequency",
    "planned_rail",
    "station_gate",
    "station_data_available",
    "rail_station",
    "tram_stop",
    "forest",
    "water",
    "lake",
    "river",
    "mountains",
    "tourist_area",
    "elevation",
    "building_character",
    "building_age",
    "charm",
    "district_heating",
    "insulation",
    "windows",
    "ceiling",
    "large_windows",
    "outdoor",
    "condition",
    "primary_market",
    "parking",
    "elevator",
    "air_conditioning",
    "internet",
    "old_house",
    "traditional_material",
    "airbnb",
    "house_condition",
    "building_plot",
    "utilities",
    "fenced",
    "auction_discount",
    "market_time",
    "price_cut",
    "rental_yield",
)

PENALTY_CODES = (
    "prefab_penalty",
    "newer_building",
    "old_building_penalty",
    "modern_house",
    "no_charm",
    "non_building_plot",
    "build_unconfirmed",
    "overpriced",
)

Factor = float | None


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
    raw = _text(row, "heating")
    value = normalize_text(raw)
    return {"co miejskie": "district", "miejskie": "district", "siec miejska": "district"}.get(
        value, raw or value
    )


def _windows(row: sqlite3.Row) -> str:
    raw = _text(row, "windows_type")
    value = normalize_text(raw)
    return {
        "nowe wymienione": "new",
        "czesciowo wymienione": "partially_new",
        "nowe": "new",
    }.get(value, raw or value)


def _material(row: sqlite3.Row) -> str:
    raw = _text(row, "building_material")
    value = normalize_text(raw)
    return {
        "cegla": "brick",
        "wielka plyta": "prefabricated_concrete",
        "prefabrykowan": "prefabricated_concrete",
    }.get(value, raw or value)


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


def _extra_features(row: sqlite3.Row) -> dict[str, Any]:
    extra = _json(row, "extra") or {}
    if not isinstance(extra, dict):
        return {}
    features = extra.get("scoring_features")
    return features if isinstance(features, dict) else {}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _factor(value: float) -> float:
    return round(_clamp(value), 6)


def _boolean_factor(value: Any) -> Factor:
    if value is None or value == "":
        return None
    return 1.0 if bool(value) else 0.0


def _outdoor_factor(*values: Any) -> Factor:
    if any(value is True or value == 1 for value in values if value is not None):
        return 1.0
    if any(value is None or value == "" for value in values):
        return None
    return 0.0


def _distance_factor(distance: float, full: float, zero: float) -> float:
    if distance <= full:
        return 1.0
    if zero <= full or distance >= zero:
        return 0.0
    return _factor(1.0 - (distance - full) / (zero - full))


def _ramp_factor(value: float, zero: float, full: float) -> float:
    if value <= zero:
        return 0.0
    if full <= zero or value >= full:
        return 1.0
    return _factor((value - zero) / (full - zero))


# ---------------------------------------------------------------------------
# Baseline and comparable-sales pricing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Comparable:
    key: str
    lat: float
    lng: float
    area: float
    ppm: float
    building_type: str
    market: str | None = None
    rooms: int | None = None


@dataclass(frozen=True, slots=True)
class RcnComparable:
    key: str
    lat: float
    lng: float
    area: float
    ppm: float
    market: str | None
    rooms: int | None
    document_date: date


@dataclass(frozen=True, slots=True)
class ComparableSummary:
    median: float
    p25: float
    p75: float
    count: int
    radius_km: float
    confidence: str


@dataclass(slots=True)
class PriceBaseline:
    low: float
    median: float
    high: float
    count: int
    level: str


@dataclass(frozen=True, slots=True)
class PriceEvidence:
    listing: ComparableSummary | None
    rcn: ComparableSummary | None
    baseline: PriceBaseline | None
    benchmark: float | None
    benchmark_source: str | None
    discount: float | None
    effective_discount: float | None
    confidence: str | None
    overpriced: bool
    top_eligible: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "listing": _summary_json(self.listing),
            "rcn": _summary_json(self.rcn),
            "baseline": (
                {
                    "low": self.baseline.low,
                    "median": self.baseline.median,
                    "high": self.baseline.high,
                    "count": self.baseline.count,
                    "level": self.baseline.level,
                }
                if self.baseline is not None
                else None
            ),
            "benchmark": self.benchmark,
            "benchmarkSource": self.benchmark_source,
            "discount": self.discount,
            "effectiveDiscount": self.effective_discount,
            "confidence": self.confidence,
            "overpriced": self.overpriced,
            "topEligible": self.top_eligible,
        }


def _summary_json(summary: ComparableSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "median": summary.median,
        "p25": summary.p25,
        "p75": summary.p75,
        "count": summary.count,
        "radiusKm": summary.radius_km,
        "confidence": summary.confidence,
    }


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


def _months_ago(months: int) -> str:
    today = date.today()
    year, month_zero = divmod(today.year * 12 + today.month - 1 - months, 12)
    month = month_zero + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _quantile_entries(
    entries: list[tuple[float, str]], fraction: float, skip_index: int | None = None
) -> float:
    count = len(entries) - (1 if skip_index is not None else 0)
    if count <= 0:
        return 0.0
    position = (count - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)

    def value_at(index: int) -> float:
        if skip_index is not None and index >= skip_index:
            index += 1
        return entries[index][0]

    if low == high:
        return value_at(low)
    weight = position - low
    return value_at(low) * (1.0 - weight) + value_at(high) * weight


def _weighted_median(candidates: list[tuple[float, float, float]]) -> float | None:
    weighted = sorted(((ppm, weight) for _, ppm, weight in candidates), key=lambda item: item[0])
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return None
    cumulative = 0.0
    for ppm, weight in weighted:
        cumulative += weight
        if cumulative >= total / 2:
            return ppm
    return weighted[-1][0]


def _remove_price_outliers(
    candidates: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    if len(candidates) < 8:
        return candidates
    values = [ppm for _, ppm, _ in candidates]
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    spread = q3 - q1
    if spread <= 0:
        return candidates
    lower = q1 - spread * 1.5
    upper = q3 + spread * 1.5
    return [candidate for candidate in candidates if lower <= candidate[1] <= upper]


def _market(row: sqlite3.Row) -> str | None:
    value = _text(row, "market")
    return value if value in {"primary", "secondary"} else None


def _rooms(row: sqlite3.Row, column: str = "rooms") -> int | None:
    value = _number(row, column)
    return round(value) if value is not None else None


def _area_ratio(property_type: str, config: dict[str, Any]) -> tuple[float, float]:
    key = "plot_area_ratio" if property_type == "plot" else "area_ratio"
    low, high = config.get(key, [0.7, 1.4])
    return float(low), float(high)


def _confidence(count: int, radius_km: float, minimum_count: int) -> str:
    if count >= 10 and radius_km <= 2.5:
        return "high"
    return "medium" if count >= minimum_count else "low"


class PriceModel:
    """Hierarchical baselines plus matched nearby asks and RCN transactions."""

    COMP_CELL = 0.02  # ~2.2 km; avoids scanning whole city blocks for every offer.
    CONFIDENCE_RANK: ClassVar[dict[str, int]] = {"low": 1, "medium": 2, "high": 3}

    def __init__(self, rows: list[sqlite3.Row]) -> None:
        self.groups: dict[tuple[str, str, str], list[tuple[float, str]]] = defaultdict(list)
        self.comps: dict[str, list[Comparable]] = defaultdict(list)
        self.comp_index: dict[str, dict[tuple[int, int], list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.rcn_comps: dict[str, list[RcnComparable]] = defaultdict(list)
        self.rcn_index: dict[str, dict[tuple[int, int], list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._evidence_cache: dict[str, PriceEvidence] = {}
        self._today = date.today()
        seen: set[str] = set()
        for row in rows:
            value = _ppm(row)
            property_type = _text(row, "property_type")
            if (
                not _sale(row)
                or property_type not in {"flat", "house", "plot"}
                or value is None
                or value <= 0
            ):
                continue
            key = row["canonical_key"] or f"{row['source_id']}:{row['external_id']}"
            dedupe_key = f"{property_type}:{key}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            city = normalize_text(_city(row))
            district = normalize_text(_text(row, "district"))
            county = normalize_text(_text(row, "county"))
            voivodeship = normalize_text(_text(row, "voivodeship"))
            group_keys = {(property_type, "", "")}
            if voivodeship:
                group_keys.add((property_type, voivodeship, ""))
            if county:
                group_keys.add((property_type, county, ""))
            if city:
                group_keys.add((property_type, city, ""))
                if district and district != city:
                    group_keys.add((property_type, city, district))
            for group in group_keys:
                self.groups[group].append((value, dedupe_key))
            lat, lng, area = _number(row, "lat"), _number(row, "lng"), _area(row)
            if lat is not None and lng is not None and area is not None:
                index = len(self.comps[property_type])
                self.comps[property_type].append(
                    Comparable(
                        key,
                        lat,
                        lng,
                        area,
                        value,
                        _building_type(row),
                        _market(row),
                        _rooms(row),
                    )
                )
                cell = (int(lat // self.COMP_CELL), int(lng // self.COMP_CELL))
                self.comp_index[property_type][cell].append(index)

        self.group_entries = {key: sorted(values) for key, values in self.groups.items()}
        self.group_positions = {
            key: {member: index for index, (_, member) in enumerate(values)}
            for key, values in self.group_entries.items()
        }
        self.quantiles: dict[tuple[str, str, str], tuple[float, float, float, int]] = {}
        for key, values in self.group_entries.items():
            plain = [value for value, _ in values]
            self.quantiles[key] = (
                _quantile(plain, 0.25),
                _quantile(plain, 0.5),
                _quantile(plain, 0.75),
                len(plain),
            )

    def load_rcn(self, rows: Iterable[sqlite3.Row]) -> int:
        loaded = 0
        for row in rows:
            kind = _text(row, "kind")
            lat, lng = _number(row, "lat"), _number(row, "lng")
            area, ppm = _number(row, "area"), _number(row, "ppm")
            try:
                document_date = date.fromisoformat(_text(row, "document_date"))
            except ValueError:
                continue
            if (
                kind not in {"flat", "building", "plot"}
                or lat is None
                or lng is None
                or area is None
                or area <= 0
                or ppm is None
                or ppm <= 0
            ):
                continue
            index = len(self.rcn_comps[kind])
            self.rcn_comps[kind].append(
                RcnComparable(
                    _text(row, "key"),
                    lat,
                    lng,
                    area,
                    ppm,
                    _market(row),
                    _rooms(row),
                    document_date,
                )
            )
            cell = (int(lat // self.COMP_CELL), int(lng // self.COMP_CELL))
            self.rcn_index[kind][cell].append(index)
            loaded += 1
        self._evidence_cache.clear()
        return loaded

    def baseline(self, row: sqlite3.Row) -> PriceBaseline | None:
        property_type = _text(row, "property_type")
        city = normalize_text(_city(row))
        district = normalize_text(_text(row, "district"))
        county = normalize_text(_text(row, "county"))
        voivodeship = normalize_text(_text(row, "voivodeship"))
        source_key = row["canonical_key"] or f"{row['source_id']}:{row['external_id']}"
        member = f"{property_type}:{source_key}"
        candidates: list[tuple[tuple[str, str, str], int, str]] = []
        if city and district and district != city:
            candidates.append(((property_type, city, district), 8, "dzielnica"))
        if city:
            candidates.append(((property_type, city, ""), 15, "miasto"))
        if county:
            candidates.append(((property_type, county, ""), 12, "powiat"))
        if voivodeship:
            candidates.append(((property_type, voivodeship, ""), 25, "województwo"))
        candidates.append(((property_type, "", ""), 20, "kraj"))
        for key, minimum, level in candidates:
            entries = self.group_entries.get(key)
            if entries is None:
                continue
            skip = self.group_positions[key].get(member)
            count = len(entries) - (1 if skip is not None else 0)
            if count < minimum:
                continue
            return PriceBaseline(
                _quantile_entries(entries, 0.25, skip),
                _quantile_entries(entries, 0.5, skip),
                _quantile_entries(entries, 0.75, skip),
                count,
                level,
            )
        return None

    def _listing_candidates(
        self, row: sqlite3.Row, radius_km: float, config: dict[str, Any]
    ) -> list[tuple[float, float, float]]:
        property_type = _text(row, "property_type")
        lat, lng, area = _number(row, "lat"), _number(row, "lng"), _area(row)
        if lat is None or lng is None or area is None:
            return []
        ratio_low, ratio_high = _area_ratio(property_type, config)
        candidates: list[tuple[float, float, float]] = []
        cx, cy = int(lat // self.COMP_CELL), int(lng // self.COMP_CELL)
        cells = self.comp_index[property_type]
        source_key = row["canonical_key"] or f"{row['source_id']}:{row['external_id']}"
        own_type = _building_type(row)
        own_market = _market(row)
        own_rooms = _rooms(row)
        span = max(1, math.ceil(radius_km / (self.COMP_CELL * 111.0)))
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                for index in cells.get((cx + dx, cy + dy), []):
                    candidate = self.comps[property_type][index]
                    if candidate.key == source_key:
                        continue
                    if own_market is not None and candidate.market != own_market:
                        continue
                    ratio = candidate.area / area
                    if not (ratio_low <= ratio <= ratio_high):
                        continue
                    distance = haversine_km(lat, lng, candidate.lat, candidate.lng)
                    if distance > radius_km:
                        continue
                    similarity = 1.0 - min(0.25, abs(ratio - 1.0) * 0.5)
                    if own_type and candidate.building_type and own_type != candidate.building_type:
                        similarity *= 0.75
                    if property_type == "flat" and own_rooms is not None:
                        if candidate.rooms is None:
                            similarity *= 0.85
                        elif abs(candidate.rooms - own_rooms) > 1:
                            continue
                    weight = (1.0 / (1.0 + distance)) * similarity
                    candidates.append((distance, candidate.ppm, weight))
        return candidates

    def _rcn_candidates(
        self, row: sqlite3.Row, radius_km: float, config: dict[str, Any]
    ) -> list[tuple[float, float, float]]:
        property_type = _text(row, "property_type")
        kind = {"flat": "flat", "house": "building", "plot": "plot"}.get(property_type)
        lat, lng, area = _number(row, "lat"), _number(row, "lng"), _area(row)
        if kind is None or lat is None or lng is None or area is None:
            return []
        ratio_low, ratio_high = _area_ratio(property_type, config)
        candidates: list[tuple[float, float, float]] = []
        cx, cy = int(lat // self.COMP_CELL), int(lng // self.COMP_CELL)
        cells = self.rcn_index[kind]
        own_market = _market(row)
        own_rooms = _rooms(row)
        span = max(1, math.ceil(radius_km / (self.COMP_CELL * 111.0)))
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                for index in cells.get((cx + dx, cy + dy), []):
                    candidate = self.rcn_comps[kind][index]
                    if own_market is not None and candidate.market != own_market:
                        continue
                    ratio = candidate.area / area
                    if not (ratio_low <= ratio <= ratio_high):
                        continue
                    distance = haversine_km(lat, lng, candidate.lat, candidate.lng)
                    if distance > radius_km:
                        continue
                    similarity = 1.0 - min(0.25, abs(ratio - 1.0) * 0.5)
                    if kind == "flat" and own_rooms is not None:
                        if candidate.rooms is None:
                            similarity *= 0.85
                        elif abs(candidate.rooms - own_rooms) > int(config.get("rooms_delta", 1)):
                            continue
                    age_days = max(0, (self._today - candidate.document_date).days)
                    recency = math.exp(-age_days / 730.0)
                    weight = (1.0 / (1.0 + distance)) * similarity * recency
                    candidates.append((distance, candidate.ppm, weight))
        return candidates

    @staticmethod
    def _summary(
        candidates: list[tuple[float, float, float]],
        *,
        radius_km: float,
        minimum_count: int,
        maximum_count: int,
    ) -> ComparableSummary | None:
        candidates = _remove_price_outliers(candidates)
        if len(candidates) < minimum_count:
            return None
        selected = sorted(candidates, key=lambda item: item[2], reverse=True)[:maximum_count]
        median = _weighted_median(selected)
        if median is None:
            return None
        values = [ppm for _, ppm, _ in selected]
        return ComparableSummary(
            median,
            _quantile(values, 0.25),
            _quantile(values, 0.75),
            len(selected),
            radius_km,
            _confidence(len(selected), radius_km, minimum_count),
        )

    def listing_summary(self, row: sqlite3.Row, config: dict[str, Any]) -> ComparableSummary | None:
        minimum_count = int(config.get("min_count", 5))
        maximum_count = int(config.get("max_count", 30))
        radii = config.get("radii_km", [1.0, float(config.get("radius_km", 2.5)), 5.0])
        for radius in radii:
            summary = self._summary(
                self._listing_candidates(row, float(radius), config),
                radius_km=float(radius),
                minimum_count=minimum_count,
                maximum_count=maximum_count,
            )
            if summary is not None:
                return summary
        return None

    def rcn_summary(self, row: sqlite3.Row, config: dict[str, Any]) -> ComparableSummary | None:
        kind = {"flat": "flat", "house": "building", "plot": "plot"}.get(
            _text(row, "property_type")
        )
        if kind is None:
            return None
        minimum_count = int(config.get("min_count", 5))
        maximum_count = int(config.get("max_count", 30))
        radii = config.get("radii_km", {}).get(kind, [1.0, 2.5, 5.0])
        for radius in radii:
            summary = self._summary(
                self._rcn_candidates(row, float(radius), config),
                radius_km=float(radius),
                minimum_count=minimum_count,
                maximum_count=maximum_count,
            )
            if summary is not None:
                return summary
        return None

    def comparable(self, row: sqlite3.Row, config: dict[str, Any]) -> tuple[float | None, int]:
        candidates = self._listing_candidates(row, float(config.get("radius_km", 2.5)), config)
        minimum_count = int(config.get("min_count", 5))
        if len(candidates) < minimum_count:
            return None, len(candidates)
        selected = sorted(candidates, key=lambda item: item[2], reverse=True)[
            : int(config.get("max_count", 30))
        ]
        return _weighted_median(selected), len(selected)

    def evidence(
        self,
        row: sqlite3.Row,
        comps_config: dict[str, Any],
        rcn_config: dict[str, Any],
        value_config: dict[str, Any],
    ) -> PriceEvidence:
        source_key = row["canonical_key"] or f"{row['source_id']}:{row['external_id']}"
        cache_key = f"{_text(row, 'property_type')}:{source_key}"
        cached = self._evidence_cache.get(cache_key)
        if cached is not None:
            return cached
        listing = self.listing_summary(row, comps_config)
        rcn = self.rcn_summary(row, rcn_config)
        baseline = self.baseline(row)
        value = _ppm(row)
        listing_discount = (
            (listing.median - value) / listing.median
            if listing is not None and value is not None
            else None
        )
        rcn_discount = (
            (rcn.median - value) / rcn.median if rcn is not None and value is not None else None
        )
        discounts = [item for item in (listing_discount, rcn_discount) if item is not None]
        effective_discount = min(discounts) if discounts else None
        if rcn is not None:
            benchmark, source = rcn.median, "rcn"
        elif listing is not None:
            benchmark, source = listing.median, "listings"
        elif baseline is not None:
            benchmark, source = baseline.median, "baseline"
        else:
            benchmark, source = None, None
        discount = (
            (benchmark - value) / benchmark if benchmark is not None and value is not None else None
        )
        confidences = [item.confidence for item in (rcn, listing) if item is not None]
        confidence = (
            min(confidences, key=lambda item: self.CONFIDENCE_RANK[item])
            if len(confidences) == 2
            else max(confidences, key=lambda item: self.CONFIDENCE_RANK[item])
            if confidences
            else None
        )
        overpriced = effective_discount is not None and effective_discount < -float(
            value_config["overpriced_threshold"]
        )
        top_eligible = (
            listing is not None
            and effective_discount is not None
            and effective_discount >= float(value_config["top_min_discount"])
            and not overpriced
        )
        evidence = PriceEvidence(
            listing,
            rcn,
            baseline,
            benchmark,
            source,
            discount,
            effective_discount,
            confidence,
            overpriced,
            top_eligible,
        )
        self._evidence_cache[cache_key] = evidence
        return evidence


def _price_factor(value: float, baseline: PriceBaseline) -> float:
    if value <= baseline.low:
        return 1.0
    if value <= baseline.median:
        span = max(baseline.median - baseline.low, 1.0)
        return _factor(1.0 - 0.4 * (value - baseline.low) / span)
    if value <= baseline.high:
        span = max(baseline.high - baseline.median, 1.0)
        return _factor(0.6 - 0.5 * (value - baseline.median) / span)
    return 0.0


def _price_quantile_factor(row: sqlite3.Row, evidence: PriceEvidence) -> Factor:
    value = _ppm(row)
    if value is None or evidence.baseline is None:
        return None
    return _price_factor(value, evidence.baseline)


def _comparable_discount_factor(evidence: PriceEvidence, config: dict[str, Any]) -> Factor:
    if evidence.effective_discount is None:
        return None
    zero = float(config["value"]["zero_discount"])
    full = float(config["value"]["full_discount"])
    if full <= zero:
        return 1.0 if evidence.effective_discount >= full else 0.0
    return _factor((evidence.effective_discount - zero) / (full - zero))


def _median_sorted(values: list[float]) -> float:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


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
    monthly_per_m2 = _median_sorted(samples)
    return monthly_per_m2 * area * 12 / price * 100


# ---------------------------------------------------------------------------
# Geo signals
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GeoSignals:
    station_name: str | None = None
    station_km: float | None = None
    station_destinations: tuple[str, ...] = ()
    weekly_trips: float = 0.0
    station_gate: bool | None = None
    station_data_available: bool | None = None
    rail_station_km: float | None = None
    tram_stop_km: float | None = None
    planned_factor: float = 0.0
    planned_label: str | None = None
    forest_km: float | None = None
    lake_km: float | None = None
    river_km: float | None = None
    water_km: float | None = None
    forest_data_available: bool = False
    lake_data_available: bool = False
    river_data_available: bool = False
    water_data_available: bool = False
    mountain: str | None = None
    tourist: bool | None = None
    walkability: float | None = None
    commute_km: float | None = None
    commute_label: str | None = None
    commute_data_available: bool | None = None
    elevation_m: float | None = None


def _geo_signals(row: sqlite3.Row, config: dict[str, Any], geo: GeoContext) -> GeoSignals:
    result = GeoSignals()
    property_type = _text(row, "property_type")
    if property_type not in {"flat", "house", "plot"}:
        return result
    lat, lng = _number(row, "lat"), _number(row, "lng")

    if property_type in {"house", "plot"}:
        result.station_data_available = bool(geo.stations)
        result.forest_data_available = bool(geo.forest)
        result.lake_data_available = bool(geo.lake)
        result.river_data_available = bool(geo.river)
        result.water_data_available = bool(geo.water)

    if property_type in {"flat", "house"}:
        result.commute_data_available = bool(geo.stations) or any(
            poi.get("cat") in {"transit", "rail_station", "tram_stop"} for poi in geo.pois
        )

    if lat is None or lng is None:
        return result

    if property_type in {"house", "plot"}:
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
        elif result.station_data_available:
            result.station_gate = False
        result.planned_factor, result.planned_label = geo.planned_rail_signal(
            lat, lng, config["planned_rail"]
        )

    rail_radius = float(config["rail"]["distance_zero_km"])
    rail_station = geo.nearest_station(lat, lng, radius_km=rail_radius)
    if rail_station is not None:
        result.rail_station_km = round(rail_station[1], 2)
    rail_poi = geo.nearest_poi(lat, lng, rail_radius, categories=("rail_station",))
    if rail_poi is not None and (
        result.rail_station_km is None or rail_poi[1] < result.rail_station_km
    ):
        result.rail_station_km = round(rail_poi[1], 2)

    tram = config["tram"]
    tram_radius = float(tram["distance_zero_km"])
    tram_stop = geo.nearest_poi(lat, lng, tram_radius, categories=("tram_stop",))
    if tram_stop is not None:
        result.tram_stop_km = round(tram_stop[1], 2)

    if property_type in {"house", "plot"}:
        nature = config["nature"]
        for kind in ("forest", "lake", "river"):
            settings = nature[kind]
            distance = geo.nature_distance_km(
                lat, lng, kind, radius_km=float(settings["zero_m"]) / 1000
            )
            setattr(result, f"{kind}_km", round(distance, 2) if distance is not None else None)
        water_distances = [
            distance for distance in (result.lake_km, result.river_km) if distance is not None
        ]
        result.water_km = min(water_distances) if water_distances else None

        for region in config["mountain_regions"]:
            min_lng, min_lat, max_lng, max_lat = region["bbox"]
            if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat:
                result.mountain = region["name"]
                break
        location = normalize_text(f"{_city(row)} {_district(row)} {_text(row, 'commune')}")
        result.tourist = (
            any(normalize_text(city) in location for city in config["tourist_cities"])
            if location
            else None
        )
        result.elevation_m = geo.elevation_m(lat, lng)

    if property_type in {"flat", "house"}:
        if property_type == "flat" and geo.pois:
            result.walkability = geo.walkability(lat, lng)
        commute = config["new_flat"]
        station = geo.nearest_station(lat, lng, radius_km=float(commute["commute_zero_km"]))
        transit = geo.nearest_transit(
            lat,
            lng,
            radius_km=float(commute["commute_zero_km"]),
            categories=("transit", "rail_station", "tram_stop"),
        )
        candidates: list[tuple[float, str]] = []
        if station is not None:
            candidates.append((station[1], f"stacja {station[0].get('name') or 'kolejowa'}"))
        if transit is not None:
            candidates.append((transit[1], "przystanek komunikacji publicznej"))
        if candidates:
            distance, label = min(candidates, key=lambda item: item[0])
            result.commute_km = round(distance, 2)
            result.commute_label = label
    return result


def _nature_factor(
    distance: float | None,
    available: bool | None,
    text: bool,
    settings: dict[str, Any],
) -> Factor:
    geo_factor = (
        _distance_factor(
            distance,
            float(settings["full_m"]) / 1000,
            float(settings["zero_m"]) / 1000,
        )
        if distance is not None
        else 0.0
        if available
        else None
    )
    text_factor = float(settings["text_factor"]) if text else None
    factors = [value for value in (geo_factor, text_factor) if value is not None]
    return max(factors) if factors else None


# ---------------------------------------------------------------------------
# Text and structured signals
# ---------------------------------------------------------------------------


_STRONG_LARGE_WINDOW_PATTERNS = (
    re.compile(r"\bbardzo du(?:ze|zych) ok(?:na|ien)\b"),
    re.compile(r"\bokna od podlogi\b"),
    re.compile(r"\bportfenetr\w*"),
    re.compile(r"\bpanoramiczn\w* okna\b"),
    re.compile(r"\bwysok\w* okna\b"),
)
_PLAIN_LARGE_WINDOW_PATTERN = re.compile(r"\bdu(?:ze|zych) ok(?:na|ien)\b")
_ORDINARY_WINDOW_PATTERNS = (
    re.compile(r"\b(?:standardowe|zwykle|male|niewielkie|waskie) okna\b"),
    re.compile(r"\bokna (?:standardowe|zwykle|male|niewielkie|waskie)\b"),
    re.compile(r"\bnie duze okna\b"),
)


def _large_windows(text: str) -> tuple[bool | None, Factor]:
    if any(pattern.search(text) for pattern in _STRONG_LARGE_WINDOW_PATTERNS):
        return True, 1.0
    if _PLAIN_LARGE_WINDOW_PATTERN.search(text):
        return True, 0.5
    if any(pattern.search(text) for pattern in _ORDINARY_WINDOW_PATTERNS):
        return False, 0.0
    return None, None


_POLISH_FOLD = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
    }
)
_DIMENSION = r"(\d{1,2}(?:[.,]\d+)?)"
_CEILING_PATTERNS = (
    re.compile(
        rf"\bwysokosc\s+sufit(?:ow|y)?\s*(?:wynosi|to|sa)?\s*\(?\s*{_DIMENSION}\s*(m|cm)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bsufity\s+o\s+wysokosci\s*\(?\s*{_DIMENSION}\s*(m|cm)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bwysokie\s+sufity\s*(?:o\s+wysokosci\s*)?\(?\s*{_DIMENSION}\s*(m|cm)?\)?",
        re.IGNORECASE,
    ),
)
_HIGH_CEILING_PATTERN = re.compile(r"\b(?:wysokie sufity|sufity wysokie|wysokich sufitow)\b")


def _fold_dimensions(value: str) -> str:
    translated = value.translate(_POLISH_FOLD)
    decomposed = unicodedata.normalize("NFKD", translated)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _parse_ceiling_height(title: str | None, description: str | None) -> float | None:
    text = _fold_dimensions(f"{title or ''} {description or ''}")
    for pattern in _CEILING_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        value = float(match.group(1).replace(",", "."))
        if match.group(2) == "cm":
            value /= 100
        if 2.0 <= value <= 6.0:
            return round(value, 2)
    return None


def _ceiling(row: sqlite3.Row, text: str) -> tuple[float | None, Factor]:
    height = _number(row, "ceiling_height_m")
    if height is not None and height > 0:
        height = round(height, 2)
    else:
        height = _parse_ceiling_height(_text(row, "title"), _text(row, "description"))
    if height is not None:
        return height, _ramp_factor(height, 2.9, 3.4)
    if _HIGH_CEILING_PATTERN.search(text):
        return None, 0.5
    return None, None


def _feature_factor(features: dict[str, Any], key: str, text_match: bool) -> Factor:
    if text_match or features.get(key) is True:
        return 1.0
    if features.get(key) is False:
        return 0.0
    return None


def _windows_factor(
    row: sqlite3.Row, text: str, features: dict[str, Any], config: dict[str, Any]
) -> Factor:
    windows = _windows(row)
    if windows in {"new", "partially_new"}:
        return 1.0
    if _has(text, config["keywords"]["new_windows"]) or features.get("new_windows") is True:
        return 1.0
    if features.get("new_windows") is False:
        return 0.0
    if windows == "plastic":
        return 0.4
    if windows:
        return 0.0
    return None


def _parking_factor(row: sqlite3.Row) -> Factor:
    parking = normalize_text(_text(row, "parking"))
    if not parking:
        return None
    return 0.0 if parking in {"none", "brak"} else 1.0


def _condition_factor(row: sqlite3.Row, *, house: bool = False) -> Factor:
    raw_condition = _text(row, "condition")
    condition = normalize_text(raw_condition)
    year = _number(row, "build_year")
    if house:
        condition_factor = (
            1.0
            if condition
            in {
                "very_high_standard",
                "very high standard",
                "high_standard",
                "high standard",
                "move_in_ready",
                "move in ready",
            }
            else 0.7
            if condition
            in {"good", "very_good", "very good", "after_renovation", "after renovation"}
            else 0.0
        )
        year_factor = (
            1.0
            if year is not None and year >= 2000
            else 0.75
            if year is not None and year >= 1980
            else 0.45
            if year is not None and year >= 1960
            else 0.2
            if year is not None
            else None
        )
        if year_factor is None and not condition:
            return None
        return max(value for value in (year_factor, condition_factor) if value is not None)
    if not condition:
        return None
    return (
        1.0
        if raw_condition
        in {
            "developer",
            "to_finish",
            "move_in_ready",
            "very_high_standard",
            "high_standard",
        }
        else 0.0
    )


def _building_confirmation(
    row: sqlite3.Row, text: str, config: dict[str, Any]
) -> tuple[bool, bool]:
    features = _extra_features(row)
    confirmed = (
        _has(text, config["keywords"]["plot_building"]) or features.get("building_plot") is True
    )
    negative = (
        _has(text, config["keywords"]["plot_negative"]) or features.get("building_plot") is False
    )
    return confirmed, negative


def _empty_profiles() -> dict[str, dict[str, dict[str, Factor]]]:
    return {
        profile: {
            "signals": dict.fromkeys(SIGNAL_CODES),
            "penalties": dict.fromkeys(PENALTY_CODES),
        }
        for profile in PROFILE_NAMES
    }


def _opportunity_signals(
    row: sqlite3.Row,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
    config: dict[str, Any],
    now_ms: int,
) -> dict[str, Factor]:
    if not _sale(row):
        return {
            "auction_discount": None,
            "market_time": None,
            "price_cut": None,
            "rental_yield": None,
        }
    opportunity = config["opportunity"]
    price, estimate = _price(row), _number(row, "estimate_pln")
    auction_discount: Factor = 0.0
    if _text(row, "offer_type") == "auction":
        if price is None or estimate is None or estimate <= 0:
            auction_discount = None
        else:
            discount = max(0.0, (estimate - price) / estimate)
            factor = min(1.0, discount / float(opportunity["auction_full_discount"]))
            if (_number(row, "auction_round") or 1) >= 2:
                factor = max(float(opportunity["auction_later_round_factor"]), factor)
            auction_discount = _factor(factor)

    published = _number(row, "published_at") or _number(row, "first_seen_at")
    days_on_market: Factor = None
    if published is not None:
        days = max(0.0, (now_ms - published) / 86_400_000)
        factor = (
            1.0
            if days >= float(opportunity["days_full"])
            else 0.5
            if days >= float(opportunity["days_half"])
            else 0.4
            if days <= float(opportunity["days_recent"])
            else 0.0
        )
        days_on_market = factor

    snapshot = history.get(int(row["id"]))
    price_cut: Factor = 0.0
    if snapshot and snapshot.get("drops", 0) > 0:
        peak = float(snapshot.get("peak") or 0)
        drop = (peak - price) / peak if price is not None and peak > 0 else 0.0
        price_cut = _factor(drop / float(opportunity["price_cut_full_drop"]))

    gross_yield = _rent_yield(row, rent_ppm)
    rental_yield = (
        _factor(gross_yield / float(opportunity["rental_yield_full_percent"]))
        if gross_yield is not None
        else None
    )
    return {
        "auction_discount": auction_discount,
        "market_time": days_on_market,
        "price_cut": price_cut,
        "rental_yield": rental_yield,
    }


# ---------------------------------------------------------------------------
# Public extraction API
# ---------------------------------------------------------------------------


def load_signals_config(root: Path | None = None) -> dict[str, Any]:
    base = root or repo_root()
    return json.loads((base / "scraper" / "config" / "signals.json").read_text(encoding="utf-8"))


def extract_signals_row(
    row: sqlite3.Row,
    config: dict[str, Any],
    geo: GeoContext,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Extract one versioned signal payload without calculating a score."""
    text = _description(row)
    property_type = _text(row, "property_type")
    ceiling_height, ceiling_factor = _ceiling(row, text)
    large_windows, large_windows_factor = _large_windows(text)
    profiles = _empty_profiles()
    metrics = {
        "ceiling_height_m": ceiling_height,
        "large_windows": large_windows,
    }
    if property_type not in {"flat", "house", "plot"}:
        return {
            "version": SIGNALS_VERSION,
            "profiles": profiles,
            "geo": {
                "rail_station_km": None,
                "tram_stop_km": None,
                "lake_km": None,
                "river_km": None,
                "forest_km": None,
            },
            "metrics": metrics,
            "priceEvidence": None,
        }

    features = _extra_features(row)
    keywords = config["keywords"]
    geo_signals = _geo_signals(row, config, geo)
    evidence = (
        prices.evidence(row, config["comps"], config["rcn"], config["value"])
        if _sale(row)
        else None
    )
    price_quantile = _price_quantile_factor(row, evidence) if evidence is not None else None
    comparable_discount = (
        _comparable_discount_factor(evidence, config) if evidence is not None else None
    )
    overpriced: Factor = None
    if evidence is not None:
        overpriced = (
            1.0 if evidence.overpriced else 0.0 if evidence.effective_discount is not None else None
        )
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    opportunity = _opportunity_signals(row, history, rent_ppm, config, current_ms)
    common = {
        "price_quantile": price_quantile,
        "comparable_discount": comparable_discount,
        **opportunity,
    }
    transit_distance_known = (
        geo_signals.rail_station_km is not None or geo_signals.tram_stop_km is not None
    )
    for profile in profiles.values():
        profile["signals"].update(common)
        profile["penalties"]["overpriced"] = overpriced
        for kind, distance in (
            ("rail_station", geo_signals.rail_station_km),
            ("tram_stop", geo_signals.tram_stop_km),
        ):
            settings = config["rail"] if kind == "rail_station" else config["tram"]
            profile["signals"][kind] = (
                _distance_factor(
                    distance,
                    float(settings["distance_full_km"]),
                    float(settings["distance_zero_km"]),
                )
                if distance is not None
                else 0.0
                if transit_distance_known
                else None
            )

    building = _building_type(row)
    material = _material(row)
    year = _number(row, "build_year")
    charm_text = _has(text, keywords["charm"])
    old_house_text = property_type == "house" and _has(text, keywords["old_house"])
    if property_type == "house":
        charm_factors = [
            value
            for value in (
                1.0 if charm_text else None,
                0.9 if features.get("charm") is True else None,
                0.85 if old_house_text else None,
                0.0 if features.get("charm") is False else None,
            )
            if value is not None
        ]
        charm = max(charm_factors) if charm_factors else None
    else:
        charm = _feature_factor(features, "charm", charm_text)
    airbnb = _feature_factor(features, "airbnb_potential", _has(text, keywords["airbnb"]))
    insulation = _feature_factor(features, "insulation", _has(text, keywords["insulation"]))
    windows = _windows_factor(row, text, features, config)
    district_heating: Factor = None if not _heating(row) else float(_heating(row) == "district")
    outdoor_flat = _outdoor_factor(row["balcony"], row["terrace"])
    outdoor_house = _outdoor_factor(row["garden"], row["terrace"])
    walkability = (
        _factor(geo_signals.walkability / 100.0) if geo_signals.walkability is not None else None
    )
    commute = (
        _distance_factor(
            geo_signals.commute_km,
            float(config["new_flat"]["commute_full_km"]),
            float(config["new_flat"]["commute_zero_km"]),
        )
        if geo_signals.commute_km is not None
        else 0.0
        if geo_signals.commute_data_available
        else None
    )

    if property_type == "flat":
        signature = max(
            1.0 if building == "tenement" else 0.0,
            0.8 if _has(text, keywords["tenement"]) else 0.0,
            0.6 if material == "brick" else 0.0,
            0.35 if building == "apartment_building" else 0.0,
        )
        kamienica_age = (
            1.0
            if year is not None and year < 1939
            else 0.5
            if year is not None and year < 1970
            else 0.0
            if year is not None
            else None
        )
        new_age = (
            1.0
            if year is not None and year >= 2010
            else 0.65
            if year is not None and year >= 1990
            else 0.0
            if year is not None
            else None
        )
        prefab = (
            1.0 if material == "prefabricated_concrete" or _has(text, keywords["prefab"]) else 0.0
        )
        profiles["kamienica"]["signals"].update(
            {
                "walkability": walkability,
                "commute": commute,
                "building_character": signature,
                "building_age": kamienica_age,
                "charm": charm,
                "district_heating": district_heating,
                "insulation": insulation,
                "windows": windows,
                "ceiling": ceiling_factor,
                "large_windows": large_windows_factor,
                "outdoor": outdoor_flat,
            }
        )
        profiles["kamienica"]["penalties"].update(
            {
                "prefab_penalty": prefab,
                "newer_building": (None if year is None else float(year >= 1970)),
            }
        )
        profiles["nowe_mieszkanie"]["signals"].update(
            {
                "walkability": walkability,
                "commute": commute,
                "building_age": new_age,
                "ceiling": ceiling_factor,
                "large_windows": large_windows_factor,
                "condition": _condition_factor(row),
                "primary_market": (
                    None
                    if _text(row, "market") not in {"primary", "secondary"}
                    else float(_text(row, "market") == "primary")
                ),
                "parking": _parking_factor(row),
                "elevator": _boolean_factor(row["elevator"]),
                "outdoor": outdoor_flat,
                "air_conditioning": _boolean_factor(row["air_conditioning"]),
            }
        )
        profiles["nowe_mieszkanie"]["penalties"].update(
            {
                "prefab_penalty": prefab,
                "old_building_penalty": None if year is None else float(year < 1939),
            }
        )

    if property_type == "house":
        old_house = (
            1.0
            if year is not None and year < 1939
            else 0.65
            if year is not None and year < 1970
            else 0.0
            if year is not None
            else None
        )
        traditional_material = 1.0 if material in {"wood", "brick"} else 0.0 if material else None
        no_charm = not any(
            value is not None and value > 0
            for value in (old_house, charm, airbnb, traditional_material)
        )
        profiles["dom_airbnb"]["signals"].update(
            {
                "commute": commute,
                "old_house": old_house,
                "traditional_material": traditional_material,
                "charm": charm,
                "airbnb": airbnb,
                "ceiling": ceiling_factor,
                "large_windows": large_windows_factor,
            }
        )
        profiles["dom_airbnb"]["penalties"].update(
            {
                "modern_house": None if year is None else float(year >= 1990),
                "no_charm": float(no_charm),
            }
        )
        profiles["dom_kolej"]["signals"].update(
            {
                "house_condition": _condition_factor(row, house=True),
                "district_heating": district_heating,
                "parking": _parking_factor(row),
                "outdoor": outdoor_house,
                "internet": _boolean_factor(row["internet"]),
                "ceiling": ceiling_factor,
                "large_windows": large_windows_factor,
            }
        )

    forest_text = _has(text, keywords["nature_forest"])
    water_text = _has(text, keywords.get("nature_water", []))
    lake_text = _has(text, keywords.get("nature_lake", []))
    river_text = _has(text, keywords.get("nature_river", []))
    mountain_text = _has(text, keywords["nature_mountains"])
    nature = {
        "forest": _nature_factor(
            geo_signals.forest_km,
            geo_signals.forest_data_available,
            forest_text,
            config["nature"]["forest"],
        ),
        "water": _nature_factor(
            geo_signals.water_km,
            geo_signals.water_data_available,
            water_text,
            config["nature"].get("water", config["nature"]["lake"]),
        ),
        "lake": _nature_factor(
            geo_signals.lake_km,
            geo_signals.lake_data_available,
            lake_text or water_text,
            config["nature"]["lake"],
        ),
        "river": _nature_factor(
            geo_signals.river_km,
            geo_signals.river_data_available,
            river_text or water_text,
            config["nature"]["river"],
        ),
        "mountains": (
            1.0
            if geo_signals.mountain
            else 0.65
            if mountain_text
            else 0.0
            if _number(row, "lat") is not None and _number(row, "lng") is not None
            else None
        ),
        "tourist_area": float(geo_signals.tourist) if geo_signals.tourist is not None else None,
        "elevation": (
            _ramp_factor(geo_signals.elevation_m, 400.0, 800.0)
            if geo_signals.elevation_m is not None
            else None
        ),
    }

    rail: dict[str, Factor] = {
        "station": None,
        "station_frequency": None,
        "planned_rail": _factor(geo_signals.planned_factor),
        "station_gate": None,
        "station_data_available": (
            float(geo_signals.station_data_available)
            if geo_signals.station_data_available is not None
            else None
        ),
    }
    if property_type in {"house", "plot"} and geo_signals.station_data_available:
        if geo_signals.station_km is None:
            rail.update(
                {
                    "station": 0.0,
                    "station_frequency": 0.0,
                    "station_gate": 0.0,
                }
            )
        else:
            rail.update(
                {
                    "station": _distance_factor(
                        geo_signals.station_km,
                        float(config["rail"]["gate_km"]),
                        float(config["rail"]["decay_km"]),
                    ),
                    "station_frequency": (
                        _factor(
                            geo_signals.weekly_trips
                            / float(config["rail"]["frequency_cap_trips_per_week"])
                        )
                        if geo_signals.station_gate
                        else 0.0
                    ),
                    "station_gate": 1.0 if geo_signals.station_gate else 0.0,
                }
            )

    if property_type == "house":
        profiles["dom_airbnb"]["signals"].update(nature)
        profiles["dom_kolej"]["signals"].update(nature)
        profiles["dom_kolej"]["signals"].update(rail)

    if property_type == "plot":
        building_plot, negative = _building_confirmation(row, text, config)
        utilities = bool(re.search(r"\b(?:media|prad|woda|wodociag|gaz)\b", text))
        plot_signals = {
            "building_plot": 1.0 if building_plot else None,
            "utilities": float(utilities),
            **nature,
            **rail,
        }
        profiles["dzialka_kolej"]["signals"].update(plot_signals)
        profiles["dzialka_kolej"]["signals"]["fenced"] = _boolean_factor(row["fenced"])
        profiles["dzialka_airbnb"]["signals"].update(plot_signals)
        profiles["dzialka_airbnb"]["signals"]["airbnb"] = airbnb
        for profile_name in PROFILE_NAMES:
            profiles[profile_name]["signals"]["lake"] = nature["lake"]
            profiles[profile_name]["signals"]["river"] = nature["river"]
        for profile_name in ("dzialka_kolej", "dzialka_airbnb"):
            profiles[profile_name]["penalties"]["non_building_plot"] = (
                1.0 if negative and not building_plot else 0.0 if building_plot else None
            )
        profiles["dzialka_airbnb"]["penalties"]["build_unconfirmed"] = float(
            not building_plot and not negative
        )

    return {
        "version": SIGNALS_VERSION,
        "profiles": profiles,
        "geo": {
            "rail_station_km": geo_signals.rail_station_km,
            "tram_stop_km": geo_signals.tram_stop_km,
            "lake_km": geo_signals.lake_km,
            "river_km": geo_signals.river_km,
            "forest_km": geo_signals.forest_km,
        },
        "metrics": metrics,
        "priceEvidence": evidence.as_json() if evidence is not None else None,
    }


def _compact_payload(payload: dict[str, Any], property_type: str) -> dict[str, Any]:
    """Keep only applicable profiles and non-null factors in persisted JSON."""
    applicable = {
        "flat": ("kamienica", "nowe_mieszkanie"),
        "house": ("dom_airbnb", "dom_kolej"),
        "plot": ("dzialka_kolej", "dzialka_airbnb"),
    }.get(property_type, ())
    profiles: dict[str, Any] = {}
    for name in applicable:
        profile = payload["profiles"].get(name)
        if not isinstance(profile, dict):
            continue
        signals = {
            code: value for code, value in profile.get("signals", {}).items() if value is not None
        }
        penalties = {
            code: value for code, value in profile.get("penalties", {}).items() if value is not None
        }
        if signals or penalties:
            profiles[name] = {"signals": signals, "penalties": penalties}
    return {
        "version": payload["version"],
        "profiles": profiles,
        "geo": payload.get("geo"),
        "metrics": payload["metrics"],
        "priceEvidence": payload["priceEvidence"],
    }


def extract_signals_database(
    db: Any,
    *,
    source_id: str | None = None,
    pending_only: bool = True,
    limit: int | None = None,
    dry_run: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract signals for stored listings and persist only the signal columns."""
    config = config or load_signals_config()
    rows = list(
        db.iter_signal_rows(
            source_id=source_id,
            limit=limit,
            pending_only=pending_only,
            signals_version=SIGNALS_VERSION,
        )
    )
    empty_report = {
        "version": SIGNALS_VERSION,
        "processed": 0,
        "updated": 0,
        "dry_run": dry_run,
        "profiles": list(PROFILE_NAMES),
        "priceEvidence": {"listing": 0, "rcn": 0, "both": 0, "topEligible": 0, "overpriced": 0},
    }
    if not rows:
        return empty_report

    geo = GeoContext.load()
    price_rows = list(db.iter_signal_rows())
    prices = PriceModel(price_rows)
    regions = {_text(row, "voivodeship") for row in price_rows} - {""}
    object_kinds = {
        {"flat": "flat", "house": "building", "plot": "plot"}[kind]
        for row in price_rows
        if (kind := _text(row, "property_type")) in {"flat", "house", "plot"}
    }
    prices.load_rcn(
        db.iter_rcn_signal_rows(
            document_date_cutoff=_months_ago(int(config["rcn"]["months"])),
            regions=regions or None,
            object_kinds=object_kinds or None,
        )
    )
    history = db.price_history_stats()
    rent_ppm = _rent_price_samples(price_rows)
    updates: list[tuple[str, str, int, int]] = []
    evidence_counts: dict[str, int] = defaultdict(int)
    updated = 0
    now = int(time.time() * 1000)
    for index, row in enumerate(rows, start=1):
        payload = extract_signals_row(row, config, geo, prices, history, rent_ppm, now_ms=now)
        evidence = payload["priceEvidence"] or {}
        if evidence.get("listing") is not None:
            evidence_counts["listing"] += 1
        if evidence.get("rcn") is not None:
            evidence_counts["rcn"] += 1
        if evidence.get("listing") is not None and evidence.get("rcn") is not None:
            evidence_counts["both"] += 1
        if evidence.get("topEligible"):
            evidence_counts["topEligible"] += 1
        if evidence.get("overpriced"):
            evidence_counts["overpriced"] += 1
        updates.append(
            (
                json.dumps(
                    _compact_payload(payload, _text(row, "property_type")),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                SIGNALS_VERSION,
                now,
                int(row["id"]),
            )
        )
        if len(updates) >= 5000:
            if not dry_run:
                updated += db.update_signals(updates)
            updates.clear()
        if index % 5000 == 0:
            logger.info("extracted signals for %s/%s listings", index, len(rows))
    if updates and not dry_run:
        updated += db.update_signals(updates)
    return {
        "version": SIGNALS_VERSION,
        "processed": len(rows),
        "updated": updated,
        "dry_run": dry_run,
        "profiles": list(PROFILE_NAMES),
        "priceEvidence": {
            key: evidence_counts[key]
            for key in ("listing", "rcn", "both", "topEligible", "overpriced")
        },
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
    for values in samples.values():
        values.sort()
    return samples

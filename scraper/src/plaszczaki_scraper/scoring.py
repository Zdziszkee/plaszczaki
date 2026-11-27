"""Explainable, training-free attractiveness scoring for stored listings.

Six profiles produce independent 0-100 scores. Every positive contribution has
an explicit maximum inside its profile budget, and every result persists a full
breakdown that reconciles exactly with the final score.

All signals come from canonical fields, listing text, the listing database, or
open reference geodata. Price uses local quantiles and distance-weighted
comparable sales. Opportunity signals add auction discounts, days on market,
price cuts, and gross rental yield.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

from plaszczaki_scraper.config import repo_root
from plaszczaki_scraper.geodata import GeoContext, haversine_km
from plaszczaki_scraper.normalize import normalize_text

logger = logging.getLogger(__name__)

PROFILE_LABELS = {
    "kamienica": "Klimatyczna kamienica",
    "nowe_mieszkanie": "Nowe mieszkanie",
    "dzialka_kolej": "Działka pod kolej",
    "dom_airbnb": "Dom pod Airbnb",
    "dom_kolej": "Dom blisko kolei",
    "dzialka_airbnb": "Działka przy naturze",
}

CATEGORY_LABELS = {
    "price": "Cena i wartość",
    "location": "Lokalizacja i kolej",
    "attributes": "Atrybuty i dopasowanie",
    "nature": "Natura i turystyka",
    "opportunity": "Okazja rynkowa",
    "penalty": "Kary",
}


@dataclass(slots=True)
class Reason:
    code: str
    label: str
    points: float
    category: str
    max_points: float
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "points": self.points,
            "category": self.category,
            "maxPoints": self.max_points,
            "source": self.source,
        }


@dataclass(slots=True)
class ScoreParts:
    profile: str
    weights: dict[str, float]
    reasons: list[Reason] = field(default_factory=list)
    cap: float | None = None
    cap_label: str | None = None
    eligible: bool | None = True
    missing: list[str] = field(default_factory=list)

    def add(
        self,
        code: str,
        label: str,
        points: float,
        *,
        category: str,
        max_points: float,
        source: str,
        keep_zero: bool = False,
    ) -> None:
        value = round(points, 1)
        if not keep_zero and abs(value) < 0.05:
            return
        self.reasons.append(Reason(code, label, value, category, round(max_points, 1), source))

    def add_missing(self, label: str) -> None:
        if label not in self.missing:
            self.missing.append(label)

    @property
    def raw_score(self) -> float:
        return round(sum(reason.points for reason in self.reasons), 1)

    @property
    def effective_cap(self) -> float | None:
        if self.eligible is False:
            return 0.0
        return self.cap

    def finish(self) -> int:
        value = self.raw_score
        cap = self.effective_cap
        if cap is not None:
            value = min(value, cap)
        bounded = max(0.0, min(100.0, value))
        return math.floor(bounded + 0.5)

    def to_json(self, score: int) -> dict[str, Any]:
        categories = []
        for key, label in CATEGORY_LABELS.items():
            maximum = float(self.weights.get(key, 0.0))
            if maximum <= 0:
                continue
            points = round(
                sum(
                    reason.points
                    for reason in self.reasons
                    if reason.category == key and reason.points > 0
                ),
                1,
            )
            categories.append({"code": key, "label": label, "points": points, "maxPoints": maximum})
        positive = [reason.as_dict() for reason in self.reasons if reason.points > 0]
        penalties = [reason.as_dict() for reason in self.reasons if reason.points < 0]
        zero = [reason.as_dict() for reason in self.reasons if reason.points == 0]
        return {
            "score": score,
            "rawScore": self.raw_score,
            "cap": self.effective_cap,
            "capLabel": self.cap_label,
            "eligible": self.eligible,
            "categories": categories,
            "contributions": positive,
            "penalties": penalties,
            "zero": zero,
            "missing": self.missing,
        }


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
    station_data_available: bool | None = None
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
    commute_km: float | None = None
    commute_label: str | None = None
    commute_points: float = 0.0
    elevation_m: float | None = None


@dataclass(slots=True)
class ScoredListing:
    row_id: int
    score: int
    profile: str
    scores: dict[str, int]
    reasons: list[dict[str, Any]]
    breakdown: dict[str, Any]


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


def _extra_scoring_features(row: sqlite3.Row) -> dict[str, Any]:
    extra = _json(row, "extra") or {}
    if not isinstance(extra, dict):
        return {}
    features = extra.get("scoring_features")
    return features if isinstance(features, dict) else {}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


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


def _geo_signals(row: sqlite3.Row, config: dict[str, Any], geo: GeoContext) -> GeoSignals:
    result = GeoSignals()
    property_type = _text(row, "property_type")
    if property_type not in {"flat", "house", "plot"}:
        return result
    lat, lng = _number(row, "lat"), _number(row, "lng")
    if lat is None or lng is None:
        return result

    if property_type in {"house", "plot"}:
        rail = config["rail"]
        result.station_data_available = bool(geo.stations)
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

    if property_type in {"flat", "house"}:
        if property_type == "flat":
            result.walkability = geo.walkability(lat, lng) if geo.pois else None
        commute = config["new_flat"]
        station = geo.nearest_station(lat, lng, radius_km=float(commute["commute_zero_km"]))
        transit = geo.nearest_transit(lat, lng, radius_km=float(commute["commute_zero_km"]))
        candidates: list[tuple[float, str]] = []
        if station is not None:
            candidates.append((station[1], f"stacja {station[0].get('name') or 'kolejowa'}"))
        if transit is not None:
            candidates.append((transit[1], "przystanek komunikacji publicznej"))
        if candidates:
            distance, label = min(candidates, key=lambda item: item[0])
            result.commute_km = round(distance, 2)
            result.commute_label = label
            full = float(commute["commute_full_km"])
            zero = float(commute["commute_zero_km"])
            factor = 1.0 if distance <= full else max(0.0, 1.0 - (distance - full) / (zero - full))
            result.commute_points = round(float(commute["commute_points"]) * factor, 1)
    return result


# ---------------------------------------------------------------------------
# Weighted signals
# ---------------------------------------------------------------------------


def _price_factor(value: float, baseline: PriceBaseline) -> float:
    if value <= baseline.low:
        return 1.0
    if value <= baseline.median:
        span = max(baseline.median - baseline.low, 1.0)
        return _clamp(1.0 - 0.4 * (value - baseline.low) / span)
    if value <= baseline.high:
        span = max(baseline.high - baseline.median, 1.0)
        return _clamp(0.6 - 0.5 * (value - baseline.median) / span)
    return 0.0


def _price_label(value: float, baseline: PriceBaseline) -> str:
    if value <= baseline.low:
        return f"cena poniżej dolnego kwantyla ({baseline.level})"
    if value <= baseline.median:
        discount = max(0.0, round((baseline.median - value) / baseline.median * 100))
        return f"{discount}% poniżej mediany ({baseline.level}, n={baseline.count})"
    if value <= baseline.high:
        return f"cena w zakresie mediany-górnego kwantyla ({baseline.level})"
    return f"cena powyżej górnego kwantyla ({baseline.level})"


def _price_signal(
    parts: ScoreParts,
    row: sqlite3.Row,
    config: dict[str, Any],
    prices: PriceModel,
) -> None:
    budget = float(parts.weights.get("price", 0.0))
    if budget <= 0:
        return
    value = _ppm(row)
    evidence = prices.evidence(row, config["comps"], config["rcn"], config["value"])
    if value is None or not _sale(row):
        parts.add_missing("brak lokalnego punktu odniesienia dla ceny")
        return

    value_config = config["value"]
    quantile_max = round(budget * float(value_config["quantile_share"]), 1)
    comparable_max = round(budget - quantile_max, 1)
    if evidence.baseline is not None:
        factor = _price_factor(value, evidence.baseline)
        parts.add(
            "price_quantile",
            _price_label(value, evidence.baseline),
            quantile_max * factor,
            category="price",
            max_points=quantile_max,
            source="database",
            keep_zero=True,
        )
    else:
        parts.add_missing("brak lokalnego kwantyla ceny")

    if evidence.listing is None or evidence.effective_discount is None:
        count = evidence.rcn.count if evidence.rcn is not None else 0
        parts.add_missing(f"za mało podobnych ofert do porównania ceny ({count})")
        return

    zero_discount = float(value_config["zero_discount"])
    full_discount = float(value_config["full_discount"])
    comparable_factor = _clamp(
        (evidence.effective_discount - zero_discount) / (full_discount - zero_discount)
    )
    source = "rcn" if evidence.rcn is not None else "database"
    benchmark_label = (
        f"RCN n={evidence.rcn.count}"
        if evidence.rcn is not None
        else f"ofert n={evidence.listing.count}"
    )
    parts.add(
        "comparable_discount",
        f"{max(0.0, round(evidence.effective_discount * 100))}% poniżej mediany {benchmark_label}",
        comparable_max * comparable_factor,
        category="price",
        max_points=comparable_max,
        source=source,
        keep_zero=True,
    )

    if evidence.overpriced:
        parts.add(
            "overpriced",
            f"cena o ponad {round(float(value_config['overpriced_threshold']) * 100)}% "
            "powyżej mediany podobnych ofert / transakcji",
            -float(value_config["overpriced_penalty"]),
            category="penalty",
            max_points=0.0,
            source=source,
        )
        cap = float(value_config["overpriced_cap"])
        if parts.cap is None or cap < parts.cap:
            parts.cap = cap
            parts.cap_label = "limit dla ceny powyżej rynku"


def _opportunity_signal(
    parts: ScoreParts,
    row: sqlite3.Row,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> None:
    budget = float(parts.weights.get("opportunity", 0.0))
    if budget <= 0 or not _sale(row):
        return
    is_plot = _text(row, "property_type") == "plot"
    shares = (0.35, 0.25, 0.40, 0.0) if is_plot else (0.30, 0.20, 0.25, 0.25)
    auction_max = round(budget * shares[0], 1)
    market_max = round(budget * shares[1], 1)
    cut_max = round(budget * shares[2], 1)
    yield_max = round(budget - auction_max - market_max - cut_max, 1)

    price, estimate = _price(row), _number(row, "estimate_pln")
    round_number = _number(row, "auction_round") or 1
    if (
        _text(row, "offer_type") == "auction"
        and price is not None
        and estimate is not None
        and estimate > 0
    ):
        discount = max(0.0, (estimate - price) / estimate)
        factor = min(1.0, discount / 0.30)
        if round_number >= 2:
            factor = max(0.6, factor)
        parts.add(
            "auction_discount",
            f"dyskont {round(discount * 100)}% do wyceny",
            auction_max * factor,
            category="opportunity",
            max_points=auction_max,
            source="database",
        )

    published = _number(row, "published_at") or _number(row, "first_seen_at")
    if published is not None:
        days = max(0.0, (time.time() * 1000 - published) / 86_400_000)
        factor = 1.0 if days >= 30 else 0.5 if days >= 7 else 0.4 if days <= 1 else 0.0
        label = (
            f"oferta na rynku {int(days)} dni"
            if days >= 7
            else "oferta dodana ostatnio"
            if days <= 1
            else "oferta na rynku kilka dni"
        )
        parts.add(
            "market_time",
            label,
            market_max * factor,
            category="opportunity",
            max_points=market_max,
            source="database",
        )

    snapshot = history.get(int(row["id"]))
    if snapshot and snapshot.get("drops", 0) > 0:
        peak = float(snapshot.get("peak") or 0)
        drop = (peak - price) / peak if price is not None and peak > 0 else 0.0
        parts.add(
            "price_cut",
            f"obniżka ceny{(' o ' + str(round(drop * 100)) + '%') if drop > 0 else ''}",
            cut_max * _clamp(drop / 0.20),
            category="opportunity",
            max_points=cut_max,
            source="database",
        )

    if yield_max > 0:
        gross_yield = _rent_yield(row, rent_ppm)
        if gross_yield is not None:
            parts.add(
                "rental_yield",
                f"szacowany gross yield {gross_yield:.1f}%",
                yield_max * _clamp(gross_yield / 10.0),
                category="opportunity",
                max_points=yield_max,
                source="database",
            )


def _access_signal(parts: ScoreParts, geo: GeoSignals, config: dict[str, Any]) -> None:
    budget = float(parts.weights.get("location", 0.0))
    if budget <= 0:
        return
    if geo.commute_km is None:
        parts.add_missing("brak danych o dojeździe do stacji lub przystanku")
        return
    maximum = float(config["new_flat"]["commute_points"])
    factor = _clamp(geo.commute_points / maximum) if maximum > 0 else 0.0
    parts.add(
        "commute",
        f"dojazd: {geo.commute_label} — {geo.commute_km:.2f} km",
        budget * factor,
        category="location",
        max_points=budget,
        source="geo",
    )


def _rail_location_signal(
    parts: ScoreParts,
    geo: GeoSignals,
    config: dict[str, Any],
    *,
    require_gate: bool,
) -> None:
    budget = float(parts.weights.get("location", 0.0))
    if budget <= 0:
        return
    rail = config["rail"]
    distance_max = round(budget * 0.5, 1)
    frequency_max = round(budget * 0.3, 1)
    planned_max = round(budget - distance_max - frequency_max, 1)

    if require_gate:
        if geo.station_data_available is False:
            parts.eligible = None
            parts.add_missing("brak cache stacji kolejowych")
        elif not geo.station_gate:
            parts.eligible = False
            parts.cap = 0.0
            parts.cap_label = "brak stacji z połączeniami do celu w promieniu 3 km"

    if geo.station_km is not None:
        full = float(rail["gate_km"])
        zero = float(rail["decay_km"])
        factor = (
            1.0
            if geo.station_km <= full
            else max(0.0, 1.0 - (geo.station_km - full) / (zero - full))
        )
        station_label = geo.station_name or "stacja kolejowa"
        parts.add(
            "station",
            f"{station_label} — {geo.station_km:.2f} km",
            distance_max * factor,
            category="location",
            max_points=distance_max,
            source="geo",
            keep_zero=True,
        )
        if geo.station_gate:
            frequency_cap = float(rail["frequency_cap_trips_per_week"])
            factor = _clamp(geo.weekly_trips / frequency_cap)
            parts.add(
                "station_frequency",
                "połączenia do " + ", ".join(geo.station_destinations),
                frequency_max * factor,
                category="location",
                max_points=frequency_max,
                source="geo",
            )
    elif geo.station_data_available:
        parts.add_missing("brak stacji w pobliżu")

    if geo.planned_points:
        planned_cap = max(
            float(value)
            for value in config["planned_rail"].values()
            if isinstance(value, (int, float))
        )
        factor = _clamp(geo.planned_points / planned_cap) if planned_cap > 0 else 0.0
        parts.add(
            "planned_rail",
            f"planowana inwestycja: {geo.planned_label}",
            planned_max * factor,
            category="location",
            max_points=planned_max,
            source="geo",
        )


def _nature_signal(
    parts: ScoreParts,
    geo: GeoSignals,
    text: str,
    config: dict[str, Any],
) -> None:
    budget = float(parts.weights.get("nature", 0.0))
    if budget <= 0:
        return
    forest_max = round(budget * 0.35, 1)
    water_max = round(budget * 0.30, 1)
    mountain_max = round(budget * 0.20, 1)
    tourist_max = round(budget * 0.10, 1)
    elevation_max = round(budget - forest_max - water_max - mountain_max - tourist_max, 1)
    keywords = config["keywords"]
    found = False

    forest_geo = _clamp(geo.forest_points / float(config["nature"]["forest"]["points"]))
    forest_text = _has(text, keywords["nature_forest"])
    forest_factor = max(forest_geo, 0.65 if forest_text else 0.0)
    if forest_factor > 0:
        found = True
        parts.add(
            "forest",
            f"las {geo.forest_km:.2f} km" if geo.forest_km is not None else "las wskazany w opisie",
            forest_max * forest_factor,
            category="nature",
            max_points=forest_max,
            source="geo" if forest_geo > 0 else "text",
        )

    water_geo = _clamp(geo.water_points / float(config["nature"]["water"]["points"]))
    water_text = _has(text, keywords["nature_water"])
    water_factor = max(water_geo, 0.65 if water_text else 0.0)
    if water_factor > 0:
        found = True
        parts.add(
            "water",
            f"woda {geo.water_km:.2f} km" if geo.water_km is not None else "woda wskazana w opisie",
            water_max * water_factor,
            category="nature",
            max_points=water_max,
            source="geo" if water_geo > 0 else "text",
        )

    mountain_text = _has(text, keywords["nature_mountains"])
    mountain_factor = 1.0 if geo.mountain else 0.65 if mountain_text else 0.0
    if mountain_factor > 0:
        found = True
        parts.add(
            "mountains",
            f"region górski: {geo.mountain}" if geo.mountain else "góry wskazane w opisie",
            mountain_max * mountain_factor,
            category="nature",
            max_points=mountain_max,
            source="geo" if geo.mountain else "text",
        )

    if geo.tourist:
        found = True
        parts.add(
            "tourist_area",
            "okolica turystyczna",
            tourist_max,
            category="nature",
            max_points=tourist_max,
            source="geo",
        )

    if geo.elevation_m is not None and geo.elevation_m >= 400:
        found = True
        factor = _clamp((geo.elevation_m - 400) / 400)
        parts.add(
            "elevation",
            f"wysokość {geo.elevation_m:.0f} m n.p.m.",
            elevation_max * factor,
            category="nature",
            max_points=elevation_max,
            source="geo",
        )

    if not found:
        parts.add_missing("brak danych o lesie, wodzie i górach")


def _flat_location_signal(parts: ScoreParts, geo: GeoSignals, config: dict[str, Any]) -> None:
    budget = float(parts.weights.get("location", 0.0))
    if budget <= 0:
        return
    walkability_max = round(budget * 0.6, 1)
    commute_max = round(budget - walkability_max, 1)
    if geo.walkability is None:
        parts.add_missing("brak danych o użyteczności okolicy")
    else:
        parts.add(
            "walkability",
            f"użyteczność okolicy: {geo.walkability:.0f}/100",
            walkability_max * _clamp(geo.walkability / 100.0),
            category="location",
            max_points=walkability_max,
            source="geo",
        )
    if geo.commute_km is None:
        parts.add_missing("brak danych o dojeździe do stacji lub przystanku")
    else:
        maximum = float(config["new_flat"]["commute_points"])
        factor = _clamp(geo.commute_points / maximum) if maximum > 0 else 0.0
        parts.add(
            "commute",
            f"dojazd: {geo.commute_label} — {geo.commute_km:.2f} km",
            commute_max * factor,
            category="location",
            max_points=commute_max,
            source="geo",
        )


def _building_confirmation(
    row: sqlite3.Row, text: str, config: dict[str, Any]
) -> tuple[bool, bool]:
    features = _extra_scoring_features(row)
    confirmed = (
        _has(text, config["keywords"]["plot_building"]) or features.get("building_plot") is True
    )
    negative = (
        _has(text, config["keywords"]["plot_negative"]) or features.get("building_plot") is False
    )
    return confirmed, negative


def _profile_kamienica(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoreParts:
    parts = ScoreParts("kamienica", dict(config["profile_weights"]["kamienica"]))
    features = _extra_scoring_features(row)
    building = _building_type(row)
    signature = max(
        1.0 if building == "tenement" else 0.0,
        0.8 if _has(text, config["keywords"]["tenement"]) else 0.0,
        0.6 if _material(row) == "brick" else 0.0,
        0.35 if building == "apartment_building" else 0.0,
    )
    parts.add(
        "building_character",
        "kamienica / starsza zabudowa",
        10.0 * signature,
        category="attributes",
        max_points=10.0,
        source="structured" if building == "tenement" else "text",
    )
    year = _number(row, "build_year")
    if year is not None:
        factor = 1.0 if year < 1939 else 0.5 if year < 1970 else 0.0
        parts.add(
            "building_age",
            f"rok budowy: {int(year)}",
            6.0 * factor,
            category="attributes",
            max_points=6.0,
            source="structured",
        )
        if year >= 1970:
            parts.add(
                "newer_building",
                "budynek po 1970 r., bez starszego klimatu",
                -3.0,
                category="penalty",
                max_points=0.0,
                source="structured",
            )
    charm = _has(text, config["keywords"]["charm"]) or features.get("charm") is True
    parts.add(
        "charm",
        "oryginalne detale / klimat / wysokość",
        8.0 if charm else 0.0,
        category="attributes",
        max_points=8.0,
        source="text" if _has(text, config["keywords"]["charm"]) else "llm",
    )
    if _heating(row) == "district":
        parts.add(
            "district_heating",
            "ogrzewanie miejskie",
            8.0,
            category="attributes",
            max_points=8.0,
            source="structured",
        )
    insulation = _has(text, config["keywords"]["insulation"]) or features.get("insulation") is True
    parts.add(
        "insulation",
        "ocieplenie / termomodernizacja",
        5.0 if insulation else 0.0,
        category="attributes",
        max_points=5.0,
        source="text",
    )
    windows = _windows(row)
    windows_factor = (
        1.0
        if windows in {"new", "partially_new"}
        else 0.75
        if _has(text, config["keywords"]["new_windows"]) or features.get("new_windows") is True
        else 0.4
        if windows == "plastic"
        else 0.0
    )
    parts.add(
        "windows",
        "nowa / wymieniona stolarka okienna" if windows_factor >= 0.75 else "plastikowa stolarka",
        4.0 * windows_factor,
        category="attributes",
        max_points=4.0,
        source="structured",
    )
    ceiling = _number(row, "ceiling_height_m")
    if ceiling is not None:
        parts.add(
            "ceiling",
            f"wysokość sufitów: {ceiling:g} m",
            2.0 * _clamp((ceiling - 2.6) / 0.6),
            category="attributes",
            max_points=2.0,
            source="structured",
        )
    if row["balcony"] or row["terrace"]:
        parts.add(
            "outdoor",
            "balkon / taras",
            2.0,
            category="attributes",
            max_points=2.0,
            source="structured",
        )
    _flat_location_signal(parts, geo, config)
    _price_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    if _material(row) == "prefabricated_concrete" or _has(text, config["keywords"]["prefab"]):
        parts.add(
            "prefab_penalty",
            "wielka płyta / budownictwo systemowe",
            -float(config["prefab"]["penalty"]),
            category="penalty",
            max_points=0.0,
            source="structured",
        )
        parts.cap = float(config["prefab"]["cap"])
        parts.cap_label = "limit dla budownictwa systemowego"
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
    parts = ScoreParts("nowe_mieszkanie", dict(config["profile_weights"]["nowe_mieszkanie"]))
    year = _number(row, "build_year")
    if year is not None:
        factor = 1.0 if year >= 2010 else 0.65 if year >= 1990 else 0.0
        parts.add(
            "building_age",
            f"rok budowy: {int(year)}",
            8.0 * factor,
            category="attributes",
            max_points=8.0,
            source="structured",
        )
        if year < 1939:
            parts.add(
                "old_building_penalty",
                "przedwojenny budynek, nie nowy standard",
                -6.0,
                category="penalty",
                max_points=0.0,
                source="structured",
            )
    condition = _text(row, "condition")
    if condition in {
        "developer",
        "to_finish",
        "move_in_ready",
        "very_high_standard",
        "high_standard",
    }:
        parts.add(
            "condition",
            "wysoki standard / gotowe do zamieszkania",
            6.0,
            category="attributes",
            max_points=6.0,
            source="structured",
        )
    if _text(row, "market") == "primary":
        parts.add(
            "primary_market",
            "rynek pierwotny",
            3.0,
            category="attributes",
            max_points=3.0,
            source="structured",
        )
    if row["parking"] and row["parking"] != "none":
        parts.add(
            "parking",
            "parking",
            3.0,
            category="attributes",
            max_points=3.0,
            source="structured",
        )
    if row["elevator"]:
        parts.add(
            "elevator",
            "winda",
            2.0,
            category="attributes",
            max_points=2.0,
            source="structured",
        )
    if row["balcony"] or row["terrace"]:
        parts.add(
            "outdoor",
            "balkon / taras",
            1.0,
            category="attributes",
            max_points=1.0,
            source="structured",
        )
    if row["air_conditioning"]:
        parts.add(
            "air_conditioning",
            "klimatyzacja",
            1.0,
            category="attributes",
            max_points=1.0,
            source="structured",
        )
    _flat_location_signal(parts, geo, config)
    _price_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    if _material(row) == "prefabricated_concrete" or _has(text, config["keywords"]["prefab"]):
        parts.add(
            "prefab_penalty",
            "wielka płyta / budownictwo systemowe",
            -float(config["prefab"]["penalty"]),
            category="penalty",
            max_points=0.0,
            source="structured",
        )
        parts.cap = float(config["prefab"]["cap"])
        parts.cap_label = "limit dla budownictwa systemowego"
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
    parts = ScoreParts("dom_airbnb", dict(config["profile_weights"]["dom_airbnb"]))
    features = _extra_scoring_features(row)
    year = _number(row, "build_year")
    if year is not None:
        factor = 1.0 if year < 1939 else 0.65 if year < 1970 else 0.0
        parts.add(
            "old_house",
            f"rok budowy: {int(year)}",
            8.0 * factor,
            category="attributes",
            max_points=8.0,
            source="structured",
        )
        if year >= 1990:
            parts.add(
                "modern_house",
                "nowoczesny dom, bez starszego klimatu",
                -float(config["house"]["modern_penalty"]),
                category="penalty",
                max_points=0.0,
                source="structured",
            )
    if _material(row) in {"wood", "brick"}:
        parts.add(
            "traditional_material",
            "drewno / cegła — tradycyjna zabudowa",
            5.0,
            category="attributes",
            max_points=5.0,
            source="structured",
        )
    charm_factor = max(
        1.0 if _has(text, config["keywords"]["charm"]) else 0.0,
        0.85 if _has(text, config["keywords"]["old_house"]) else 0.0,
        0.9 if features.get("charm") is True else 0.0,
    )
    parts.add(
        "charm",
        "klimat / charakter / zabytkowe detale",
        12.0 * charm_factor,
        category="attributes",
        max_points=12.0,
        source="text",
    )
    airbnb = _has(text, config["keywords"]["airbnb"]) or features.get("airbnb_potential") is True
    parts.add(
        "airbnb",
        "potencjał noclegowy / inwestycyjny",
        5.0 if airbnb else 0.0,
        category="attributes",
        max_points=5.0,
        source="text",
    )
    _nature_signal(parts, geo, text, config)
    _access_signal(parts, geo, config)
    _price_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    if not any(
        reason.code in {"old_house", "charm", "airbnb", "traditional_material"}
        for reason in parts.reasons
    ):
        parts.add(
            "no_charm",
            "brak sygnałów klimatu / starego domu",
            -6.0,
            category="penalty",
            max_points=0.0,
            source="structured",
        )
        parts.cap = float(config["house"]["no_charm_cap"])
        parts.cap_label = "limit bez sygnałów klimatu"
    return parts


def _profile_house_rail(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoreParts:
    parts = ScoreParts("dom_kolej", dict(config["profile_weights"]["dom_kolej"]))
    year = _number(row, "build_year")
    condition = normalize_text(_text(row, "condition"))
    condition_factor = (
        1.0
        if condition in {"very_high_standard", "high_standard", "move_in_ready"}
        else 0.7
        if condition in {"good", "very_good", "after_renovation"}
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
        else 0.0
    )
    factor = max(year_factor, condition_factor)
    parts.add(
        "house_condition",
        f"rok budowy: {int(year)}" if year is not None else "stan techniczny domu",
        10.0 * factor,
        category="attributes",
        max_points=10.0,
        source="structured",
    )
    if _heating(row) == "district":
        parts.add(
            "district_heating",
            "ogrzewanie miejskie",
            5.0,
            category="attributes",
            max_points=5.0,
            source="structured",
        )
    if row["parking"] and row["parking"] != "none":
        parts.add(
            "parking",
            "parking",
            4.0,
            category="attributes",
            max_points=4.0,
            source="structured",
        )
    if row["garden"] or row["terrace"]:
        parts.add(
            "outdoor",
            "ogród / taras",
            3.0,
            category="attributes",
            max_points=3.0,
            source="structured",
        )
    if row["internet"]:
        parts.add(
            "internet",
            "internet",
            3.0,
            category="attributes",
            max_points=3.0,
            source="structured",
        )
    _rail_location_signal(parts, geo, config, require_gate=True)
    _nature_signal(parts, geo, text, config)
    _price_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    return parts


def _profile_plot_rail(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoreParts:
    parts = ScoreParts("dzialka_kolej", dict(config["profile_weights"]["dzialka_kolej"]))
    building, negative = _building_confirmation(row, text, config)
    if building:
        parts.add(
            "building_plot",
            "działka budowlana / MPZP / WZ",
            22.0,
            category="attributes",
            max_points=22.0,
            source="text",
        )
    elif negative:
        parts.add(
            "non_building_plot",
            "działka rolna / leśna / rekreacyjna",
            -20.0,
            category="penalty",
            max_points=0.0,
            source="text",
        )
        parts.eligible = False
        parts.cap = 0.0
        parts.cap_label = "brak przeznaczenia do zabudowy"
    else:
        parts.eligible = None
        parts.add_missing("brak potwierdzonego prawa do zabudowy")
    if _has(text, ["media ", "prąd ", "woda ", "gaz "]):
        parts.add(
            "utilities",
            "media w okolicy",
            5.0,
            category="attributes",
            max_points=5.0,
            source="text",
        )
    if row["fenced"]:
        parts.add(
            "fenced",
            "ogrodzona działka",
            3.0,
            category="attributes",
            max_points=3.0,
            source="structured",
        )
    _rail_location_signal(parts, geo, config, require_gate=True)
    _nature_signal(parts, geo, text, config)
    _price_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
    return parts


def _profile_plot_airbnb(
    row: sqlite3.Row,
    text: str,
    config: dict[str, Any],
    geo: GeoSignals,
    prices: PriceModel,
    history: dict[int, dict[str, Any]],
    rent_ppm: dict[tuple[str, str], list[float]],
) -> ScoreParts:
    parts = ScoreParts("dzialka_airbnb", dict(config["profile_weights"]["dzialka_airbnb"]))
    features = _extra_scoring_features(row)
    building, negative = _building_confirmation(row, text, config)
    if building:
        parts.add(
            "building_plot",
            "możliwość zabudowy / MPZP / WZ",
            17.0,
            category="attributes",
            max_points=17.0,
            source="text",
        )
    if negative:
        parts.add(
            "non_building_plot",
            "działka rolna / leśna / rekreacyjna",
            -20.0,
            category="penalty",
            max_points=0.0,
            source="text",
        )
        parts.cap = float(config["plot_airbnb"]["non_residential_cap"])
        parts.cap_label = "limit dla terenu bez zabudowy"
    elif not building:
        parts.add(
            "build_unconfirmed",
            "brak potwierdzonej możliwości zabudowy",
            -5.0,
            category="penalty",
            max_points=0.0,
            source="text",
        )
        parts.cap = float(config["plot_airbnb"]["unconfirmed_build_cap"])
        parts.cap_label = "limit bez potwierdzonej zabudowy"
        parts.add_missing("brak potwierdzonego prawa do zabudowy")
    airbnb = _has(text, config["keywords"]["airbnb"]) or features.get("airbnb_potential") is True
    parts.add(
        "airbnb",
        "potencjał inwestycyjny / turystyczny",
        5.0 if airbnb else 0.0,
        category="attributes",
        max_points=5.0,
        source="text",
    )
    if _has(text, ["media ", "prąd ", "woda ", "gaz "]):
        parts.add(
            "utilities",
            "media w okolicy",
            3.0,
            category="attributes",
            max_points=3.0,
            source="text",
        )
    _nature_signal(parts, geo, text, config)
    _rail_location_signal(parts, geo, config, require_gate=False)
    _price_signal(parts, row, config, prices)
    _opportunity_signal(parts, row, history, rent_ppm)
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
        return ScoredListing(
            int(row["id"]),
            0,
            "other",
            {},
            [],
            {
                "version": str(config["version"]),
                "winner": "other",
                "profiles": {},
                "priceEvidence": None,
            },
        )
    signals = _geo_signals(row, config, geo)
    profiles: list[ScoreParts]
    if property_type == "flat":
        profiles = [
            _profile_kamienica(row, text, config, signals, prices, history, rent_ppm),
            _profile_new_flat(row, text, config, signals, prices, history, rent_ppm),
        ]
    elif property_type == "house":
        profiles = [
            _profile_house_airbnb(row, text, config, signals, prices, history, rent_ppm),
            _profile_house_rail(row, text, config, signals, prices, history, rent_ppm),
        ]
    else:
        profiles = [
            _profile_plot_rail(row, text, config, signals, prices, history, rent_ppm),
            _profile_plot_airbnb(row, text, config, signals, prices, history, rent_ppm),
        ]

    scored = [(parts, parts.finish()) for parts in profiles]
    scored.sort(key=lambda item: (item[0].eligible is False, -item[1]))
    winner, score = scored[0]
    scores = {parts.profile: value for parts, value in scored}
    breakdown = {
        "version": str(config["version"]),
        "winner": winner.profile,
        "profiles": {parts.profile: parts.to_json(value) for parts, value in scored},
        "priceEvidence": prices.evidence(
            row, config["comps"], config["rcn"], config["value"]
        ).as_json(),
    }
    return ScoredListing(
        int(row["id"]),
        score,
        winner.profile,
        scores,
        _reason_json(winner, score),
        breakdown,
    )


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
    version = str(config["version"])
    rows = list(
        db.iter_score_rows(
            source_id=source_id,
            limit=limit,
            pending_only=pending_only,
            score_version=version,
        )
    )
    if not rows:
        return {
            "processed": 0,
            "updated": 0,
            "dry_run": dry_run,
            "version": version,
            "profiles": {},
            "priceEvidence": {
                "listing": 0,
                "rcn": 0,
                "both": 0,
                "topEligible": 0,
                "overpriced": 0,
            },
        }
    geo = geo or GeoContext.load()
    price_rows = list(db.iter_score_rows()) if pending_only or source_id is not None else rows
    prices = PriceModel(price_rows)
    regions = {_text(row, "voivodeship") for row in price_rows} - {""}
    object_kinds = {
        {"flat": "flat", "house": "building", "plot": "plot"}[kind]
        for row in price_rows
        if (kind := _text(row, "property_type")) in {"flat", "house", "plot"}
    }
    prices.load_rcn(
        db.iter_rcn_score_rows(
            document_date_cutoff=_months_ago(int(config["rcn"]["months"])),
            regions=regions or None,
            object_kinds=object_kinds or None,
        )
    )
    history = db.price_history_stats()
    rent_ppm = _rent_price_samples(price_rows)
    updates: list[tuple[Any, ...]] = []
    distributions: dict[str, list[int]] = defaultdict(list)
    evidence_counts: dict[str, int] = defaultdict(int)
    now = int(time.time() * 1000)
    for index, row in enumerate(rows, start=1):
        result = score_row(row, config, geo, prices, history, rent_ppm)
        distributions[result.profile].append(result.score)
        evidence = result.breakdown.get("priceEvidence") or {}
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
                result.score,
                result.profile,
                json.dumps(result.scores, ensure_ascii=False, sort_keys=True),
                json.dumps(result.reasons, ensure_ascii=False, sort_keys=True),
                json.dumps(result.breakdown, ensure_ascii=False, sort_keys=True),
                now,
                version,
                result.row_id,
            )
        )
        if index % 5000 == 0:
            logger.info("scored %s/%s listings", index, len(rows))
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
        "version": version,
        "processed": len(rows),
        "updated": 0 if dry_run else len(updates),
        "dry_run": dry_run,
        "profiles": profiles,
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

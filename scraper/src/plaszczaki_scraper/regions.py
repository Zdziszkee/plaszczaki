"""Geographic scope matching for Małopolskie and Śląskie."""

from __future__ import annotations

from dataclasses import dataclass

from plaszczaki_scraper.config import RegionConfig, RegionsConfig
from plaszczaki_scraper.normalize import normalize_text

_PLACE_PREFIXES = (
    "powiat ",
    "gmina ",
    "miasto ",
    "gmina miejsko wiejska ",
    "gmina wiejska ",
    "gmina miejska ",
)


def _normalize_place(value: str) -> str:
    normalized = normalize_text(value)
    for prefix in _PLACE_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.strip()


@dataclass(frozen=True, slots=True)
class RegionMatch:
    voivodeship: str
    county: str | None = None
    city: str | None = None


class RegionRegistry:
    """Resolve source location fields to one of the configured voivodeships."""

    def __init__(self, config: RegionsConfig) -> None:
        self.config = config
        self._alias_to_region: dict[str, str] = {}
        self._counties: dict[str, set[str]] = {}
        self._cities: dict[str, set[str]] = {}

        for region in config.regions.values():
            for alias in (region.id, region.name, *region.aliases):
                self._alias_to_region[_normalize_place(alias)] = region.id
            for county in region.counties:
                self._counties.setdefault(_normalize_place(county), set()).add(region.id)
            for city in region.cities:
                self._cities.setdefault(_normalize_place(city), set()).add(region.id)

    @classmethod
    def load(cls) -> RegionRegistry:
        from plaszczaki_scraper.config import load_regions_config

        return cls(load_regions_config())

    @property
    def default_regions(self) -> tuple[str, ...]:
        return self.config.default

    def has_region(self, region_id: str) -> bool:
        return region_id in self.config.regions

    def region(self, region_id: str) -> RegionConfig:
        return self.config.regions[region_id]

    def canonical_voivodeship(self, value: str | None) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None

        # Otodom sometimes provides ids such as "malopolskie/krakow/...".
        if "/" in text:
            text = text.split("/", 1)[0]

        normalized = _normalize_place(text)
        if normalized in self._alias_to_region:
            return self._alias_to_region[normalized]

        # Handle "powiat krakowski" and similar prefixes.
        stripped = _normalize_place(normalized)
        return self._alias_to_region.get(stripped)

    def match_county(self, value: str | None) -> str | None:
        if not value:
            return None
        regions = self._counties.get(_normalize_place(str(value)), set())
        return next(iter(regions)) if len(regions) == 1 else None

    def match_city(self, value: str | None) -> str | None:
        if not value:
            return None
        regions = self._cities.get(_normalize_place(str(value)), set())
        return next(iter(regions)) if len(regions) == 1 else None

    def region_for_point(self, lat: float | None, lng: float | None) -> str | None:
        if lat is None or lng is None:
            return None

        matches = [
            region.id
            for region in self.config.regions.values()
            if self._bbox_contains(region.bbox, lat, lng)
        ]
        # Overlapping bounding boxes (e.g. near the border) are ambiguous.
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _bbox_contains(
        bbox: tuple[float, float, float, float],
        lat: float,
        lng: float,
    ) -> bool:
        min_lon, min_lat, max_lon, max_lat = bbox
        return min_lon <= lng <= max_lon and min_lat <= lat <= max_lat

    def resolve(
        self,
        *,
        province: str | None = None,
        county: str | None = None,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> RegionMatch | None:
        """Resolve a location using the most specific available signal."""
        region_id = self.canonical_voivodeship(province)
        if region_id is not None:
            return RegionMatch(
                voivodeship=region_id,
                county=county,
                city=city,
            )

        region_id = self.match_county(county)
        if region_id is not None:
            return RegionMatch(voivodeship=region_id, county=county, city=city)

        region_id = self.match_city(city)
        if region_id is not None:
            return RegionMatch(voivodeship=region_id, county=county, city=city)

        region_id = self.region_for_point(lat, lng)
        if region_id is not None:
            return RegionMatch(voivodeship=region_id, county=county, city=city)

        return None

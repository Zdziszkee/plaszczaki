from __future__ import annotations

from plaszczaki_scraper.config import load_regions_config
from plaszczaki_scraper.regions import RegionRegistry


def _registry() -> RegionRegistry:
    return RegionRegistry(load_regions_config())


def test_default_regions() -> None:
    assert _registry().default_regions == ("malopolskie", "slaskie")


def test_canonical_voivodeship() -> None:
    registry = _registry()
    assert registry.canonical_voivodeship("małopolskie") == "malopolskie"
    assert registry.canonical_voivodeship("śląskie") == "slaskie"
    assert registry.canonical_voivodeship("malopolskie/krakow/krakow") == "malopolskie"
    assert registry.canonical_voivodeship("POWIAT KRAKOWSKI") is None


def test_county_and_city_matching() -> None:
    registry = _registry()
    assert registry.match_county("powiat krakowski") == "malopolskie"
    assert registry.match_county("krakowski") == "malopolskie"
    assert registry.match_city("Katowice") == "slaskie"
    assert registry.match_city("Kraków") == "malopolskie"
    assert registry.match_city("Nieznane Miasto") is None


def test_resolve_by_source_hierarchy() -> None:
    registry = _registry()
    match = registry.resolve(province="małopolskie", county="krakowski", city="Rząska")
    assert match is not None
    assert match.voivodeship == "malopolskie"
    assert match.county == "krakowski"
    assert match.city == "Rząska"


def test_resolve_by_city() -> None:
    registry = _registry()
    match = registry.resolve(city="Kraków")
    assert match is not None
    assert match.voivodeship == "malopolskie"

    match = registry.resolve(city="Katowice")
    assert match is not None
    assert match.voivodeship == "slaskie"


def test_region_for_point() -> None:
    registry = _registry()
    # Tarnów is clearly inside Małopolskie and outside Śląskie's box.
    assert registry.region_for_point(50.01, 20.98) == "malopolskie"
    # Katowice is clearly inside Śląskie and outside Małopolskie's box.
    assert registry.region_for_point(50.26, 19.02) == "slaskie"
    # Kraków falls inside both bounding boxes, so it is intentionally ambiguous.
    assert registry.region_for_point(50.06, 19.94) is None
    assert registry.region_for_point(None, None) is None

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyproj import Transformer

from plaszczaki_scraper.geodata import (
    GeoContext,
    _geojson_polygon_rings,
    _poi_elements,
    _project_ring,
    _shapefile_rings,
    _simplify_lnglat,
    _transit_category,
)


def test_simplify_lnglat_removes_near_duplicates_and_closure() -> None:
    ring = _simplify_lnglat(
        [
            (19.0, 50.0),
            (19.00001, 50.00001),
            (19.001, 50.001),
            (19.002, 50.0),
            (19.0, 50.0),
        ]
    )
    assert ring == [[19.0, 50.0], [19.001, 50.001], [19.002, 50.0]]


def test_project_ring_transforms_epsg_2180_to_wgs84() -> None:
    transformer = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
    ring = _project_ring([(500000, 0), (501000, 0), (501000, 1000)], transformer)
    assert len(ring) == 3
    assert 18.9 < ring[0][0] < 19.1
    assert 47.8 < ring[0][1] < 48.0


def test_geojson_polygon_rings_uses_exterior_rings_only() -> None:
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [(0, 0), (1, 0), (1, 1), (0, 0)],
            [(0.2, 0.2), (0.4, 0.2), (0.4, 0.4), (0.2, 0.2)],
        ],
    }
    multipolygon = {
        "type": "MultiPolygon",
        "coordinates": [polygon["coordinates"], polygon["coordinates"]],
    }
    assert list(_geojson_polygon_rings(polygon)) == [polygon["coordinates"][0]]
    assert list(_geojson_polygon_rings(multipolygon)) == [
        polygon["coordinates"][0],
        polygon["coordinates"][0],
    ]


def test_shapefile_rings_splits_parts() -> None:
    shape = SimpleNamespace(
        points=[(0, 0), (1, 0), (1, 1), (2, 2), (3, 2), (3, 3)],
        parts=[0, 3],
    )
    assert list(_shapefile_rings(shape)) == [
        [(0, 0), (1, 0), (1, 1)],
        [(2, 2), (3, 2), (3, 3)],
    ]


def test_transit_category_distinguishes_tram_rail_and_bus() -> None:
    assert _transit_category({"railway": "tram_stop"}) == "tram_stop"
    assert _transit_category({"railway": "station"}) == "rail_station"
    assert _transit_category({"railway": "halt"}) == "rail_station"
    assert _transit_category({"highway": "bus_stop"}) == "transit"
    assert _transit_category({"railway": "tram"}) is None


def test_poi_elements_preserves_transit_types() -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "lat": 50.0,
                "lon": 19.0,
                "tags": {"railway": "tram_stop", "name": "Dworzec"},
            },
            {"type": "node", "lat": 50.1, "lon": 19.1, "tags": {"railway": "station"}},
            {"type": "node", "lat": 50.2, "lon": 19.2, "tags": {"highway": "bus_stop"}},
        ]
    }
    assert _poi_elements(payload) == [
        {"lat": 50.0, "lng": 19.0, "cat": "tram_stop", "name": "Dworzec"},
        {"lat": 50.1, "lng": 19.1, "cat": "rail_station", "name": None},
        {"lat": 50.2, "lng": 19.2, "cat": "transit", "name": None},
    ]


def test_geo_context_loads_transit_sidecar_without_overwriting_pois(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "poi.json").write_text(
        json.dumps({"pois": [{"lat": 50.0, "lng": 19.0, "cat": "grocery"}]}),
        encoding="utf-8",
    )
    (cache / "transit.json").write_text(
        json.dumps(
            {
                "elements": [
                    {"type": "node", "lat": 50.01, "lon": 19.01, "tags": {"railway": "tram_stop"}}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("plaszczaki_scraper.geodata.cache_dir", lambda: cache)

    context = GeoContext.load(root=tmp_path)

    assert {poi["cat"] for poi in context.pois} == {"grocery", "tram_stop"}
    assert context.nearest_poi(50.0, 19.0, categories=("tram_stop",)) is not None

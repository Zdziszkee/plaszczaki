from __future__ import annotations

from types import SimpleNamespace

from pyproj import Transformer

from plaszczaki_scraper.geodata import (
    _geojson_polygon_rings,
    _project_ring,
    _shapefile_rings,
    _simplify_lnglat,
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

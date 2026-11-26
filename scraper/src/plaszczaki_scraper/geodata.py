"""Reference geodata for attractiveness scoring.

Fetchers (CLI ``geodata``) download open data into ``data/cache``:
- GTFS national rail feed (PolishTrainsGTFS: PolRegio, PKP Intercity, Koleje
  Śląskie/Małopolskie and others) -> stations with direct service to the four
  target cities and weekly frequency.
- BDOT10k forest/water polygons for the two voivodeships (preferred), with
  OSM Overpass as a fallback, plus OSM amenity POIs.
- Open-Meteo elevation for house/plot points (mountain proxy).

The scoring module loads whatever is cached; missing files degrade gracefully
to neutral geo signals. Planned rail comes from the map snapshots committed in
``web/public/data`` (planned-rail.geojson, kolej-plus.geojson).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import shutil
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import flatgeobuf
import fsspec
import httpx
import shapefile
from pyproj import Transformer

from plaszczaki_scraper.config import load_regions_config, repo_root
from plaszczaki_scraper.normalize import normalize_text
from plaszczaki_scraper.robots import default_cache_dir

logger = logging.getLogger(__name__)

GTFS_URL = "https://mkuran.pl/gtfs/polish_trains.zip"
BDOT_MALOPOLSKA_URL = "https://mapymalopolski.pl/shared/dane/12_BDOT10k_klasy_FlatGeoBuf.zip"
BDOT_SLASKIE_FOREST_URL = (
    "https://pzgik.wodgik.katowice.pl/download/bdot10k/tereny-zielone-i-uprawy.zip"
)
BDOT_SLASKIE_WATER_URL = "https://pzgik.wodgik.katowice.pl/download/bdot10k/siec-wodna.zip"
OVERPASS_ENDPOINTS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

NATURE_QUERY = """[out:json][timeout:300];
area["ISO3166-2"="PL-24"]->.slaskie;
area["ISO3166-2"="PL-12"]->.malopolskie;
(
  way["landuse"="forest"](area.slaskie);
  way["natural"="wood"](area.slaskie);
  way["natural"="water"]["water"~"^(lake|reservoir|pond)$"](area.slaskie);
  way["landuse"="reservoir"](area.slaskie);
  way["landuse"="forest"](area.malopolskie);
  way["natural"="wood"](area.malopolskie);
  way["natural"="water"]["water"~"^(lake|reservoir|pond)$"](area.malopolskie);
  way["landuse"="reservoir"](area.malopolskie);
);
out geom;"""

POI_QUERY = """[out:json][timeout:300];
area["ISO3166-2"="PL-24"]->.slaskie;
area["ISO3166-2"="PL-12"]->.malopolskie;
(
  node["shop"~"^(supermarket|convenience|greengrocer|bakery|butcher|deli)$"](area.slaskie);
  node["amenity"~"^(restaurant|cafe|fast_food|pharmacy|clinic|doctors|school|kindergarten)$"](area.slaskie);
  node["leisure"~"^(park|playground|garden)$"](area.slaskie);
  node["highway"="bus_stop"](area.slaskie);
  node["railway"~"^(station|halt|tram_stop)$"](area.slaskie);
  node["shop"~"^(supermarket|convenience|greengrocer|bakery|butcher|deli)$"](area.malopolskie);
  node["amenity"~"^(restaurant|cafe|fast_food|pharmacy|clinic|doctors|school|kindergarten)$"](area.malopolskie);
  node["leisure"~"^(park|playground|garden)$"](area.malopolskie);
  node["highway"="bus_stop"](area.malopolskie);
  node["railway"~"^(station|halt|tram_stop)$"](area.malopolskie);
);
out;"""

# GTFS route_type values that mean rail (urban rail included, buses excluded).
RAIL_ROUTE_TYPES = {0, 1, 2, *range(100, 118), 400, 401, 402, 403, 404, 405}

DESTINATION_STOPS: dict[str, set[str]] = {
    "krakow": {"krakow glowny", "krakow plaszow"},
    "katowice": {"katowice"},
    "warszawa": {
        "warszawa centralna",
        "warszawa wschodnia",
        "warszawa zachodnia",
        "warszawa gdanska",
    },
    "wroclaw": {"wroclaw glowny", "wroclaw mikolajow"},
}

POI_CATEGORIES: dict[str, tuple[str, ...]] = {
    "grocery": ("supermarket", "convenience", "greengrocer", "bakery", "butcher", "deli"),
    "food": ("restaurant", "cafe", "fast_food"),
    "health": ("pharmacy", "clinic", "doctors"),
    "education": ("school", "kindergarten"),
    "leisure": ("park", "playground", "garden"),
    "transit": ("bus_stop", "station", "halt", "tram_stop"),
}


def cache_dir() -> Path:
    path = default_cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _overpass(query: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = httpx.post(
                endpoint,
                data={"data": query},
                headers={"user-agent": "plaszczaki-scoring-geodata/1.0"},
                timeout=360.0,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            logger.warning("overpass %s failed: %s", endpoint, exc)
    raise RuntimeError(f"all Overpass endpoints failed: {last_error}")


def _region_box(margin: float = 0.2) -> tuple[float, float, float, float]:
    regions = load_regions_config()
    boxes = [region.bbox for region in regions.regions.values()]
    return (
        min(box[0] for box in boxes) - margin,
        min(box[1] for box in boxes) - margin,
        max(box[2] for box in boxes) + margin,
        max(box[3] for box in boxes) + margin,
    )


# ---------------------------------------------------------------------------
# GTFS rail stations
# ---------------------------------------------------------------------------


def _service_weekly_factor(
    calendar_rows: list[dict[str, str]],
    exceptions: dict[str, list[tuple[date, int]]],
    window_start: date,
    window_days: int = 14,
) -> dict[str, float]:
    """Active days per service in the next two weeks, expressed per week."""
    window_end = window_start + timedelta(days=window_days - 1)
    factors: dict[str, float] = {}
    weekday_keys = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    for row in calendar_rows:
        service = row["service_id"]
        try:
            start = date(
                int(row["start_date"][:4]), int(row["start_date"][4:6]), int(row["start_date"][6:8])
            )
            end = date(
                int(row["end_date"][:4]), int(row["end_date"][4:6]), int(row["end_date"][6:8])
            )
        except ValueError, KeyError:
            continue
        days = {index for index, key in enumerate(weekday_keys) if row.get(key) == "1"}
        lo, hi = max(start, window_start), min(end, window_end)
        active = 0
        cursor = lo
        while cursor <= hi:
            if cursor.weekday() in days:
                active += 1
            cursor += timedelta(days=1)
        factors[service] = active / (window_days / 7)
    for service, entries in exceptions.items():
        added = sum(1 for day, kind in entries if kind == 1 and window_start <= day <= window_end)
        removed = sum(1 for day, kind in entries if kind == 2 and window_start <= day <= window_end)
        base = factors.get(service, 0.0)
        factors[service] = max(0.0, base + (added - removed) / (window_days / 7))
    return factors


def fetch_gtfs_stations(*, force: bool = False) -> dict[str, Any]:
    """Download the national rail GTFS and derive stations with target-city service."""
    target = cache_dir() / "gtfs"
    target.mkdir(parents=True, exist_ok=True)
    zip_path = target / "polish_trains.zip"
    if force or not zip_path.exists():
        logger.info("downloading GTFS: %s", GTFS_URL)
        with httpx.stream("GET", GTFS_URL, timeout=300.0, follow_redirects=True) as response:
            response.raise_for_status()
            with zip_path.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 16):
                    handle.write(chunk)

    box = _region_box()
    _STOP_NAME_CACHE.clear()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())

        def rows(name: str) -> list[dict[str, str]]:
            if name not in names:
                return []
            with archive.open(name) as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
                return list(csv.DictReader(text))

        stops_raw = rows("stops.txt")
        trips_raw = rows("trips.txt")
        routes_raw = rows("routes.txt")
        calendar_raw = rows("calendar.txt")
        exceptions_raw = rows("calendar_dates.txt")

    # Cluster stops by normalized name inside the region box; platforms of the
    # same station share a name and sit within ~1 km of each other.
    stop_cluster: dict[str, int] = {}
    clusters: list[dict[str, Any]] = []
    stop_target_city: dict[str, str] = {}
    for row in stops_raw:
        try:
            lat, lng = float(row["stop_lat"]), float(row["stop_lon"])
        except KeyError, ValueError:
            continue
        name = normalize_text(row.get("stop_name") or "")
        for city, needles in DESTINATION_STOPS.items():
            if name in needles:
                stop_target_city[row["stop_id"]] = city
        if not (box[0] <= lng <= box[2] and box[1] <= lat <= box[3]):
            continue
        key = name
        if key not in stop_cluster:
            stop_cluster[key] = len(clusters)
            clusters.append({"name": row.get("stop_name") or name, "lat": lat, "lng": lng, "n": 1})
        else:
            entry = clusters[stop_cluster[key]]
            entry["lat"] += (lat - entry["lat"]) / (entry["n"] + 1)
            entry["lng"] += (lng - entry["lng"]) / (entry["n"] + 1)
            entry["n"] += 1
    # Target-city stops outside the box still matter for trip qualification.
    for row in stops_raw:
        name = normalize_text(row.get("stop_name") or "")
        for city, needles in DESTINATION_STOPS.items():
            if name in needles:
                stop_target_city[row["stop_id"]] = city

    rail_routes = {
        row["route_id"]
        for row in routes_raw
        if _safe_int(row.get("route_type")) in RAIL_ROUTE_TYPES
    }
    exceptions: dict[str, list[tuple[date, int]]] = {}
    for row in exceptions_raw:
        try:
            day = date(int(row["date"][:4]), int(row["date"][4:6]), int(row["date"][6:8]))
            exceptions.setdefault(row["service_id"], []).append((day, int(row["exception_type"])))
        except KeyError, ValueError:
            continue
    factors = _service_weekly_factor(calendar_raw, exceptions, date.today())

    trip_info: dict[str, tuple[str, float]] = {}
    for row in trips_raw:
        if row.get("route_id") not in rail_routes:
            continue
        factor = factors.get(row.get("service_id", ""), 0.0)
        if factor > 0:
            trip_info[row["trip_id"]] = (row.get("service_id", ""), factor)

    # Pass 1: which trips touch a target-city stop, and which cities.
    trip_cities: dict[str, int] = {}
    city_bit = {city: 1 << index for index, city in enumerate(DESTINATION_STOPS)}
    with zipfile.ZipFile(zip_path) as archive, archive.open("stop_times.txt") as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text):
            trip_id = row.get("trip_id", "")
            if trip_id not in trip_info:
                continue
            city = stop_target_city.get(row.get("stop_id", ""))
            if city:
                trip_cities[trip_id] = trip_cities.get(trip_id, 0) | city_bit[city]

    # Pass 2: accumulate station -> city weekly trips (streaming, trips sorted).
    counts: list[dict[str, float]] = [{} for _ in clusters]
    current_trip: str | None = None
    current_mask = 0
    current_factor = 0.0
    seen: set[int] = set()
    unsorted = False
    done_trips: set[str] = set()

    def flush() -> None:
        if current_trip is None:
            return
        for cluster_index in seen:
            own_city = None
            for city, bit in city_bit.items():
                if current_mask & bit and _cluster_is_city(clusters[cluster_index], city):
                    own_city = city
            for city, bit in city_bit.items():
                if current_mask & bit and city != own_city:
                    entry = counts[cluster_index]
                    entry[city] = entry.get(city, 0.0) + current_factor

    with zipfile.ZipFile(zip_path) as archive, archive.open("stop_times.txt") as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text):
            trip_id = row.get("trip_id", "")
            mask = trip_cities.get(trip_id)
            if mask is None:
                continue
            if trip_id != current_trip:
                flush()
                if trip_id in done_trips:
                    unsorted = True
                    break
                done_trips.add(trip_id)
                current_trip = trip_id
                current_mask = mask
                current_factor = trip_info[trip_id][1]
                seen = set()
            cluster = stop_cluster.get(
                normalize_text(_stop_name(stops_raw, row.get("stop_id", "")))
            )
            if cluster is not None:
                seen.add(cluster)
        flush()

    if unsorted:
        logger.warning("stop_times not sorted by trip_id; falling back to dict accumulation")
        per_trip: dict[str, set[int]] = {}
        with zipfile.ZipFile(zip_path) as archive, archive.open("stop_times.txt") as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
            for row in csv.DictReader(text):
                trip_id = row.get("trip_id", "")
                if trip_id not in trip_cities:
                    continue
                cluster = stop_cluster.get(
                    normalize_text(_stop_name(stops_raw, row.get("stop_id", "")))
                )
                if cluster is not None:
                    per_trip.setdefault(trip_id, set()).add(cluster)
        counts = [{} for _ in clusters]
        for trip_id, cluster_set in per_trip.items():
            mask = trip_cities[trip_id]
            factor = trip_info[trip_id][1]
            for cluster_index in cluster_set:
                own_city = None
                for city, bit in city_bit.items():
                    if mask & bit and _cluster_is_city(clusters[cluster_index], city):
                        own_city = city
                for city, bit in city_bit.items():
                    if mask & bit and city != own_city:
                        entry = counts[cluster_index]
                        entry[city] = entry.get(city, 0.0) + factor

    stations: list[dict[str, Any]] = []
    for index, entry in enumerate(clusters):
        destinations = {city: round(value, 1) for city, value in counts[index].items() if value > 0}
        if not destinations:
            continue
        stations.append(
            {
                "name": entry["name"],
                "lat": round(entry["lat"], 5),
                "lng": round(entry["lng"], 5),
                "destinations": destinations,
                "weekly_trips": round(sum(destinations.values()), 1),
            }
        )
    stations.sort(key=lambda item: (-item["weekly_trips"], item["name"]))
    payload = {
        "source": GTFS_URL,
        "generated_at": int(time.time() * 1000),
        "window_start": date.today().isoformat(),
        "stations": stations,
    }
    _write_json(cache_dir() / "rail-stations.json", payload)
    return {"stations": len(stations), "output": str(cache_dir() / "rail-stations.json")}


_STOP_NAME_CACHE: dict[str, str] = {}


def _stop_name(stops_raw: list[dict[str, str]], stop_id: str) -> str:
    # stops_raw is scanned linearly only on cache miss; build the index once.
    if not _STOP_NAME_CACHE:
        for row in stops_raw:
            _STOP_NAME_CACHE[row["stop_id"]] = row.get("stop_name") or ""
    return _STOP_NAME_CACHE.get(stop_id, "")


def _cluster_is_city(cluster: dict[str, Any], city: str) -> bool:
    name = normalize_text(cluster["name"])
    return name in DESTINATION_STOPS[city]


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


# ---------------------------------------------------------------------------
# Nature polygons: BDOT10k (preferred) and OSM Overpass (fallback)
# ---------------------------------------------------------------------------


def _simplify_lnglat(
    points: list[tuple[float, float]], tolerance: float = 0.0008
) -> list[list[float]]:
    """Radial-distance simplification (~80 m) keeps scoring granularity."""
    ring: list[list[float]] = []
    for lng, lat in points:
        if not (math.isfinite(lng) and math.isfinite(lat)):
            continue
        if ring and abs(ring[-1][0] - lng) < tolerance and abs(ring[-1][1] - lat) < tolerance:
            continue
        ring.append([round(lng, 5), round(lat, 5)])
    if len(ring) > 2 and ring[0] == ring[-1]:
        ring.pop()
    return ring


def _simplify_ring(points: list[dict[str, float]], tolerance: float = 0.0008) -> list[list[float]]:
    return _simplify_lnglat([(point["lon"], point["lat"]) for point in points], tolerance)


def _project_ring(points: Any, transformer: Transformer) -> list[list[float]]:
    coordinates = [(float(point[0]), float(point[1])) for point in points if len(point) >= 2]
    if len(coordinates) < 3:
        return []
    xs = [point[0] for point in coordinates]
    ys = [point[1] for point in coordinates]
    lngs, lats = transformer.transform(xs, ys)
    return _simplify_lnglat(list(zip(lngs, lats, strict=True)))


def _geojson_polygon_rings(geometry: dict[str, Any]) -> Iterator[list[Any]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if kind == "Polygon" and coordinates:
        yield coordinates[0]
    elif kind == "MultiPolygon" and coordinates:
        for polygon in coordinates:
            if polygon:
                yield polygon[0]


def _shapefile_rings(shape: Any) -> Iterator[list[Any]]:
    points = shape.points
    parts = list(shape.parts)
    for index, start in enumerate(parts):
        end = parts[index + 1] if index + 1 < len(parts) else len(points)
        if end - start >= 3:
            yield points[start:end]


def _download(url: str, destination: Path, *, force: bool) -> None:
    if destination.exists() and not force:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    logger.info("downloading %s -> %s", url, destination)
    with httpx.stream(
        "GET", url, timeout=300.0, follow_redirects=True, headers=_geodata_headers()
    ) as response:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    temporary.replace(destination)


def _extract_remote_zip_member(url: str, member: str, destination: Path, *, force: bool) -> None:
    if destination.exists() and not force:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    logger.info("extracting %s!%s -> %s", url, member, destination)
    with (
        fsspec.open(url, "rb", block_size=1 << 20) as remote,
        zipfile.ZipFile(remote) as archive,
        archive.open(member) as source,
        temporary.open("wb") as handle,
    ):
        shutil.copyfileobj(source, handle, 1 << 20)
    temporary.replace(destination)


def _extract_zip_member(archive_path: Path, member: str, destination: Path, *, force: bool) -> None:
    if destination.exists() and not force:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with (
        zipfile.ZipFile(archive_path) as archive,
        archive.open(member) as source,
        temporary.open("wb") as handle,
    ):
        shutil.copyfileobj(source, handle, 1 << 20)
    temporary.replace(destination)


def _geodata_headers() -> dict[str, str]:
    return {"user-agent": "plaszczaki-scoring-geodata/1.0"}


def _read_flatgeobuf_rings(path: Path, transformer: Transformer) -> list[list[list[float]]]:
    rings: list[list[list[float]]] = []
    with path.open("rb") as handle:
        for feature in flatgeobuf.Reader(handle):
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict):
                continue
            for ring in _geojson_polygon_rings(geometry):
                projected = _project_ring(ring, transformer)
                if len(projected) >= 3:
                    rings.append(projected)
    return rings


def _read_shapefile_rings(base: Path, transformer: Transformer) -> list[list[list[float]]]:
    rings: list[list[list[float]]] = []
    with (
        base.with_suffix(".shp").open("rb") as shp,
        base.with_suffix(".shx").open("rb") as shx,
        base.with_suffix(".dbf").open("rb") as dbf,
    ):
        reader = shapefile.Reader(shp=shp, shx=shx, dbf=dbf)
        for shape in reader.iterShapes():
            for ring in _shapefile_rings(shape):
                projected = _project_ring(ring, transformer)
                if len(projected) >= 3:
                    rings.append(projected)
    return rings


def _fetch_bdot_nature(*, force: bool) -> tuple[list[list[list[float]]], list[list[list[float]]]]:
    root = cache_dir() / "bdot10k"
    root.mkdir(parents=True, exist_ok=True)
    transformer = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
    forest: list[list[list[float]]] = []
    water: list[list[list[float]]] = []

    malopolska_prefix = "12_BDOT10k_klasy_FlatGeoBuf/"
    for kind, filename in (("forest", "OT_PTLZ_A.fgb"), ("water", "OT_PTWP_A.fgb")):
        destination = root / f"malopolskie_{filename}"
        _extract_remote_zip_member(
            BDOT_MALOPOLSKA_URL,
            malopolska_prefix + filename,
            destination,
            force=force,
        )
        rings = _read_flatgeobuf_rings(destination, transformer)
        if kind == "forest":
            forest.extend(rings)
        else:
            water.extend(rings)

    slaskie_forest_zip = root / "slaskie_tereny_zielone.zip"
    slaskie_water_zip = root / "slaskie_siec_wodna.zip"
    _download(BDOT_SLASKIE_FOREST_URL, slaskie_forest_zip, force=force)
    _download(BDOT_SLASKIE_WATER_URL, slaskie_water_zip, force=force)
    for archive_path, members in (
        (
            slaskie_forest_zip,
            [("TERENY_ZIELONE_I_UPRAWY/lasy_zadrzewienia", "slaskie_lasy")],
        ),
        (
            slaskie_water_zip,
            [("SIEC_WODNA/obszary_wodne", "slaskie_wody")],
        ),
    ):
        for member, name in members:
            base = root / name
            for suffix in (".shp", ".shx", ".dbf"):
                _extract_zip_member(
                    archive_path,
                    member + suffix,
                    base.with_suffix(suffix),
                    force=force,
                )
            rings = _read_shapefile_rings(base, transformer)
            if name == "slaskie_lasy":
                forest.extend(rings)
            else:
                water.extend(rings)
    return forest, water


def _fetch_osm_nature() -> tuple[list[list[list[float]]], list[list[list[float]]]]:
    payload = _overpass(NATURE_QUERY)
    forest: list[list[list[float]]] = []
    water: list[list[list[float]]] = []
    for element in payload.get("elements", []):
        if element.get("type") != "way" or not element.get("geometry"):
            continue
        tags = element.get("tags", {})
        ring = _simplify_ring(element["geometry"])
        if len(ring) < 3:
            continue
        kind = tags.get("landuse") or tags.get("natural") or ""
        if kind in {"forest", "wood"}:
            forest.append(ring)
        elif kind in {"water", "reservoir"}:
            water.append(ring)
    return forest, water


def fetch_nature(*, force: bool = False) -> dict[str, Any]:
    path = cache_dir() / "nature.json"
    cached: dict[str, Any] | None = None
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not force and cached.get("source") == "BDOT10k":
            return {
                "source": "BDOT10k",
                "forest": len(cached["forest"]),
                "water": len(cached["water"]),
                "cached": True,
            }
    try:
        forest, water = _fetch_bdot_nature(force=force)
        if not forest and not water:
            raise RuntimeError("BDOT10k returned no forest/water polygons")
        source = "BDOT10k"
    except Exception:
        logger.warning("BDOT10k nature import failed; falling back to OSM", exc_info=True)
        if cached is not None and not force:
            return {
                "source": cached.get("source", "OSM"),
                "forest": len(cached["forest"]),
                "water": len(cached["water"]),
                "cached": True,
                "fallback": True,
            }
        forest, water = _fetch_osm_nature()
        source = "OSM"
    result = {
        "source": source,
        "generated_at": int(time.time() * 1000),
        "forest": forest,
        "water": water,
    }
    _write_json(path, result)
    return {
        "source": source,
        "forest": len(forest),
        "water": len(water),
        "output": str(path),
    }


def _poi_category(tags: dict[str, str]) -> str | None:
    for category, values in POI_CATEGORIES.items():
        for key in ("shop", "amenity", "leisure", "highway", "railway"):
            if tags.get(key) in values:
                return category
    return None


def fetch_poi(*, force: bool = False) -> dict[str, Any]:
    path = cache_dir() / "poi.json"
    if path.exists() and not force:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {"pois": len(payload["pois"]), "cached": True}
    payload = _overpass(POI_QUERY)
    pois: list[dict[str, Any]] = []
    for element in payload.get("elements", []):
        if element.get("type") != "node":
            continue
        category = _poi_category(element.get("tags", {}))
        if category is None:
            continue
        pois.append(
            {"lat": round(element["lat"], 5), "lng": round(element["lon"], 5), "cat": category}
        )
    result = {"generated_at": int(time.time() * 1000), "pois": pois}
    _write_json(path, result)
    return {"pois": len(pois), "output": str(path)}


def fetch_elevation(points: list[tuple[float, float]], *, rps: float = 1.0) -> dict[str, Any]:
    """Batch-query Open-Meteo elevation; cached per ~11 m grid cell."""
    path = cache_dir() / "elevation.json"
    cached: dict[str, float] = {}
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8")).get("points", {})
    missing: list[tuple[float, float]] = []
    seen: set[str] = set()
    for lat, lng in points:
        key = f"{lat:.4f},{lng:.4f}"
        if key not in cached and key not in seen:
            seen.add(key)
            missing.append((lat, lng))
    fetched = 0
    for start in range(0, len(missing), 100):
        batch = missing[start : start + 100]
        response = httpx.get(
            ELEVATION_URL,
            params={
                "latitude": ",".join(f"{lat:.4f}" for lat, _ in batch),
                "longitude": ",".join(f"{lng:.4f}" for _, lng in batch),
            },
            timeout=60.0,
        )
        response.raise_for_status()
        values = response.json().get("elevation", [])
        for (lat, lng), value in zip(batch, values, strict=False):
            if isinstance(value, int | float):
                cached[f"{lat:.4f},{lng:.4f}"] = float(value)
                fetched += 1
        time.sleep(1.0 / rps)
    _write_json(path, {"generated_at": int(time.time() * 1000), "points": cached})
    return {"cached": len(cached), "fetched": fetched, "output": str(path)}


# ---------------------------------------------------------------------------
# Spatial index + distance helpers used by scoring
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GridIndex:
    """Bucket items by 0.05° cells (~5 km) for radius queries."""

    cell: float = 0.05
    cells: dict[tuple[int, int], list[int]] = field(default_factory=dict)

    def add(
        self, index: int, min_lat: float, min_lng: float, max_lat: float, max_lng: float
    ) -> None:
        for cx in range(int(min_lng // self.cell), int(max_lng // self.cell) + 1):
            for cy in range(int(min_lat // self.cell), int(max_lat // self.cell) + 1):
                self.cells.setdefault((cx, cy), []).append(index)

    def query(self, lat: float, lng: float, radius_km: float) -> list[int]:
        span = max(1, math.ceil(radius_km / (self.cell * 111.0)))
        cx, cy = int(lng // self.cell), int(lat // self.cell)
        found: set[int] = set()
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                found.update(self.cells.get((cx + dx, cy + dy), ()))
        return sorted(found)


def point_in_ring(lat: float, lng: float, ring: list[list[float]]) -> bool:
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 > lat) != (y2 > lat):
            intersect_x = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lng < intersect_x:
                inside = not inside
    return inside


def ring_distance_km(lat: float, lng: float, ring: list[list[float]]) -> float:
    """Distance from a point to a polygon ring (0 when inside)."""
    min_x = min(point[0] for point in ring)
    max_x = max(point[0] for point in ring)
    min_y = min(point[1] for point in ring)
    max_y = max(point[1] for point in ring)
    if min_y <= lat <= max_y and min_x <= lng <= max_x and point_in_ring(lat, lng, ring):
        return 0.0
    # Local equirectangular projection around the query point.
    cos_lat = math.cos(math.radians(lat))
    px, py = 0.0, 0.0
    best = float("inf")
    count = len(ring)
    for index in range(count):
        x1 = (ring[index][0] - lng) * 111.32 * cos_lat
        y1 = (ring[index][1] - lat) * 110.57
        x2 = (ring[(index + 1) % count][0] - lng) * 111.32 * cos_lat
        y2 = (ring[(index + 1) % count][1] - lat) * 110.57
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            distance = math.hypot(px - x1, py - y1)
        else:
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
            distance = math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
        best = min(best, distance)
    return best


def segment_distance_km(
    lat: float, lng: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    cos_lat = math.cos(math.radians(lat))
    ax = (a[1] - lng) * 111.32 * cos_lat
    ay = (a[0] - lat) * 110.57
    bx = (b[1] - lng) * 111.32 * cos_lat
    by = (b[0] - lat) * 110.57
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
    return math.hypot(ax + t * dx, ay + t * dy)


@dataclass(slots=True)
class PlannedSegment:
    status: str
    stage: str | None
    target: str | None
    program: str | None
    project: str | None
    a: tuple[float, float]
    b: tuple[float, float]


@dataclass(slots=True)
class GeoContext:
    """Everything scoring needs; missing caches mean neutral signals."""

    stations: list[dict[str, Any]] = field(default_factory=list)
    station_index: GridIndex = field(default_factory=GridIndex)
    forest: list[list[list[float]]] = field(default_factory=list)
    forest_index: GridIndex = field(default_factory=GridIndex)
    water: list[list[list[float]]] = field(default_factory=list)
    water_index: GridIndex = field(default_factory=GridIndex)
    pois: list[dict[str, Any]] = field(default_factory=list)
    poi_index: GridIndex = field(default_factory=GridIndex)
    elevation: dict[str, float] = field(default_factory=dict)
    planned: list[PlannedSegment] = field(default_factory=list)
    planned_index: GridIndex = field(default_factory=GridIndex)

    @classmethod
    def load(cls, root: Path | None = None) -> GeoContext:
        base = root or repo_root()
        cache = cache_dir()
        context = cls()

        stations_path = cache / "rail-stations.json"
        if stations_path.exists():
            payload = json.loads(stations_path.read_text(encoding="utf-8"))
            context.stations = payload.get("stations", [])
            for index, station in enumerate(context.stations):
                context.station_index.add(
                    index, station["lat"], station["lng"], station["lat"], station["lng"]
                )

        nature_path = cache / "nature.json"
        if nature_path.exists():
            payload = json.loads(nature_path.read_text(encoding="utf-8"))
            context.forest = payload.get("forest", [])
            context.water = payload.get("water", [])
            for index, ring in enumerate(context.forest):
                xs = [point[0] for point in ring]
                ys = [point[1] for point in ring]
                context.forest_index.add(index, min(ys), min(xs), max(ys), max(xs))
            for index, ring in enumerate(context.water):
                xs = [point[0] for point in ring]
                ys = [point[1] for point in ring]
                context.water_index.add(index, min(ys), min(xs), max(ys), max(xs))

        poi_path = cache / "poi.json"
        if poi_path.exists():
            payload = json.loads(poi_path.read_text(encoding="utf-8"))
            context.pois = payload.get("pois", [])
            for index, poi in enumerate(context.pois):
                context.poi_index.add(index, poi["lat"], poi["lng"], poi["lat"], poi["lng"])

        elevation_path = cache / "elevation.json"
        if elevation_path.exists():
            payload = json.loads(elevation_path.read_text(encoding="utf-8"))
            context.elevation = payload.get("points", {})

        for name in ("planned-rail.geojson", "kolej-plus.geojson"):
            path = base / "web" / "public" / "data" / name
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            for feature in payload.get("features", []):
                properties = feature.get("properties", {})
                geometry = feature.get("geometry", {})
                status = properties.get("status") or "proposed"
                stage = properties.get("stage")
                target = properties.get("target")
                program = properties.get("program")
                project = properties.get("project")
                if geometry.get("type") == "LineString":
                    coordinates = geometry.get("coordinates", [])
                    for a, b in pairwise(coordinates):
                        segment = PlannedSegment(
                            status, stage, target, program, project, (a[1], a[0]), (b[1], b[0])
                        )
                        context.planned.append(segment)
                        context.planned_index.add(
                            len(context.planned) - 1,
                            min(a[1], b[1]),
                            min(a[0], b[0]),
                            max(a[1], b[1]),
                            max(a[0], b[0]),
                        )
                elif geometry.get("type") == "Point":
                    lng, lat = geometry["coordinates"]
                    segment = PlannedSegment(
                        "schematic", stage, target, program, project, (lat, lng), (lat, lng)
                    )
                    context.planned.append(segment)
                    context.planned_index.add(len(context.planned) - 1, lat, lng, lat, lng)
        return context

    # -- queries ----------------------------------------------------------

    def nearest_station(
        self,
        lat: float,
        lng: float,
        radius_km: float = 15.0,
        destinations: set[str] | None = None,
    ) -> tuple[dict[str, Any], float] | None:
        best: tuple[dict[str, Any], float] | None = None
        for index in self.station_index.query(lat, lng, radius_km):
            station = self.stations[index]
            if destinations and not destinations.intersection(station.get("destinations", {})):
                continue
            distance = haversine_km(lat, lng, station["lat"], station["lng"])
            if distance <= radius_km and (best is None or distance < best[1]):
                best = (station, distance)
        return best

    def nearest_transit(
        self,
        lat: float,
        lng: float,
        radius_km: float = 3.0,
    ) -> tuple[dict[str, Any], float] | None:
        """Nearest OSM bus/tram/rail stop within ``radius_km``."""
        best: tuple[dict[str, Any], float] | None = None
        for index in self.poi_index.query(lat, lng, radius_km):
            poi = self.pois[index]
            if poi.get("cat") != "transit":
                continue
            distance = haversine_km(lat, lng, poi["lat"], poi["lng"])
            if distance <= radius_km and (best is None or distance < best[1]):
                best = (poi, distance)
        return best

    def nature_distance_km(
        self, lat: float, lng: float, kind: str, radius_km: float = 3.0
    ) -> float | None:
        rings = self.forest if kind == "forest" else self.water
        index = self.forest_index if kind == "forest" else self.water_index
        best: float | None = None
        for ring_index in index.query(lat, lng, radius_km):
            distance = ring_distance_km(lat, lng, rings[ring_index])
            if distance <= radius_km and (best is None or distance < best):
                best = distance
        return best

    def planned_rail_bonus(
        self, lat: float, lng: float, config: dict[str, Any], radius_km: float = 3.0
    ) -> tuple[float, str | None]:
        points_map = {
            "construction": config.get("construction_points", 8),
            "proposed": config.get("proposed_points", 4),
            "schematic": config.get("schematic_points", 2),
            "existing": 0,
        }
        best = 0.0
        label: str | None = None
        for index in self.planned_index.query(lat, lng, radius_km):
            segment = self.planned[index]
            distance = segment_distance_km(lat, lng, segment.a, segment.b)
            if distance > radius_km:
                continue
            points = points_map.get(segment.status, 0)
            if segment.status == "existing":
                continue
            decay = max(0.0, 1.0 - distance / radius_km)
            value = points * (0.5 + 0.5 * decay)
            if value > best:
                best = value
                label = segment.project or segment.program or segment.status
        return round(best, 1), label

    def walkability(self, lat: float, lng: float, radius_km: float = 1.0) -> float:
        weights = {"grocery": 3, "food": 3, "health": 2, "education": 2, "leisure": 2, "transit": 2}
        caps = {"grocery": 2, "food": 10, "health": 5, "education": 5, "leisure": 5, "transit": 10}
        decayed: dict[str, float] = {}
        for index in self.poi_index.query(lat, lng, radius_km):
            poi = self.pois[index]
            distance = haversine_km(lat, lng, poi["lat"], poi["lng"])
            if distance > radius_km:
                continue
            category = poi["cat"]
            decayed[category] = decayed.get(category, 0.0) + max(0.0, 1.0 - distance / radius_km)
        raw = sum(weights[cat] * min(decayed.get(cat, 0.0), caps[cat]) for cat in weights)
        maximum = sum(weights[cat] * caps[cat] for cat in weights)
        return round(100.0 * raw / maximum, 1)

    def elevation_m(self, lat: float, lng: float) -> float | None:
        return self.elevation.get(f"{lat:.4f},{lng:.4f}")

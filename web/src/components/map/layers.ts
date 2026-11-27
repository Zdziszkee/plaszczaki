import type {
	ExpressionSpecification,
	FilterSpecification,
	GeoJSONSource,
	Map as MapboxMap,
} from "mapbox-gl";
import type { RcnFeatureCollection } from "#/lib/rcn-types";
import {
	COLORS,
	DETAIL_3D_BUILDINGS_MINZOOM,
	DETAIL_3D_LANDMARKS_MINZOOM,
	DETAIL_3D_TREES_MINZOOM,
	LAYER_IDS,
	type LayerToggles,
	LISTING_PIN_ICON,
	MODELS_MINZOOM,
	POI_HEATMAP_MAXZOOM,
	POI_HEATMAP_MINZOOM,
	RAIL_STATION_MINZOOM,
	SERVICE_RAIL_MINZOOM,
	TRAFFIC_MINZOOM,
	TRAM_MINZOOM,
	TRAM_STOP_MINZOOM,
} from "./constants";
import type { ListingFeatureCollection, ListingsMode } from "./listings-types";

/** One vector source serves rail, boundaries and the POI heatmap. */
const STREETS_SOURCE = "streets";

const PLANNED_RAIL_SOURCE = "planned-rail";
/** Committed OSM snapshot, regenerated with `bun run data:planned-rail`. */
const PLANNED_RAIL_URL = "/data/planned-rail.geojson";
const PLANNED_RAIL_ATTRIBUTION =
	'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> (ODbL)';

const KOLEJ_PLUS_SOURCE = "kolej-plus";
/** Committed PKP PLK Kolej Plus snapshot (`bun run data:kolej-plus`). */
const KOLEJ_PLUS_URL = "/data/kolej-plus.geojson";
const KOLEJ_PLUS_ATTRIBUTION = PLANNED_RAIL_ATTRIBUTION;

/** Line layers that should show the project popup and pointer cursor. */
export const PLANNED_RAIL_LINE_LAYERS = [
	LAYER_IDS.plannedRailConstruction,
	LAYER_IDS.plannedRailProposed,
	LAYER_IDS.portPolskaLine,
	LAYER_IDS.kolejPlusLine,
	LAYER_IDS.kolejPlusPoint,
] as const;

const MODEL_ID = "wind-turbine";
const WIND_FARM_SOURCE = "wind-farm";
const TRAFFIC_SOURCE = "traffic";
export const LISTINGS_SOURCE = "listings";
export const LISTINGS_AGG_SOURCE = "listings-aggregate";
export const RCN_SOURCE = "rcn-transactions";

/** Illustrative turbine positions inside the Margonin wind farm area. */
const TURBINE_SITES: Array<[number, number]> = [
	[17.075, 52.968],
	[17.095, 52.972],
	[17.115, 52.966],
	[17.085, 52.955],
	[17.105, 52.95],
];

const RAIL_MODES = [
	"rail",
	"metro_rail",
	"light_rail",
	"monorail",
	"funicular",
];

function ensureStreetsSource(map: MapboxMap): void {
	if (!map.getSource(STREETS_SOURCE)) {
		map.addSource(STREETS_SOURCE, {
			type: "vector",
			url: "mapbox://mapbox.mapbox-streets-v8",
		});
	}
}

function removeLayer(map: MapboxMap, id: string): void {
	if (map.getLayer(id)) map.removeLayer(id);
}

function removeSource(map: MapboxMap, id: string): void {
	if (map.getSource(id)) map.removeSource(id);
}

function setVisibility(
	map: MapboxMap,
	layerId: string,
	visible: boolean,
): void {
	if (!map.getLayer(layerId)) return;
	map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
}

/**
 * Terrain + hillshade. Terrain is not part of the Standard fragment style, so
 * we add our own DEM sources in `style.load` (fires after every style load).
 * Exaggeration stays at the spec default of 1 — real-world scale.
 */
export function addTerrainLayers(map: MapboxMap): void {
	if (!map.getSource("mapbox-dem")) {
		map.addSource("mapbox-dem", {
			type: "raster-dem",
			url: "mapbox://mapbox.mapbox-terrain-dem-v1",
			tileSize: 512,
			maxzoom: 14,
		});
	}
	if (!map.getSource("mapbox-dem-hillshade")) {
		map.addSource("mapbox-dem-hillshade", {
			type: "raster-dem",
			url: "mapbox://mapbox.mapbox-terrain-dem-v1",
			tileSize: 512,
			maxzoom: 14,
		});
	}
	map.setTerrain({ source: "mapbox-dem", exaggeration: 1 });

	if (!map.getLayer(LAYER_IDS.hillshade)) {
		map.addLayer({
			id: LAYER_IDS.hillshade,
			type: "hillshade",
			source: "mapbox-dem-hillshade",
			slot: "bottom",
			paint: {
				"hillshade-exaggeration": 0.35,
				"hillshade-shadow-color": "#020617",
				"hillshade-highlight-color": "#475569",
				"hillshade-accent-color": "#0f172a",
				"hillshade-illumination-direction": 315,
				"hillshade-illumination-anchor": "viewport",
			},
		});
	}
}

/** Topographic contour lines + elevation labels from Mapbox Terrain v2. */
export function addContourLayers(map: MapboxMap): void {
	if (!map.getSource("terrain-contours")) {
		map.addSource("terrain-contours", {
			type: "vector",
			url: "mapbox://mapbox.mapbox-terrain-v2",
		});
	}

	if (!map.getLayer(LAYER_IDS.contourLine)) {
		map.addLayer({
			id: LAYER_IDS.contourLine,
			type: "line",
			source: "terrain-contours",
			"source-layer": "contour",
			minzoom: 10,
			layout: { "line-join": "round", "line-cap": "round" },
			paint: {
				"line-color": [
					"interpolate",
					["linear"],
					["get", "ele"],
					0,
					"#16324f",
					300,
					"#2f4f6f",
					800,
					"#5b7089",
					1500,
					"#94a3b8",
					2500,
					"#e2e8f0",
				],
				"line-width": ["case", [">=", ["get", "index"], 5], 1.6, 0.7],
				"line-opacity": 0.55,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.contourLabel)) {
		map.addLayer({
			id: LAYER_IDS.contourLabel,
			type: "symbol",
			source: "terrain-contours",
			"source-layer": "contour",
			minzoom: 12,
			layout: {
				"symbol-placement": "line",
				"text-field": ["concat", ["to-string", ["get", "ele"]], " m"],
				"text-size": 10,
				"text-max-angle": 30,
				"text-padding": 2,
			},
			paint: {
				"text-color": "#cbd5e1",
				"text-halo-color": "#020617",
				"text-halo-width": 1.2,
			},
		});
	}
}

/**
 * Railway, tram and stop overlays from Mapbox Streets v8. `road.class`
 * separates `major_rail` (mainline) from `minor_rail` (light rail + tram);
 * `transit_stop_label.mode` separates stations from tram stops.
 */
export function addRailLayers(map: MapboxMap): void {
	ensureStreetsSource(map);

	if (!map.getLayer(LAYER_IDS.railCasing)) {
		map.addLayer({
			id: LAYER_IDS.railCasing,
			type: "line",
			source: STREETS_SOURCE,
			"source-layer": "road",
			filter: ["==", ["get", "class"], "major_rail"],
			paint: {
				"line-color": COLORS.railCasing,
				"line-opacity": 0.7,
				"line-width": [
					"interpolate",
					["linear"],
					["zoom"],
					5,
					1.5,
					10,
					3,
					18,
					9,
				],
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.railMajor)) {
		map.addLayer({
			id: LAYER_IDS.railMajor,
			type: "line",
			source: STREETS_SOURCE,
			"source-layer": "road",
			filter: ["==", ["get", "class"], "major_rail"],
			paint: {
				"line-color": COLORS.rail,
				"line-width": [
					"interpolate",
					["linear"],
					["zoom"],
					5,
					0.7,
					10,
					1.5,
					18,
					5,
				],
				"line-opacity": 0.95,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.railService)) {
		map.addLayer({
			id: LAYER_IDS.railService,
			type: "line",
			source: STREETS_SOURCE,
			"source-layer": "road",
			filter: ["==", ["get", "class"], "service_rail"],
			minzoom: SERVICE_RAIL_MINZOOM,
			paint: {
				"line-color": COLORS.rail,
				"line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.7, 18, 2],
				"line-opacity": 0.35,
				"line-dasharray": [2, 1.5],
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.tramCasing)) {
		map.addLayer({
			id: LAYER_IDS.tramCasing,
			type: "line",
			source: STREETS_SOURCE,
			"source-layer": "road",
			filter: ["==", ["get", "class"], "minor_rail"],
			minzoom: TRAM_MINZOOM,
			paint: {
				"line-color": COLORS.tramCasing,
				"line-opacity": 0.7,
				"line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.5, 18, 7],
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.tramLine)) {
		map.addLayer({
			id: LAYER_IDS.tramLine,
			type: "line",
			source: STREETS_SOURCE,
			"source-layer": "road",
			filter: ["==", ["get", "class"], "minor_rail"],
			minzoom: TRAM_MINZOOM,
			paint: {
				"line-color": COLORS.tram,
				"line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.8, 18, 3.5],
				"line-opacity": 0.95,
				"line-dasharray": [3, 1.5],
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.railStation)) {
		map.addLayer({
			id: LAYER_IDS.railStation,
			type: "circle",
			source: STREETS_SOURCE,
			"source-layer": "transit_stop_label",
			filter: ["match", ["get", "mode"], RAIL_MODES, true, false],
			minzoom: RAIL_STATION_MINZOOM,
			paint: {
				"circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3, 16, 6.5],
				"circle-color": COLORS.station,
				"circle-stroke-color": COLORS.label,
				"circle-stroke-width": 1.4,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.tramStop)) {
		map.addLayer({
			id: LAYER_IDS.tramStop,
			type: "circle",
			source: STREETS_SOURCE,
			"source-layer": "transit_stop_label",
			filter: ["==", ["get", "mode"], "tram"],
			minzoom: TRAM_STOP_MINZOOM,
			paint: {
				"circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2.5, 16, 5],
				"circle-color": COLORS.tramStop,
				"circle-stroke-color": COLORS.label,
				"circle-stroke-width": 1.2,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.railStationLabel)) {
		map.addLayer({
			id: LAYER_IDS.railStationLabel,
			type: "symbol",
			source: STREETS_SOURCE,
			"source-layer": "transit_stop_label",
			filter: ["match", ["get", "mode"], RAIL_MODES, true, false],
			minzoom: 11,
			layout: {
				"text-field": ["coalesce", ["get", "name"], ["get", "name_en"], ""],
				"text-size": 11,
				"text-offset": [0, 1],
				"text-anchor": "top",
				"text-allow-overlap": false,
			},
			paint: {
				"text-color": COLORS.station,
				"text-halo-color": COLORS.labelHalo,
				"text-halo-width": 1.4,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.tramStopLabel)) {
		map.addLayer({
			id: LAYER_IDS.tramStopLabel,
			type: "symbol",
			source: STREETS_SOURCE,
			"source-layer": "transit_stop_label",
			filter: ["==", ["get", "mode"], "tram"],
			minzoom: 13,
			layout: {
				"text-field": ["coalesce", ["get", "name"], ["get", "name_en"], ""],
				"text-size": 10,
				"text-offset": [0, 0.9],
				"text-anchor": "top",
				"text-allow-overlap": false,
			},
			paint: {
				"text-color": COLORS.tramStop,
				"text-halo-color": COLORS.labelHalo,
				"text-halo-width": 1.3,
			},
		});
	}
}

/**
 * Planned and under-construction heavy rail from the committed OSM snapshot.
 * Line colour separates projects already being built from proposals; both are
 * dashed/solid variants of the same magenta family so they never read as the
 * orange existing network.
 */
export function addPlannedRailLayers(map: MapboxMap): void {
	if (!map.getSource(PLANNED_RAIL_SOURCE)) {
		map.addSource(PLANNED_RAIL_SOURCE, {
			type: "geojson",
			data: PLANNED_RAIL_URL,
			attribution: PLANNED_RAIL_ATTRIBUTION,
		});
	}

	const casingWidth: ExpressionSpecification = [
		"interpolate",
		["linear"],
		["zoom"],
		5,
		2.8,
		10,
		4.8,
		14,
		8,
		18,
		13,
	];
	const lineWidth: ExpressionSpecification = [
		"interpolate",
		["linear"],
		["zoom"],
		5,
		1.5,
		10,
		2.8,
		14,
		5,
		18,
		9,
	];

	if (!map.getLayer(LAYER_IDS.plannedRailCasingConstruction)) {
		map.addLayer({
			id: LAYER_IDS.plannedRailCasingConstruction,
			type: "line",
			source: PLANNED_RAIL_SOURCE,
			filter: [
				"all",
				["==", ["get", "status"], "construction"],
				["!=", ["coalesce", ["get", "program"], ""], "port-polska"],
				["!=", ["coalesce", ["get", "program"], ""], "kolej-plus"],
			],
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.plannedCasing,
				"line-width": casingWidth,
				"line-opacity": 0.75,
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.plannedRailConstruction)) {
		map.addLayer({
			id: LAYER_IDS.plannedRailConstruction,
			type: "line",
			source: PLANNED_RAIL_SOURCE,
			filter: [
				"all",
				["==", ["get", "status"], "construction"],
				["!=", ["coalesce", ["get", "program"], ""], "port-polska"],
				["!=", ["coalesce", ["get", "program"], ""], "kolej-plus"],
			],
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.plannedConstruction,
				"line-width": lineWidth,
				"line-opacity": 0.95,
				// Standard's night lighting darkens custom layers; keep the
				// planned network readable without going full neon.
				"line-emissive-strength": 1,
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.plannedRailCasingProposed)) {
		map.addLayer({
			id: LAYER_IDS.plannedRailCasingProposed,
			type: "line",
			source: PLANNED_RAIL_SOURCE,
			filter: [
				"all",
				["==", ["get", "status"], "proposed"],
				["!=", ["coalesce", ["get", "program"], ""], "port-polska"],
				["!=", ["coalesce", ["get", "program"], ""], "kolej-plus"],
			],
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.plannedCasing,
				"line-width": casingWidth,
				"line-opacity": 0.55,
				"line-dasharray": [3, 2],
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.plannedRailProposed)) {
		map.addLayer({
			id: LAYER_IDS.plannedRailProposed,
			type: "line",
			source: PLANNED_RAIL_SOURCE,
			filter: [
				"all",
				["==", ["get", "status"], "proposed"],
				["!=", ["coalesce", ["get", "program"], ""], "port-polska"],
				["!=", ["coalesce", ["get", "program"], ""], "kolej-plus"],
			],
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.plannedProposed,
				"line-width": lineWidth,
				"line-opacity": 0.95,
				"line-dasharray": [3, 2],
				"line-emissive-strength": 1,
			},
			slot: "middle",
		});
	}

	const portCasingWidth: ExpressionSpecification = [
		"interpolate",
		["linear"],
		["zoom"],
		5,
		3.2,
		10,
		5.6,
		14,
		9,
		18,
		14,
	];
	const portLineWidth: ExpressionSpecification = [
		"interpolate",
		["linear"],
		["zoom"],
		5,
		1.8,
		10,
		3.4,
		14,
		6,
		18,
		10,
	];

	if (!map.getLayer(LAYER_IDS.portPolskaCasing)) {
		map.addLayer({
			id: LAYER_IDS.portPolskaCasing,
			type: "line",
			source: PLANNED_RAIL_SOURCE,
			filter: ["==", ["get", "program"], "port-polska"],
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.plannedCasing,
				"line-width": portCasingWidth,
				"line-opacity": 0.75,
				"line-dasharray": [4, 1.5],
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.portPolskaLine)) {
		map.addLayer({
			id: LAYER_IDS.portPolskaLine,
			type: "line",
			source: PLANNED_RAIL_SOURCE,
			filter: ["==", ["get", "program"], "port-polska"],
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.portPolska,
				"line-width": portLineWidth,
				"line-opacity": 0.95,
				"line-dasharray": [4, 1.5],
				"line-emissive-strength": 1,
			},
			slot: "middle",
		});
	}
}

/**
 * Current Kolej Plus projects (PKP PLK): new lines plus modernisations of
 * existing tracks. Drawn in lime so they stay distinct from generic proposals,
 * Port Polska and the existing network.
 */
export function addKolejPlusLayers(map: MapboxMap): void {
	if (!map.getSource(KOLEJ_PLUS_SOURCE)) {
		map.addSource(KOLEJ_PLUS_SOURCE, {
			type: "geojson",
			data: KOLEJ_PLUS_URL,
			attribution: KOLEJ_PLUS_ATTRIBUTION,
		});
	}

	const casingWidth: ExpressionSpecification = [
		"interpolate",
		["linear"],
		["zoom"],
		5,
		3,
		10,
		5,
		14,
		8.5,
		18,
		13,
	];
	const lineWidth: ExpressionSpecification = [
		"interpolate",
		["linear"],
		["zoom"],
		5,
		1.7,
		10,
		3,
		14,
		5.4,
		18,
		9.5,
	];

	if (!map.getLayer(LAYER_IDS.kolejPlusCasing)) {
		map.addLayer({
			id: LAYER_IDS.kolejPlusCasing,
			type: "line",
			source: KOLEJ_PLUS_SOURCE,
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.plannedCasing,
				"line-width": casingWidth,
				"line-opacity": 0.75,
				"line-dasharray": [4, 1.5],
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.kolejPlusLine)) {
		map.addLayer({
			id: LAYER_IDS.kolejPlusLine,
			type: "line",
			source: KOLEJ_PLUS_SOURCE,
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.kolejPlus,
				"line-width": lineWidth,
				"line-opacity": 0.95,
				"line-dasharray": [4, 1.5],
				"line-emissive-strength": 1,
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.kolejPlusPoint)) {
		map.addLayer({
			id: LAYER_IDS.kolejPlusPoint,
			type: "circle",
			source: KOLEJ_PLUS_SOURCE,
			filter: ["==", ["geometry-type"], "Point"],
			paint: {
				"circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 5, 12, 7],
				"circle-color": COLORS.kolejPlus,
				"circle-stroke-color": COLORS.labelHalo,
				"circle-stroke-width": 1.5,
				"circle-emissive-strength": 1,
			},
		});
	}
}

/**
 * Voivodeship and powiat borders from Streets v8. `worldview` must be part of
 * the filter or the tileset returns duplicate boundary geometry.
 */
export function addBoundaryLayers(map: MapboxMap): void {
	ensureStreetsSource(map);

	const worldview: ExpressionSpecification = [
		"any",
		["==", ["get", "worldview"], "all"],
		["==", ["get", "worldview"], "US"],
	];

	if (!map.getLayer(LAYER_IDS.admin1)) {
		map.addLayer({
			id: LAYER_IDS.admin1,
			type: "line",
			source: STREETS_SOURCE,
			"source-layer": "admin",
			filter: [
				"all",
				["==", ["get", "admin_level"], 1],
				["==", ["get", "maritime"], "false"],
				worldview,
			],
			minzoom: 4,
			paint: {
				"line-color": COLORS.boundary,
				"line-width": [
					"interpolate",
					["linear"],
					["zoom"],
					4,
					0.6,
					8,
					1.2,
					14,
					2.2,
				],
				"line-opacity": 0.55,
				"line-dasharray": [3, 2],
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.admin2)) {
		map.addLayer({
			id: LAYER_IDS.admin2,
			type: "line",
			source: STREETS_SOURCE,
			"source-layer": "admin",
			filter: [
				"all",
				["==", ["get", "admin_level"], 2],
				["==", ["get", "maritime"], "false"],
				worldview,
			],
			minzoom: 7,
			paint: {
				"line-color": COLORS.boundaryMinor,
				"line-width": [
					"interpolate",
					["linear"],
					["zoom"],
					7,
					0.5,
					12,
					1.1,
					16,
					1.6,
				],
				"line-opacity": 0.32,
				"line-dasharray": [1.5, 2],
			},
			slot: "middle",
		});
	}
}

/** POI density heatmap; fades out as street-level detail takes over. */
export function addHeatmapLayers(map: MapboxMap): void {
	ensureStreetsSource(map);

	if (map.getLayer(LAYER_IDS.poiHeat)) return;

	map.addLayer({
		id: LAYER_IDS.poiHeat,
		type: "heatmap",
		source: STREETS_SOURCE,
		"source-layer": "poi_label",
		minzoom: POI_HEATMAP_MINZOOM,
		maxzoom: POI_HEATMAP_MAXZOOM,
		paint: {
			"heatmap-weight": [
				"interpolate",
				["linear"],
				["get", "filterrank"],
				0,
				1,
				5,
				0.25,
			],
			"heatmap-intensity": [
				"interpolate",
				["linear"],
				["zoom"],
				POI_HEATMAP_MINZOOM,
				0.7,
				10,
				1.5,
			],
			"heatmap-radius": [
				"interpolate",
				["linear"],
				["zoom"],
				5,
				6,
				10,
				18,
				13,
				30,
			],
			"heatmap-opacity": [
				"interpolate",
				["linear"],
				["zoom"],
				11.5,
				0.8,
				13,
				0,
			],
			"heatmap-color": [
				"interpolate",
				["linear"],
				["heatmap-density"],
				0,
				"rgba(2,6,23,0)",
				0.2,
				"#0e7490",
				0.45,
				"#38bdf8",
				0.7,
				"#a855f7",
				0.9,
				"#f472b6",
				1,
				"#fde68a",
			],
		},
		slot: "middle",
	});
}

/** Real-time traffic on major roads; added only while the toggle is on. */
export function syncTraffic(map: MapboxMap, enabled: boolean): void {
	if (!enabled) {
		removeLayer(map, LAYER_IDS.trafficClosed);
		removeLayer(map, LAYER_IDS.trafficLine);
		removeLayer(map, LAYER_IDS.trafficCasing);
		removeSource(map, TRAFFIC_SOURCE);
		return;
	}

	if (!map.getSource(TRAFFIC_SOURCE)) {
		map.addSource(TRAFFIC_SOURCE, {
			type: "vector",
			url: "mapbox://mapbox.mapbox-traffic-v1",
		});
	}

	const majorRoads: FilterSpecification = [
		"match",
		["get", "class"],
		[
			"motorway",
			"motorway_link",
			"trunk",
			"trunk_link",
			"primary",
			"primary_link",
			"secondary",
			"tertiary",
		],
		true,
		false,
	];

	const width: ExpressionSpecification = [
		"interpolate",
		["linear"],
		["zoom"],
		6,
		0.8,
		10,
		2.2,
		14,
		4.5,
		18,
		9,
	];

	if (!map.getLayer(LAYER_IDS.trafficCasing)) {
		map.addLayer({
			id: LAYER_IDS.trafficCasing,
			type: "line",
			source: TRAFFIC_SOURCE,
			"source-layer": "traffic",
			filter: majorRoads,
			minzoom: TRAFFIC_MINZOOM,
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": COLORS.labelHalo,
				"line-width": width,
				"line-opacity": 0.65,
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.trafficLine)) {
		map.addLayer({
			id: LAYER_IDS.trafficLine,
			type: "line",
			source: TRAFFIC_SOURCE,
			"source-layer": "traffic",
			filter: majorRoads,
			minzoom: TRAFFIC_MINZOOM,
			layout: { "line-cap": "round", "line-join": "round" },
			paint: {
				"line-color": [
					"match",
					["get", "congestion"],
					"low",
					COLORS.trafficLow,
					"moderate",
					COLORS.trafficModerate,
					"heavy",
					COLORS.trafficHeavy,
					"severe",
					COLORS.trafficSevere,
					"#64748b",
				],
				"line-width": width,
				"line-opacity": 0.9,
			},
			slot: "middle",
		});
	}

	if (!map.getLayer(LAYER_IDS.trafficClosed)) {
		map.addLayer({
			id: LAYER_IDS.trafficClosed,
			type: "line",
			source: TRAFFIC_SOURCE,
			"source-layer": "traffic",
			filter: ["==", ["get", "closed"], "yes"],
			minzoom: TRAFFIC_MINZOOM,
			paint: {
				"line-color": COLORS.trafficClosed,
				"line-width": width,
				"line-opacity": 0.9,
				"line-dasharray": [2, 1.5],
			},
			slot: "middle",
		});
	}
}

/** 3D glTF turbines placed on a GeoJSON source. */
export function syncModels(map: MapboxMap, enabled: boolean): void {
	if (!enabled) {
		removeLayer(map, LAYER_IDS.turbineModels);
		removeSource(map, WIND_FARM_SOURCE);
		if (map.hasModel(MODEL_ID)) map.removeModel(MODEL_ID);
		return;
	}

	if (!map.isStyleLoaded()) {
		map.once("idle", () => syncModels(map, true));
		return;
	}

	if (!map.hasModel(MODEL_ID)) {
		map.addModel(MODEL_ID, "/models/wind-turbine.glb");
	}

	if (!map.getSource(WIND_FARM_SOURCE)) {
		map.addSource(WIND_FARM_SOURCE, {
			type: "geojson",
			data: {
				type: "FeatureCollection",
				features: TURBINE_SITES.map(([lng, lat], index) => ({
					type: "Feature",
					id: index,
					properties: { name: `Turbina ${index + 1}` },
					geometry: { type: "Point", coordinates: [lng, lat] },
				})),
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.turbineModels)) {
		map.addLayer({
			id: LAYER_IDS.turbineModels,
			type: "model",
			source: WIND_FARM_SOURCE,
			minzoom: MODELS_MINZOOM,
			layout: { "model-id": MODEL_ID },
			paint: {
				"model-scale": [1, 1, 1],
				"model-color": "#e2e8f0",
				"model-color-mix-intensity": 0.15,
				"model-cast-shadows": true,
			},
			slot: "middle",
		});
	}
}

function emptyListings(): ListingFeatureCollection {
	return { type: "FeatureCollection", features: [] };
}

/** Pin sprite dimensions in device pixels; drawn at 2x for crisp rendering. */
const LISTING_PIN_WIDTH = 48;
const LISTING_PIN_HEIGHT = 60;
const LISTING_PIN_PIXEL_RATIO = 2;

/**
 * Solid white SDF pin silhouette. The symbol layer tints it per feature with
 * `icon-color`, so one image serves every source. Mapbox keeps images when a
 * layer is removed, so the `hasImage` guard is all the lifetime management it
 * needs.
 */
function ensureListingPinIcon(map: MapboxMap): void {
	if (map.hasImage(LISTING_PIN_ICON)) return;

	const canvas = document.createElement("canvas");
	canvas.width = LISTING_PIN_WIDTH;
	canvas.height = LISTING_PIN_HEIGHT;

	const ctx = canvas.getContext("2d");
	if (!ctx) return;

	// Teardrop: circular head centred at (24, 21) with r=13, then two bezier
	// curves tapering down to the tip at (24, 54). Edges stay transparent.
	ctx.beginPath();
	ctx.moveTo(24, 54);
	ctx.bezierCurveTo(19, 42, 11, 33, 11, 21);
	ctx.arc(24, 21, 13, Math.PI, 0, false);
	ctx.bezierCurveTo(37, 33, 29, 42, 24, 54);
	ctx.closePath();
	ctx.fillStyle = "#fff";
	ctx.fill();

	map.addImage(
		LISTING_PIN_ICON,
		ctx.getImageData(0, 0, LISTING_PIN_WIDTH, LISTING_PIN_HEIGHT),
		{ sdf: true, pixelRatio: LISTING_PIN_PIXEL_RATIO },
	);
}

function removeListingPoints(map: MapboxMap): void {
	removeLayer(map, LAYER_IDS.listingScoreLabel);
	removeLayer(map, LAYER_IDS.listingPoint);
	removeLayer(map, LAYER_IDS.listingClusterCount);
	removeLayer(map, LAYER_IDS.listingClusters);
	removeSource(map, LISTINGS_SOURCE);
}

function removeListingAggregate(map: MapboxMap): void {
	removeLayer(map, LAYER_IDS.listingAggregateCount);
	removeLayer(map, LAYER_IDS.listingAggregate);
	removeSource(map, LISTINGS_AGG_SOURCE);
}

/** Individual listings, clustered by Mapbox at high zoom. */
export function syncListingPoints(
	map: MapboxMap,
	data?: ListingFeatureCollection,
): void {
	const existing = map.getSource(LISTINGS_SOURCE) as GeoJSONSource | undefined;
	if (!existing) {
		map.addSource(LISTINGS_SOURCE, {
			type: "geojson",
			data: data ?? emptyListings(),
			cluster: true,
			clusterMaxZoom: 13,
			clusterRadius: 48,
		});
	} else if (data) {
		existing.setData(data);
	}

	if (!map.getLayer(LAYER_IDS.listingClusters)) {
		map.addLayer({
			id: LAYER_IDS.listingClusters,
			type: "circle",
			source: LISTINGS_SOURCE,
			filter: ["has", "point_count"],
			paint: {
				"circle-color": COLORS.listingCluster,
				"circle-radius": ["step", ["get", "point_count"], 12, 10, 16, 50, 21],
				"circle-opacity": 0.92,
				"circle-stroke-color": COLORS.label,
				"circle-stroke-width": 1.2,
				"circle-stroke-opacity": 0.55,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.listingClusterCount)) {
		map.addLayer({
			id: LAYER_IDS.listingClusterCount,
			type: "symbol",
			source: LISTINGS_SOURCE,
			filter: ["has", "point_count"],
			layout: {
				"text-field": ["get", "point_count_abbreviated"],
				"text-size": 11,
				"text-allow-overlap": true,
			},
			paint: {
				"text-color": COLORS.label,
				"text-halo-color": COLORS.labelHalo,
				"text-halo-width": 1,
			},
		});
	}

	ensureListingPinIcon(map);

	if (!map.getLayer(LAYER_IDS.listingPoint)) {
		map.addLayer({
			id: LAYER_IDS.listingPoint,
			type: "symbol",
			source: LISTINGS_SOURCE,
			filter: ["!", ["has", "point_count"]],
			layout: {
				"icon-image": LISTING_PIN_ICON,
				"icon-anchor": "bottom",
				"icon-allow-overlap": true,
				"icon-ignore-placement": true,
				"icon-size": [
					"interpolate",
					["linear"],
					["zoom"],
					8,
					0.45,
					12,
					0.62,
					16,
					0.9,
				],
			},
			paint: {
				"icon-color": ["coalesce", ["get", "color"], COLORS.listingOther],
				"icon-halo-color": COLORS.labelHalo,
				"icon-halo-width": 1.4,
				"icon-halo-blur": 0.2,
				"icon-opacity": 0.95,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.listingScoreLabel)) {
		map.addLayer({
			id: LAYER_IDS.listingScoreLabel,
			type: "symbol",
			source: LISTINGS_SOURCE,
			filter: ["all", ["!", ["has", "point_count"]], ["has", "score"]],
			layout: {
				"text-field": ["to-string", ["get", "score"]],
				"text-size": ["interpolate", ["linear"], ["zoom"], 10, 8, 14, 10],
				"text-anchor": "center",
				"text-offset": [0, -0.85],
				"text-allow-overlap": true,
				"text-ignore-placement": true,
			},
			paint: {
				"text-color": COLORS.label,
				"text-halo-color": COLORS.labelHalo,
				"text-halo-width": 1.4,
			},
		});
	}
}

/** Low-zoom aggregate cells returned by `/api/listings/clusters`. */
export function syncListingAggregate(
	map: MapboxMap,
	data?: ListingFeatureCollection,
): void {
	const existing = map.getSource(LISTINGS_AGG_SOURCE) as
		| GeoJSONSource
		| undefined;
	if (!existing) {
		map.addSource(LISTINGS_AGG_SOURCE, {
			type: "geojson",
			data: data ?? emptyListings(),
		});
	} else if (data) {
		existing.setData(data);
	}

	if (!map.getLayer(LAYER_IDS.listingAggregate)) {
		map.addLayer({
			id: LAYER_IDS.listingAggregate,
			type: "circle",
			source: LISTINGS_AGG_SOURCE,
			paint: {
				"circle-color": [
					"step",
					["get", "count"],
					COLORS.listingOther,
					10,
					COLORS.listingSale,
					50,
					COLORS.listingRent,
					200,
					COLORS.listingAuction,
				],
				"circle-radius": ["step", ["get", "count"], 9, 10, 14, 50, 20, 200, 28],
				"circle-opacity": 0.82,
				"circle-stroke-color": COLORS.labelHalo,
				"circle-stroke-width": 1.5,
			},
		});
	}

	if (!map.getLayer(LAYER_IDS.listingAggregateCount)) {
		map.addLayer({
			id: LAYER_IDS.listingAggregateCount,
			type: "symbol",
			source: LISTINGS_AGG_SOURCE,
			layout: {
				"text-field": ["to-string", ["get", "count"]],
				"text-size": ["step", ["get", "count"], 10, 100, 11, 1000, 12],
				"text-allow-overlap": true,
			},
			paint: {
				"text-color": COLORS.label,
				"text-halo-color": COLORS.labelHalo,
				"text-halo-width": 1.5,
			},
		});
	}
}

function removeRcnLayers(map: MapboxMap): void {
	removeLayer(map, LAYER_IDS.rcnClusterCount);
	removeLayer(map, LAYER_IDS.rcnCluster);
	removeLayer(map, LAYER_IDS.rcnPoint);
	removeSource(map, RCN_SOURCE);
}

/** Render viewport RCN clusters or individual transaction points. */
export function syncRcnLayer(
	map: MapboxMap,
	enabled: boolean,
	mode: ListingsMode,
	data?: RcnFeatureCollection,
): void {
	if (!enabled) {
		removeRcnLayers(map);
		return;
	}
	removeRcnLayers(map);
	map.addSource(RCN_SOURCE, {
		type: "geojson",
		data: data ?? { type: "FeatureCollection", features: [] },
	});
	if (mode === "clusters") {
		map.addLayer({
			id: LAYER_IDS.rcnCluster,
			type: "circle",
			source: RCN_SOURCE,
			filter: ["has", "count"],
			paint: {
				"circle-color": "#f59e0b",
				"circle-opacity": 0.75,
				"circle-stroke-color": "#fef3c7",
				"circle-stroke-width": 1,
				"circle-radius": [
					"interpolate",
					["linear"],
					["get", "count"],
					1,
					5,
					50,
					10,
					500,
					18,
				],
			},
		});
		map.addLayer({
			id: LAYER_IDS.rcnClusterCount,
			type: "symbol",
			source: RCN_SOURCE,
			filter: ["has", "count"],
			layout: {
				"text-field": ["to-string", ["get", "count"]],
				"text-size": 10,
				"text-allow-overlap": true,
			},
			paint: {
				"text-color": "#111827",
				"text-halo-color": "#fef3c7",
				"text-halo-width": 1,
			},
		});
		return;
	}
	map.addLayer({
		id: LAYER_IDS.rcnPoint,
		type: "circle",
		source: RCN_SOURCE,
		paint: {
			"circle-color": [
				"match",
				["get", "objectKind"],
				"flat",
				"#38bdf8",
				"building",
				"#a78bfa",
				"plot",
				"#f59e0b",
				"#94a3b8",
			],
			"circle-opacity": 0.8,
			"circle-stroke-color": "#0f172a",
			"circle-stroke-width": 0.8,
			"circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3, 15, 6],
		},
	});
}

/** Switch between aggregate cells and individual listings. */
export function syncListingLayer(
	map: MapboxMap,
	enabled: boolean,
	mode: ListingsMode,
	data?: ListingFeatureCollection,
): void {
	if (!enabled) {
		removeListingPoints(map);
		removeListingAggregate(map);
		return;
	}

	if (mode === "clusters") {
		removeListingPoints(map);
		syncListingAggregate(map, data);
		return;
	}

	removeListingAggregate(map);
	syncListingPoints(map, data);
}

/** Enable/disable every custom layer and terrain to match the UI toggles. */
export function applyToggles(map: MapboxMap, toggles: LayerToggles): void {
	setVisibility(map, LAYER_IDS.railCasing, toggles.rail);
	setVisibility(map, LAYER_IDS.railMajor, toggles.rail);
	setVisibility(map, LAYER_IDS.railService, toggles.rail);

	setVisibility(
		map,
		LAYER_IDS.plannedRailCasingConstruction,
		toggles.plannedRail,
	);
	setVisibility(map, LAYER_IDS.plannedRailConstruction, toggles.plannedRail);
	setVisibility(map, LAYER_IDS.plannedRailCasingProposed, toggles.plannedRail);
	setVisibility(map, LAYER_IDS.plannedRailProposed, toggles.plannedRail);
	setVisibility(
		map,
		LAYER_IDS.portPolskaCasing,
		toggles.plannedRail && toggles.portPolska,
	);
	setVisibility(
		map,
		LAYER_IDS.portPolskaLine,
		toggles.plannedRail && toggles.portPolska,
	);
	setVisibility(
		map,
		LAYER_IDS.kolejPlusCasing,
		toggles.plannedRail && toggles.kolejPlus,
	);
	setVisibility(
		map,
		LAYER_IDS.kolejPlusLine,
		toggles.plannedRail && toggles.kolejPlus,
	);
	setVisibility(
		map,
		LAYER_IDS.kolejPlusPoint,
		toggles.plannedRail && toggles.kolejPlus,
	);

	setVisibility(map, LAYER_IDS.tramCasing, toggles.tram);
	setVisibility(map, LAYER_IDS.tramLine, toggles.tram);

	setVisibility(map, LAYER_IDS.railStation, toggles.stops);
	setVisibility(map, LAYER_IDS.tramStop, toggles.stops);
	setVisibility(map, LAYER_IDS.railStationLabel, toggles.stops);
	setVisibility(map, LAYER_IDS.tramStopLabel, toggles.stops);

	setVisibility(map, LAYER_IDS.admin1, toggles.boundaries);
	setVisibility(map, LAYER_IDS.admin2, toggles.boundaries);
	setVisibility(map, LAYER_IDS.poiHeat, toggles.heatmap);

	setVisibility(map, LAYER_IDS.listingClusters, toggles.listings);
	setVisibility(map, LAYER_IDS.listingClusterCount, toggles.listings);
	setVisibility(map, LAYER_IDS.listingPoint, toggles.listings);
	setVisibility(map, LAYER_IDS.listingScoreLabel, toggles.listings);
	setVisibility(map, LAYER_IDS.listingAggregate, toggles.listings);
	setVisibility(map, LAYER_IDS.listingAggregateCount, toggles.listings);
	setVisibility(map, LAYER_IDS.rcnCluster, toggles.rcn);
	setVisibility(map, LAYER_IDS.rcnClusterCount, toggles.rcn);
	setVisibility(map, LAYER_IDS.rcnPoint, toggles.rcn);

	setVisibility(map, LAYER_IDS.contourLine, toggles.contours);
	setVisibility(map, LAYER_IDS.contourLabel, toggles.contours);
	setVisibility(map, LAYER_IDS.hillshade, toggles.hillshade);

	syncTraffic(map, toggles.traffic);
	syncModels(map, toggles.models3d);

	if (map.getSource("mapbox-dem")) {
		map.setTerrain(
			toggles.terrain ? { source: "mapbox-dem", exaggeration: 1 } : null,
		);
	}

	sync3d(map, toggles.objects3d);
}

/** Zoom-gated 3D objects: models only render where they are worth the cost. */
export function sync3d(map: MapboxMap, enabled: boolean): void {
	const zoom = map.getZoom();
	const buildings = enabled && zoom >= DETAIL_3D_BUILDINGS_MINZOOM;
	const landmarks = enabled && zoom >= DETAIL_3D_LANDMARKS_MINZOOM;
	const trees = enabled && zoom >= DETAIL_3D_TREES_MINZOOM;

	const set = (property: string, value: boolean) => {
		try {
			map.setConfigProperty("basemap", property, value);
		} catch {
			// Older Standard style without that property: nothing to sync.
		}
	};

	set("show3dBuildings", buildings);
	set("show3dFacades", buildings);
	set("show3dLandmarks", landmarks);
	set("show3dTrees", trees);
	set("show3dObjects", buildings || landmarks || trees);
}

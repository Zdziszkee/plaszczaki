import type {
	ExpressionSpecification,
	FilterSpecification,
	Map as MapboxMap,
} from "mapbox-gl";

import {
	COLORS,
	DETAIL_3D_BUILDINGS_MINZOOM,
	DETAIL_3D_LANDMARKS_MINZOOM,
	DETAIL_3D_TREES_MINZOOM,
	LAYER_IDS,
	type LayerToggles,
	MODELS_MINZOOM,
	POI_HEATMAP_MAXZOOM,
	POI_HEATMAP_MINZOOM,
	RAIL_STATION_MINZOOM,
	SERVICE_RAIL_MINZOOM,
	TRAFFIC_MINZOOM,
	TRAM_MINZOOM,
	TRAM_STOP_MINZOOM,
} from "./constants";

/** One vector source serves rail, boundaries and the POI heatmap. */
const STREETS_SOURCE = "streets";

const MODEL_ID = "wind-turbine";
const WIND_FARM_SOURCE = "wind-farm";
const TRAFFIC_SOURCE = "traffic";

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

/** Enable/disable every custom layer and terrain to match the UI toggles. */
export function applyToggles(map: MapboxMap, toggles: LayerToggles): void {
	setVisibility(map, LAYER_IDS.railCasing, toggles.rail);
	setVisibility(map, LAYER_IDS.railMajor, toggles.rail);
	setVisibility(map, LAYER_IDS.railService, toggles.rail);

	setVisibility(map, LAYER_IDS.tramCasing, toggles.tram);
	setVisibility(map, LAYER_IDS.tramLine, toggles.tram);

	setVisibility(map, LAYER_IDS.railStation, toggles.stops);
	setVisibility(map, LAYER_IDS.tramStop, toggles.stops);
	setVisibility(map, LAYER_IDS.railStationLabel, toggles.stops);
	setVisibility(map, LAYER_IDS.tramStopLabel, toggles.stops);

	setVisibility(map, LAYER_IDS.admin1, toggles.boundaries);
	setVisibility(map, LAYER_IDS.admin2, toggles.boundaries);
	setVisibility(map, LAYER_IDS.poiHeat, toggles.heatmap);

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

/**
 * Map configuration shared by the /map route: Poland framing, night palette,
 * zoom thresholds and the layer-toggle shape persisted in localStorage.
 */

/** Extreme points of Poland, with a small margin. */
export const POLAND_BOUNDS: [[number, number], [number, number]] = [
	[14.05, 48.95],
	[24.2, 54.9],
];

/** Geometric middle of the country (near Piątek). */
export const POLAND_CENTER: [number, number] = [19.4, 51.95];

/** Hard camera limit: Poland plus enough slack to see the neighbours without
 * letting the user drift to another continent.
 */
export const POLAND_MAX_BOUNDS: [[number, number], [number, number]] = [
	[12.8, 48.0],
	[25.4, 55.8],
];
export const POLAND_MIN_ZOOM = 5;

/** Building/landmark model detail stays off until these zooms (perf). */
export const DETAIL_3D_LANDMARKS_MINZOOM = 12.5;
export const DETAIL_3D_BUILDINGS_MINZOOM = 13;
export const DETAIL_3D_TREES_MINZOOM = 14;

/** Tram and service tracks only read once the user is close enough. */
export const TRAM_MINZOOM = 10;
export const SERVICE_RAIL_MINZOOM = 13;
export const RAIL_STATION_MINZOOM = 9;
export const TRAM_STOP_MINZOOM = 11;

/** Traffic tiles only start at z6; POI heat reads best at country/region scale. */
export const TRAFFIC_MINZOOM = 6;
export const POI_HEATMAP_MINZOOM = 5;
export const POI_HEATMAP_MAXZOOM = 13;

/** 3D turbine models are tiny at country scale, so reveal them late. */
export const MODELS_MINZOOM = 12.5;

export const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN ?? "";

export const COLORS = {
	rail: "#fb923c",
	railCasing: "#020617",
	tram: "#38bdf8",
	tramCasing: "#020617",
	station: "#fbbf24",
	tramStop: "#7dd3fc",
	label: "#e2e8f0",
	labelHalo: "#020617",
	boundary: "#c4b5fd",
	boundaryMinor: "#8b5cf6",
	trafficLow: "#22c55e",
	trafficModerate: "#f59e0b",
	trafficHeavy: "#ef4444",
	trafficSevere: "#991b1b",
	trafficClosed: "#e2e8f0",
} as const;

export type LayerToggles = {
	rail: boolean;
	tram: boolean;
	stops: boolean;
	boundaries: boolean;
	traffic: boolean;
	heatmap: boolean;
	models3d: boolean;
	contours: boolean;
	hillshade: boolean;
	terrain: boolean;
	objects3d: boolean;
	elevation: boolean;
};

export const DEFAULT_TOGGLES: LayerToggles = {
	rail: true,
	tram: true,
	stops: true,
	boundaries: true,
	traffic: false,
	heatmap: false,
	models3d: false,
	contours: true,
	hillshade: true,
	terrain: true,
	objects3d: true,
	elevation: true,
};

export const TOGGLES_STORAGE_KEY = "plaszczaki:map:toggles";

export const LAYER_IDS = {
	railCasing: "rail-casing",
	railMajor: "rail-major",
	railService: "rail-service",
	tramCasing: "tram-casing",
	tramLine: "tram-line",
	railStation: "rail-station",
	tramStop: "tram-stop",
	railStationLabel: "rail-station-label",
	tramStopLabel: "tram-stop-label",
	contourLine: "contour-line",
	contourLabel: "contour-label",
	hillshade: "hillshade-dark",
	admin1: "admin-1",
	admin2: "admin-2",
	poiHeat: "poi-heat",
	trafficCasing: "traffic-casing",
	trafficLine: "traffic-line",
	trafficClosed: "traffic-closed",
	turbineModels: "turbine-models",
} as const;

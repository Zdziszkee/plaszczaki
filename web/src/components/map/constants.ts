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
	plannedConstruction: "#f472b6",
	plannedProposed: "#e879f9",
	plannedCasing: "#020617",
	portPolska: "#2dd4bf",
	kolejPlus: "#a3e635",
	tram: "#38bdf8",
	tramCasing: "#020617",
	station: "#fbbf24",
	tramStop: "#7dd3fc",
	listingSale: "#38bdf8",
	listingRent: "#fbbf24",
	listingAuction: "#f472b6",
	listingOther: "#94a3b8",
	listingAggregate: "#18181b",
	listingAggregateLabel: "#ffffff",
	rcnCluster: "#f59e0b",
	rcnClusterLabel: "#111827",
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
	plannedRail: boolean;
	portPolska: boolean;
	kolejPlus: boolean;
	tram: boolean;
	stops: boolean;
	boundaries: boolean;
	traffic: boolean;
	heatmap: boolean;
	listings: boolean;
	rcn: boolean;
	models3d: boolean;
	contours: boolean;
	hillshade: boolean;
	terrain: boolean;
	objects3d: boolean;
	elevation: boolean;
};

export const DEFAULT_TOGGLES: LayerToggles = {
	rail: true,
	plannedRail: true,
	portPolska: true,
	kolejPlus: true,
	tram: true,
	stops: true,
	boundaries: true,
	traffic: false,
	heatmap: false,
	listings: true,
	rcn: false,
	models3d: false,
	contours: true,
	hillshade: true,
	terrain: true,
	objects3d: true,
	elevation: true,
};

export const TOGGLES_STORAGE_KEY = "plaszczaki:map:toggles";
export const LISTINGS_FILTERS_STORAGE_KEY = "plaszczaki:map:listings-filters";
export const RCN_FILTERS_STORAGE_KEY = "plaszczaki:map:rcn-filters";
/** At this neighbourhood scale (about 1 km on the scale bar), show every offer. */
export const LISTINGS_POINTS_MIN_ZOOM = 14;
export const RCN_POINTS_MIN_ZOOM = 10;

export const LAYER_IDS = {
	railCasing: "rail-casing",
	railMajor: "rail-major",
	railService: "rail-service",
	plannedRailCasingConstruction: "planned-rail-casing-construction",
	plannedRailConstruction: "planned-rail-construction",
	plannedRailCasingProposed: "planned-rail-casing-proposed",
	plannedRailProposed: "planned-rail-proposed",
	portPolskaCasing: "port-polska-casing",
	portPolskaLine: "port-polska-line",
	kolejPlusCasing: "kolej-plus-casing",
	kolejPlusLine: "kolej-plus-line",
	kolejPlusPoint: "kolej-plus-point",
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
	listingPoint: "listing-point",
	listingScoreLabel: "listing-score-label",
	listingAggregateShadow: "listing-aggregate-shadow",
	listingAggregate: "listing-aggregate",
	listingAggregateCount: "listing-aggregate-count",
	rcnClusterShadow: "rcn-cluster-shadow",
	rcnCluster: "rcn-cluster",
	rcnClusterCount: "rcn-cluster-count",
	rcnPoint: "rcn-point",
	trafficCasing: "traffic-casing",
	trafficLine: "traffic-line",
	trafficClosed: "traffic-closed",
	turbineModels: "turbine-models",
} as const;

/**
 * Registered image id for the SDF pin drawn in `layers.ts`. Maps, not layer ids:
 * the same sprite is tinted per listing source through `icon-color`.
 */
export const LISTING_PIN_ICON = "listing-pin";

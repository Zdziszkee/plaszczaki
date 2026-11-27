export const RCN_OBJECT_KINDS = [
	"flat",
	"building",
	"plot",
	"premises",
] as const;
export type RcnObjectKind = (typeof RCN_OBJECT_KINDS)[number];

export const RCN_MARKETS = ["primary", "secondary"] as const;
export type RcnMarket = (typeof RCN_MARKETS)[number];

export type RcnMapFilters = {
	kind: "all" | RcnObjectKind;
	market: "all" | RcnMarket;
	months: number;
};

export const DEFAULT_RCN_FILTERS: RcnMapFilters = {
	kind: "all",
	market: "all",
	months: 24,
};

export type RcnFeature = {
	type: "Feature";
	id?: string | number;
	geometry: {
		type: "Point";
		coordinates: [number, number];
	};
	properties: Record<string, unknown>;
};

export type RcnFeatureCollection = {
	type: "FeatureCollection";
	features: RcnFeature[];
	meta?: {
		mode?: "clusters" | "points";
		total?: number;
		returned?: number;
		limit?: number;
		precision?: number;
		aggregation?: "hex";
		importedAt?: string | null;
		attribution?: string;
	};
};

export type RcnCandidate = {
	id: number;
	transactionId: number;
	objectKind: RcnObjectKind;
	pricePln: number;
	areaM2: number;
	pricePerM2Pln: number;
	lat: number;
	lng: number;
	distanceKm: number;
	documentDate: string;
	market: RcnMarket | null;
	addressText: string | null;
	city: string | null;
	street: string | null;
	rooms: number | null;
	floor: number | null;
};

export type RcnComparables = {
	status: "ok" | "unavailable";
	reason?: string;
	count: number;
	medianPricePerM2Pln: number | null;
	p25PricePerM2Pln: number | null;
	p75PricePerM2Pln: number | null;
	weightedPricePerM2Pln: number | null;
	radiusKm: number;
	months: number;
	confidence: "high" | "medium" | "low" | null;
	samples: RcnCandidate[];
	sameAddress: RcnCandidate[];
	importedAt: string | null;
	attribution: string;
};

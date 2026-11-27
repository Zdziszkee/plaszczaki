/** Minimal GeoJSON types for the scraped listings layer. */
export type ListingsMode = "clusters" | "points";

export type ListingFeature = {
	type: "Feature";
	id?: string | number;
	geometry: {
		type: "Point";
		coordinates: [number, number];
	};
	properties: Record<string, unknown>;
};

export type ListingFeatureCollection = {
	type: "FeatureCollection";
	features: ListingFeature[];
};

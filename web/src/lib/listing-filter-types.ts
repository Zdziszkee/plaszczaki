/** Client-safe listing filter types and validation. */

export const OFFER_TYPES = [
	"sale",
	"rent",
	"auction",
	"lease",
	"other",
] as const;
export type OfferType = (typeof OFFER_TYPES)[number];

export const PROPERTY_TYPES = [
	"flat",
	"house",
	"plot",
	"commercial",
	"garage",
	"room",
	"other",
] as const;
export type PropertyType = (typeof PROPERTY_TYPES)[number];

export const SCORE_PROFILES = [
	"kamienica",
	"nowe_mieszkanie",
	"dzialka_kolej",
	"dom_airbnb",
	"dom_kolej",
	"dzialka_airbnb",
] as const;
export type ScoreProfile = (typeof SCORE_PROFILES)[number];
export const LISTING_SORTS = ["newest", "score"] as const;
export type ListingSort = (typeof LISTING_SORTS)[number];

/** Newest-publication windows for the offer filters. */
export const POSTED_WITHIN_VALUES = ["today", "week", "month"] as const;
export type PostedWithin = (typeof POSTED_WITHIN_VALUES)[number];

export type ListingFilters = {
	offerType?: OfferType;
	propertyType?: PropertyType;
	voivodeship?: string;
	sourceId?: string;
	postedWithin?: PostedWithin;
	minPrice?: number;
	maxPrice?: number;
	minScore?: number;
	scoreProfile?: ScoreProfile;
	sort?: ListingSort;
};

export type BBox = {
	minLat: number;
	maxLat: number;
	minLng: number;
	maxLng: number;
};

const OFFER_TYPE_SET = new Set<string>(OFFER_TYPES);
const PROPERTY_TYPE_SET = new Set<string>(PROPERTY_TYPES);
const POSTED_WITHIN_SET = new Set<string>(POSTED_WITHIN_VALUES);
const SCORE_PROFILE_SET = new Set<string>(SCORE_PROFILES);
const LISTING_SORT_SET = new Set<string>(LISTING_SORTS);

function nonEmptyString(value: unknown): string | undefined {
	if (typeof value !== "string") return undefined;
	const text = value.trim();
	return text || undefined;
}

function positiveInt(value: unknown): number | undefined {
	if (value === null || value === undefined || value === "") return undefined;
	const parsed = typeof value === "number" ? value : Number(value);
	if (!Number.isFinite(parsed) || parsed <= 0) return undefined;
	return Math.trunc(parsed);
}

function numberValue(value: unknown): number | undefined {
	if (value === null || value === undefined || value === "") return undefined;
	const parsed = typeof value === "number" ? value : Number(value);
	return Number.isFinite(parsed) ? parsed : undefined;
}

export function sanitizeListingFilters(value: unknown): ListingFilters {
	if (!value || typeof value !== "object") return {};
	const input = value as Record<string, unknown>;
	const filters: ListingFilters = {};

	const offerType = nonEmptyString(input.offerType);
	if (offerType && OFFER_TYPE_SET.has(offerType)) {
		filters.offerType = offerType as OfferType;
	}

	const propertyType = nonEmptyString(input.propertyType);
	if (propertyType && PROPERTY_TYPE_SET.has(propertyType)) {
		filters.propertyType = propertyType as PropertyType;
	}

	filters.voivodeship = nonEmptyString(input.voivodeship);
	filters.sourceId = nonEmptyString(input.sourceId);

	const postedWithin = nonEmptyString(input.postedWithin);
	if (postedWithin && POSTED_WITHIN_SET.has(postedWithin)) {
		filters.postedWithin = postedWithin as PostedWithin;
	}

	filters.minPrice = positiveInt(input.minPrice);
	filters.maxPrice = positiveInt(input.maxPrice);
	const minScore = numberValue(input.minScore);
	if (minScore !== undefined) {
		filters.minScore = Math.max(0, Math.min(100, Math.trunc(minScore)));
	}
	const scoreProfile = nonEmptyString(input.scoreProfile);
	if (scoreProfile && SCORE_PROFILE_SET.has(scoreProfile)) {
		filters.scoreProfile = scoreProfile as ScoreProfile;
	}
	const sort = nonEmptyString(input.sort);
	if (sort && LISTING_SORT_SET.has(sort)) {
		filters.sort = sort as ListingSort;
	}
	if (
		filters.minPrice !== undefined &&
		filters.maxPrice !== undefined &&
		filters.minPrice > filters.maxPrice
	) {
		const swap = filters.minPrice;
		filters.minPrice = filters.maxPrice;
		filters.maxPrice = swap;
	}

	return filters;
}

export function parseListingFilters(search: URLSearchParams): ListingFilters {
	return sanitizeListingFilters({
		offerType: search.get("offerType"),
		propertyType: search.get("propertyType"),
		voivodeship: search.get("voivodeship"),
		sourceId: search.get("sourceId"),
		postedWithin: search.get("postedWithin"),
		minPrice: search.get("minPrice"),
		maxPrice: search.get("maxPrice"),
		minScore: search.get("minScore"),
		scoreProfile: search.get("scoreProfile"),
		sort: search.get("sort"),
	});
}

export function parseBbox(search: URLSearchParams): BBox | null {
	const minLat = numberValue(search.get("minLat"));
	const maxLat = numberValue(search.get("maxLat"));
	const minLng = numberValue(search.get("minLng"));
	const maxLng = numberValue(search.get("maxLng"));
	if (
		minLat === undefined ||
		maxLat === undefined ||
		minLng === undefined ||
		maxLng === undefined
	) {
		return null;
	}

	const bbox: BBox = {
		minLat: Math.max(-90, Math.min(90, Math.min(minLat, maxLat))),
		maxLat: Math.max(-90, Math.min(90, Math.max(minLat, maxLat))),
		minLng: Math.max(-180, Math.min(180, Math.min(minLng, maxLng))),
		maxLng: Math.max(-180, Math.min(180, Math.max(minLng, maxLng))),
	};
	if (bbox.minLat >= bbox.maxLat || bbox.minLng >= bbox.maxLng) {
		return null;
	}
	return bbox;
}

export function filtersToSearchParams(
	filters: ListingFilters,
): URLSearchParams {
	const params = new URLSearchParams();
	if (filters.offerType) params.set("offerType", filters.offerType);
	if (filters.propertyType) {
		params.set("propertyType", filters.propertyType);
	}
	if (filters.voivodeship) params.set("voivodeship", filters.voivodeship);
	if (filters.sourceId) params.set("sourceId", filters.sourceId);
	if (filters.postedWithin) {
		params.set("postedWithin", filters.postedWithin);
	}
	if (filters.minPrice !== undefined) {
		params.set("minPrice", String(filters.minPrice));
	}
	if (filters.maxPrice !== undefined) {
		params.set("maxPrice", String(filters.maxPrice));
	}
	if (filters.minScore !== undefined) {
		params.set("minScore", String(filters.minScore));
	}
	if (filters.scoreProfile) params.set("scoreProfile", filters.scoreProfile);
	if (filters.sort) params.set("sort", filters.sort);
	return params;
}

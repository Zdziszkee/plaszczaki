/** Client-safe types for configurable listing ranking. */

export const RANKING_CATEGORIES = [
	"price",
	"location",
	"attributes",
	"nature",
	"opportunity",
	"penalty",
] as const;

export type RankingCategory = (typeof RANKING_CATEGORIES)[number];
export type RankingBudgetCategory = Exclude<RankingCategory, "penalty">;
export type RankingCategoryWeights = Partial<
	Record<RankingBudgetCategory, number>
>;

export const RANKING_PROFILES = [
	"kamienica",
	"nowe_mieszkanie",
	"dzialka_kolej",
	"dom_airbnb",
	"dom_kolej",
	"dzialka_airbnb",
] as const;

export type ScoreProfile = (typeof RANKING_PROFILES)[number];

export type RankingCenter = "krakow" | "katowice";
export type RankingBooleanFilter = "any" | "yes" | "no";
export type RankingLargeWindowsFilter = RankingBooleanFilter;
export type RankingPostedWithin = "all" | "today" | "week" | "month";
export type RankingSort = "score" | "newest" | "price";
export type RankingMissingPolicy = "zero" | "exclude";
export type RankingPriceConfidence = "low" | "medium" | "high";
export type RankingPriceConfidenceFilter = "any" | RankingPriceConfidence;
export type RankingDate = Date | string | number;

export type PriceComparableSummary = {
	median: number;
	p25: number;
	p75: number;
	count: number;
	radiusKm: number;
	confidence: RankingPriceConfidence;
};

export type PriceEvidence = {
	listing: PriceComparableSummary | null;
	rcn: PriceComparableSummary | null;
	baseline: {
		low: number;
		median: number;
		high: number;
		count: number;
		level: string;
	} | null;
	benchmark: number | null;
	benchmarkSource: "rcn" | "listings" | "baseline" | null;
	discount: number | null;
	effectiveDiscount: number | null;
	confidence: RankingPriceConfidence | null;
	overpriced: boolean;
	topEligible: boolean;
};

export type RankingProfileSignals = {
	signals: Record<string, number | null>;
	penalties: Record<string, number | null>;
};

/** JSON payload written by the scraper's signals-v1 extraction step. */
export type RankingSignals = {
	version: "signals-v1";
	profiles: Partial<Record<ScoreProfile, RankingProfileSignals>>;
	metrics: {
		ceiling_height_m: number | null;
		large_windows: boolean | null;
	};
	priceEvidence: PriceEvidence | null;
};

/**
 * Canonical listing fields used by ranking, filters, and result cards.
 * Extended descriptive fields are optional so API projections can omit them.
 */
export type RankingListing = {
	id: number;
	externalId?: string;
	canonicalKey?: string | null;
	url?: string;
	title?: string | null;
	description?: string | null;
	offerType: string;
	propertyType: string;
	market?: string | null;
	priceKind?: string | null;
	priceUnit?: string | null;
	pricePln: number | null;
	rentPln?: number | null;
	depositPln?: number | null;
	pricePerM2Pln: number | null;
	areaM2: number | null;
	landAreaM2: number | null;
	rooms: number | null;
	floor?: number | null;
	floorLabel?: string | null;
	buildYear: number | null;
	condition?: string | null;
	buildingType?: string | null;
	buildingMaterial?: string | null;
	heating?: string | null;
	windowsType?: string | null;
	ownership?: string | null;
	energyCertificate?: string | null;
	ceilingHeightM?: number | null;
	buildingFloors?: number | null;
	elevator?: boolean | null;
	parking?: string | null;
	parkingSpaces?: number | null;
	balcony?: boolean | null;
	terrace?: boolean | null;
	garden?: boolean | null;
	furnished?: boolean | null;
	petsAllowed?: boolean | null;
	airConditioning?: boolean | null;
	security?: string | null;
	internet?: boolean | null;
	fenced?: boolean | null;
	administrativeRentPln?: number | null;
	estimatePln?: number | null;
	availableFrom?: RankingDate | null;
	voivodeship?: string | null;
	county?: string | null;
	commune?: string | null;
	city?: string | null;
	district?: string | null;
	street?: string | null;
	addressText?: string | null;
	lat?: number | null;
	lng?: number | null;
	geoSource?: string | null;
	images?: string[];
	sourceId: string;
	sourceLabel: string | null;
	sellerType?: string | null;
	sellerName?: string | null;
	publishedAt: number | null;
	modifiedAt?: RankingDate | null;
	firstSeenAt: number | null;
	lastSeenAt?: RankingDate | null;
	auctionDate?: RankingDate | null;
	auctionRound?: number | null;
	caseNumber?: string | null;
	scoreSignals?: RankingSignals | null;
};

export type RankingFilters = {
	propertyTypes: string[];
	offerTypes: string[];
	cities: string[];
	center: RankingCenter | null;
	maxCenterDistanceKm: number | null;
	areaMin: number | null;
	areaMax: number | null;
	landAreaMin: number | null;
	landAreaMax: number | null;
	roomsMin: number | null;
	roomsMax: number | null;
	priceMin: number | null;
	priceMax: number | null;
	pricePerM2Min: number | null;
	pricePerM2Max: number | null;
	buildYearMin: number | null;
	buildYearMax: number | null;
	buildingTypes: string[];
	elevator: RankingBooleanFilter;
	parking: RankingBooleanFilter;
	balcony: RankingBooleanFilter;
	terrace: RankingBooleanFilter;
	garden: RankingBooleanFilter;
	internet: RankingBooleanFilter;
	airConditioning: RankingBooleanFilter;
	districtHeating: RankingBooleanFilter;
	minCeilingHeight: number | null;
	largeWindows: RankingLargeWindowsFilter;
	markets: string[];
	conditions: string[];
	heatingTypes: string[];
	windowsTypes: string[];
	postedWithin: RankingPostedWithin;
	sort: RankingSort;
	resultLimit: number;
};

export type RankingOptions = {
	requirePriceEvidence: boolean;
	minPriceConfidence: RankingPriceConfidenceFilter;
	missingPolicy: RankingMissingPolicy;
};

export type RankingConfig = {
	version: string;
	profile: ScoreProfile;
	categoryWeights: Record<RankingCategory, number>;
	signalWeights: Record<string, number>;
	penaltyWeights: Record<string, number>;
	filters: RankingFilters;
	options: RankingOptions;
};

export type RankingContribution = {
	code: string;
	label: string;
	category: RankingCategory;
	source: string;
	weight: number;
	rawValue: number | null;
	points: number;
	/** Maximum points this contribution can receive under the current weights. */
	effectiveMaxPoints: number;
	/** Legacy-friendly alias; penalties have no positive category maximum. */
	maxPoints: number;
};

export type RankingCategoryBreakdown = {
	code: RankingCategory;
	label: string;
	points: number;
	maxPoints: number;
};

export type RankingAppliedRule = {
	code: string;
	label: string;
	cap: number | null;
	eligible: boolean | null;
};

export type RankingBreakdown = {
	score: number;
	rawScore: number;
	cap: number | null;
	capLabel: string | null;
	eligible: boolean | null;
	categories: RankingCategoryBreakdown[];
	contributions: RankingContribution[];
	penalties: RankingContribution[];
	zero: RankingContribution[];
	missing: string[];
	appliedRules: RankingAppliedRule[];
};

export type RankedListing = {
	rank: number;
	listing: RankingListing;
	score: number;
	eligible: boolean | null;
	breakdown: RankingBreakdown;
	pricePerM2Pln: number | null;
	distanceKm: number | null;
};

export type RankingResult = {
	items: RankedListing[];
	totalMatched: number;
	totalScored: number;
	excludedMissing: number;
	generatedAt: number;
};

export type RankingSummary = {
	count: number;
	averageScore: number;
	medianScore: number | null;
	minScore: number | null;
	maxScore: number | null;
	eligibleCount: number;
	unknownEligibilityCount: number;
	withPriceEvidenceCount: number;
};

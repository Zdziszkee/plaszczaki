import {
	BUILDING_TYPE_ALIASES,
	BUILDING_TYPE_GROUPS,
	BUILDING_TYPE_OPTIONS,
	CITY_CENTERS,
	DEFAULT_RANKING_CONFIG,
	getRankingProfileSpec,
	RANKING_CATEGORY_LABELS,
	type RankingRuleCondition,
} from "#/lib/ranking-spec";
import {
	RANKING_CATEGORIES,
	RANKING_PROFILES,
	type RankedListing,
	type RankingBooleanFilter,
	type RankingBreakdown,
	type RankingCategory,
	type RankingCategoryBreakdown,
	type RankingCategoryWeights,
	type RankingConfig,
	type RankingContribution,
	type RankingDate,
	type RankingFilters,
	type RankingListing,
	type RankingOptions,
	type RankingProfileSignals,
	type RankingResult,
	type RankingSort,
	type RankingSummary,
	type ScoreProfile,
} from "#/lib/ranking-types";

const BUDGET_CATEGORIES = [
	"price",
	"location",
	"attributes",
	"nature",
	"opportunity",
] as const;

const PROPERTY_TYPES = new Set([
	"flat",
	"house",
	"plot",
	"commercial",
	"garage",
	"room",
	"other",
]);
const OFFER_TYPES = new Set(["sale", "rent", "auction", "lease", "other"]);
const BUILDING_TYPES = new Set<string>([
	...BUILDING_TYPE_OPTIONS.map((option) => option.value),
	...BUILDING_TYPE_GROUPS.map((group) => group.code),
]);
const CONFIDENCE_RANK = { low: 1, medium: 2, high: 3 } as const;
const DAY_MS = 24 * 60 * 60 * 1000;
const EARTH_RADIUS_KM = 6371.0088;

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
	if (typeof value !== "number" && typeof value !== "string") return null;
	if (typeof value === "string" && value.trim() === "") return null;
	const parsed = typeof value === "number" ? value : Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function nonNegativeNumber(value: unknown): number | null {
	const parsed = finiteNumber(value);
	return parsed !== null && parsed >= 0 ? parsed : null;
}

function positiveNumber(value: unknown): number | null {
	const parsed = finiteNumber(value);
	return parsed !== null && parsed > 0 ? parsed : null;
}

function nonEmptyString(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const text = value.trim();
	return text || null;
}

function normalizeText(value: string): string {
	return value
		.replace(/ł/g, "l")
		.normalize("NFD")
		.replace(/[\u0300-\u036f]/g, "")
		.toLowerCase()
		.trim();
}

function uniqueStrings(values: readonly string[]): string[] {
	const seen = new Set<string>();
	const result: string[] = [];
	for (const value of values) {
		const key = normalizeText(value);
		if (!key || seen.has(key)) continue;
		seen.add(key);
		result.push(value);
	}
	return result;
}

function sanitizeStringList(
	value: unknown,
	allowed?: ReadonlySet<string>,
	transform?: (item: string) => string,
): string[] {
	if (!Array.isArray(value)) return [];
	const items: string[] = [];
	for (const entry of value) {
		const text = nonEmptyString(typeof entry === "string" ? entry : null);
		if (!text) continue;
		const item = transform ? transform(text) : text;
		const key = normalizeText(item);
		if (allowed && !allowed.has(key)) continue;
		items.push(item);
	}
	return uniqueStrings(items);
}

function normalizeBuildingType(value: string): string {
	const normalized = normalizeText(value);
	return BUILDING_TYPE_ALIASES[normalized] ?? normalized;
}

/** Straight-line great-circle distance in kilometres. */
export function haversineKm(
	lat1: number,
	lng1: number,
	lat2: number,
	lng2: number,
): number {
	const toRadians = (degrees: number) => (degrees * Math.PI) / 180;
	const deltaLat = toRadians(lat2 - lat1);
	const deltaLng = toRadians(lng2 - lng1);
	const startLat = toRadians(lat1);
	const endLat = toRadians(lat2);
	const haversine =
		Math.sin(deltaLat / 2) ** 2 +
		Math.cos(startLat) * Math.cos(endLat) * Math.sin(deltaLng / 2) ** 2;
	return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(Math.min(1, haversine)));
}

export function distanceFromCenter(
	listing: RankingListing,
	center: keyof typeof CITY_CENTERS | null,
): number | null {
	if (!center) return null;
	const lat = finiteNumber(listing.lat);
	const lng = finiteNumber(listing.lng);
	if (lat === null || lng === null) return null;
	const point = CITY_CENTERS[center];
	return haversineKm(lat, lng, point.lat, point.lng);
}

function listingPrice(listing: RankingListing): number | null {
	const price = positiveNumber(listing.pricePln);
	if (price !== null) return price;
	if (normalizeText(listing.offerType) === "rent") {
		return positiveNumber(listing.rentPln);
	}
	return null;
}

function listingArea(listing: RankingListing): number | null {
	const area = positiveNumber(listing.areaM2);
	if (area !== null) return area;
	if (normalizeText(listing.propertyType) === "plot") {
		return positiveNumber(listing.landAreaM2);
	}
	return null;
}

/** Uses the canonical price/m² value, falling back to price divided by area. */
export function derivePricePerM2Pln(listing: RankingListing): number | null {
	const canonical = positiveNumber(listing.pricePerM2Pln);
	if (canonical !== null) return canonical;
	const price = listingPrice(listing);
	const area = listingArea(listing);
	return price !== null && area !== null ? price / area : null;
}

export function listingCeilingHeight(listing: RankingListing): number | null {
	const signalHeight = finiteNumber(
		listing.scoreSignals?.metrics?.ceiling_height_m,
	);
	if (signalHeight !== null) return signalHeight;
	return finiteNumber(listing.ceilingHeightM);
}

export function listingLargeWindows(listing: RankingListing): boolean | null {
	const value = listing.scoreSignals?.metrics?.large_windows;
	return typeof value === "boolean" ? value : null;
}

/** Normalize non-negative category weights to a total budget of 100. */
export function normalizeCategoryWeights(
	weights: RankingCategoryWeights,
): Record<RankingCategory, number> {
	const normalized: Record<RankingCategory, number> = {
		price: 0,
		location: 0,
		attributes: 0,
		nature: 0,
		opportunity: 0,
		penalty: 0,
	};
	let total = 0;
	for (const category of BUDGET_CATEGORIES) {
		const value = nonNegativeNumber(weights[category]) ?? 0;
		normalized[category] = value;
		total += value;
	}
	if (total <= 0) return normalized;
	for (const category of BUDGET_CATEGORIES) {
		normalized[category] = (normalized[category] / total) * 100;
	}
	return normalized;
}

function sanitizeCategoryWeights(
	value: unknown,
	defaults: RankingConfig["categoryWeights"],
): RankingConfig["categoryWeights"] {
	const weights = { ...defaults };
	if (isRecord(value)) {
		for (const category of BUDGET_CATEGORIES) {
			if (!Object.hasOwn(value, category)) continue;
			const parsed = nonNegativeNumber(value[category]);
			if (parsed !== null) weights[category] = parsed;
		}
	}
	if (Object.values(weights).reduce((sum, weight) => sum + weight, 0) <= 0) {
		return normalizeCategoryWeights(defaults);
	}
	return normalizeCategoryWeights(weights);
}

function sanitizeWeights(
	value: unknown,
	defaults: Record<string, number>,
): Record<string, number> {
	const weights = { ...defaults };
	if (!isRecord(value)) return weights;
	for (const code of Object.keys(defaults)) {
		if (!Object.hasOwn(value, code)) continue;
		const parsed = nonNegativeNumber(value[code]);
		if (parsed !== null) weights[code] = parsed;
	}
	return weights;
}

function sanitizeNumberFilter(
	filters: Record<string, unknown>,
	key: string,
): number | null {
	if (!Object.hasOwn(filters, key) || filters[key] === null) return null;
	return nonNegativeNumber(filters[key]);
}

function sanitizeIntegerFilter(
	filters: Record<string, unknown>,
	key: string,
): number | null {
	const value = sanitizeNumberFilter(filters, key);
	return value === null ? null : Math.trunc(value);
}

function orderedRange(
	min: number | null,
	max: number | null,
): [number | null, number | null] {
	if (min !== null && max !== null && min > max) return [max, min];
	return [min, max];
}

function sanitizeEnum<T extends string>(
	value: unknown,
	allowed: readonly T[],
	fallback: T,
): T {
	const text = nonEmptyString(value);
	return text !== null && (allowed as readonly string[]).includes(text)
		? (text as T)
		: fallback;
}

function sanitizeFilters(
	value: unknown,
	defaults: RankingFilters,
): RankingFilters {
	const input = isRecord(value) ? value : {};
	const propertyTypes = Object.hasOwn(input, "propertyTypes")
		? sanitizeStringList(input.propertyTypes, PROPERTY_TYPES, (item) =>
				normalizeText(item),
			)
		: [...defaults.propertyTypes];
	const offerTypes = Object.hasOwn(input, "offerTypes")
		? sanitizeStringList(input.offerTypes, OFFER_TYPES, (item) =>
				normalizeText(item),
			)
		: [...defaults.offerTypes];
	const buildingTypes = Object.hasOwn(input, "buildingTypes")
		? sanitizeStringList(input.buildingTypes, BUILDING_TYPES, (item) =>
				normalizeBuildingType(item),
			)
		: [...defaults.buildingTypes];
	const centerText = nonEmptyString(input.center);
	const center =
		centerText === "krakow" || centerText === "katowice"
			? centerText
			: defaults.center;

	const [areaMin, areaMax] = orderedRange(
		sanitizeNumberFilter(input, "areaMin"),
		sanitizeNumberFilter(input, "areaMax"),
	);
	const [landAreaMin, landAreaMax] = orderedRange(
		sanitizeNumberFilter(input, "landAreaMin"),
		sanitizeNumberFilter(input, "landAreaMax"),
	);
	const [roomsMin, roomsMax] = orderedRange(
		sanitizeIntegerFilter(input, "roomsMin"),
		sanitizeIntegerFilter(input, "roomsMax"),
	);
	const [priceMin, priceMax] = orderedRange(
		sanitizeNumberFilter(input, "priceMin"),
		sanitizeNumberFilter(input, "priceMax"),
	);
	const [pricePerM2Min, pricePerM2Max] = orderedRange(
		sanitizeNumberFilter(input, "pricePerM2Min"),
		sanitizeNumberFilter(input, "pricePerM2Max"),
	);
	const [buildYearMin, buildYearMax] = orderedRange(
		sanitizeIntegerFilter(input, "buildYearMin"),
		sanitizeIntegerFilter(input, "buildYearMax"),
	);

	const requestedLimit = finiteNumber(input.resultLimit);
	const resultLimit =
		Object.hasOwn(input, "resultLimit") &&
		requestedLimit !== null &&
		requestedLimit > 0
			? Math.min(Math.max(Math.trunc(requestedLimit), 1), 5000)
			: defaults.resultLimit;
	const booleanFilter = (
		key:
			| "elevator"
			| "parking"
			| "balcony"
			| "terrace"
			| "garden"
			| "internet"
			| "airConditioning"
			| "districtHeating"
			| "largeWindows",
	): RankingFilters["elevator"] =>
		sanitizeEnum(input[key], ["any", "yes", "no"] as const, defaults[key]);
	return {
		propertyTypes,
		offerTypes,
		cities: Object.hasOwn(input, "cities")
			? sanitizeStringList(input.cities)
			: [...defaults.cities],
		center: center ?? null,
		maxCenterDistanceKm: sanitizeNumberFilter(input, "maxCenterDistanceKm"),
		areaMin,
		areaMax,
		landAreaMin,
		landAreaMax,
		roomsMin,
		roomsMax,
		priceMin,
		priceMax,
		pricePerM2Min,
		pricePerM2Max,
		buildYearMin,
		buildYearMax,
		buildingTypes,
		elevator: booleanFilter("elevator"),
		parking: booleanFilter("parking"),
		balcony: booleanFilter("balcony"),
		terrace: booleanFilter("terrace"),
		garden: booleanFilter("garden"),
		internet: booleanFilter("internet"),
		airConditioning: booleanFilter("airConditioning"),
		districtHeating: booleanFilter("districtHeating"),
		minCeilingHeight: sanitizeNumberFilter(input, "minCeilingHeight"),
		largeWindows: booleanFilter("largeWindows"),
		markets: Object.hasOwn(input, "markets")
			? sanitizeStringList(input.markets)
			: [...defaults.markets],
		conditions: Object.hasOwn(input, "conditions")
			? sanitizeStringList(input.conditions)
			: [...defaults.conditions],
		heatingTypes: Object.hasOwn(input, "heatingTypes")
			? sanitizeStringList(input.heatingTypes)
			: [...defaults.heatingTypes],
		windowsTypes: Object.hasOwn(input, "windowsTypes")
			? sanitizeStringList(input.windowsTypes)
			: [...defaults.windowsTypes],
		postedWithin: sanitizeEnum(
			input.postedWithin,
			["all", "today", "week", "month"] as const,
			defaults.postedWithin,
		),
		sort: sanitizeEnum(
			input.sort,
			["score", "newest", "price"] as const,
			defaults.sort,
		),
		resultLimit,
	};
}

function sanitizeOptions(value: unknown): RankingOptions {
	const input = isRecord(value) ? value : {};
	return {
		requirePriceEvidence:
			typeof input.requirePriceEvidence === "boolean"
				? input.requirePriceEvidence
				: false,
		minPriceConfidence: sanitizeEnum(
			input.minPriceConfidence,
			["any", "low", "medium", "high"],
			"any",
		),
		missingPolicy: sanitizeEnum(
			input.missingPolicy,
			["zero", "exclude"],
			"zero",
		),
	};
}

/** Validate untrusted JSON and return a complete, independent configuration. */
export function sanitizeRankingConfig(value: unknown): RankingConfig {
	const input = isRecord(value) ? value : {};
	const defaultProfile = DEFAULT_RANKING_CONFIG.profile;
	const profileText = nonEmptyString(input.profile);
	const profile = (RANKING_PROFILES as readonly string[]).includes(
		profileText ?? "",
	)
		? (profileText as ScoreProfile)
		: defaultProfile;
	const defaults = getRankingProfileSpec(profile);
	const defaultConfig = {
		...DEFAULT_RANKING_CONFIG,
		profile,
		categoryWeights: { ...defaults.categoryWeights, penalty: 0 },
		signalWeights: Object.fromEntries(
			defaults.signals.map((signal) => [signal.code, signal.defaultWeight]),
		),
		penaltyWeights: Object.fromEntries(
			defaults.penalties.map((penalty) => [
				penalty.code,
				penalty.defaultWeight,
			]),
		),
	};

	return {
		version: nonEmptyString(input.version) ?? "ranking-v1",
		profile,
		categoryWeights: sanitizeCategoryWeights(
			input.categoryWeights,
			defaultConfig.categoryWeights,
		),
		signalWeights: sanitizeWeights(
			input.signalWeights,
			defaultConfig.signalWeights,
		),
		penaltyWeights: sanitizeWeights(
			input.penaltyWeights,
			defaultConfig.penaltyWeights,
		),
		filters: sanitizeFilters(input.filters, DEFAULT_RANKING_CONFIG.filters),
		options: sanitizeOptions(input.options),
	};
}

function dateMilliseconds(
	value: RankingDate | null | undefined,
): number | null {
	if (value === null || value === undefined) return null;
	if (value instanceof Date) {
		const timestamp = value.getTime();
		return Number.isFinite(timestamp) ? timestamp : null;
	}
	if (typeof value === "number") {
		return Number.isFinite(value) ? value : null;
	}
	const text = value.trim();
	if (!text) return null;
	const numeric = Number(text);
	if (Number.isFinite(numeric)) return numeric;
	const timestamp = Date.parse(text);
	return Number.isFinite(timestamp) ? timestamp : null;
}

function postedAt(listing: RankingListing): number | null {
	return (
		dateMilliseconds(listing.publishedAt) ??
		dateMilliseconds(listing.firstSeenAt) ??
		dateMilliseconds(listing.lastSeenAt)
	);
}

function postedWithinCutoff(
	window: RankingFilters["postedWithin"],
	now: number,
): number | null {
	if (window === "all" || !window) return null;
	if (window === "week") return now - 7 * DAY_MS;
	if (window === "month") return now - 30 * DAY_MS;
	const start = new Date(now);
	start.setHours(0, 0, 0, 0);
	return start.getTime();
}

function matchesList(
	value: string | null | undefined,
	selected: readonly string[] | undefined,
): boolean {
	if (!selected || selected.length === 0) return true;
	if (!value) return false;
	const normalized = normalizeText(value);
	return selected.some((item) => normalizeText(item) === normalized);
}

function inRange(
	value: number | null,
	min: number | null | undefined,
	max: number | null | undefined,
): boolean {
	if (min === null || min === undefined) {
		if (max === null || max === undefined) return true;
		return value !== null && value <= max;
	}
	if (max === null || max === undefined) return value !== null && value >= min;
	return value !== null && value >= min && value <= max;
}

function expandedBuildingTypes(selected: readonly string[]): Set<string> {
	const expanded = new Set<string>();
	for (const item of selected) {
		const canonical = normalizeBuildingType(item);
		expanded.add(canonical);
		const group = BUILDING_TYPE_GROUPS.find(
			(candidate) => candidate.code === canonical,
		);
		if (group) {
			for (const value of group.values) expanded.add(value);
		}
	}
	return expanded;
}

function matchesBooleanFilter(
	value: boolean | null | undefined,
	filter: RankingBooleanFilter,
): boolean {
	if (filter === "any") return true;
	return filter === "yes" ? value === true : value === false;
}

function listingHasParking(listing: RankingListing): boolean | null {
	const parking = listing.parking?.trim();
	if (!parking) return null;
	return normalizeText(parking) !== "none";
}

function listingHasDistrictHeating(listing: RankingListing): boolean | null {
	const heating = normalizeText(listing.heating?.trim() ?? "");
	if (!heating) return null;
	return ["district", "miejskie", "co miejskie"].includes(heating);
}

function matchesPriceOptions(
	listing: RankingListing,
	options: RankingOptions,
): boolean {
	const evidence = listing.scoreSignals?.priceEvidence ?? null;
	if (options.requirePriceEvidence && evidence === null) return false;
	if (options.minPriceConfidence === "any") return true;
	if (evidence?.confidence === null || evidence?.confidence === undefined)
		return false;
	return (
		CONFIDENCE_RANK[evidence.confidence] >=
		CONFIDENCE_RANK[options.minPriceConfidence]
	);
}

function matchesSanitizedFilters(
	listing: RankingListing,
	config: RankingConfig,
	now: number,
): boolean {
	const filters = config.filters;
	if (
		filters.propertyTypes &&
		filters.propertyTypes.length > 0 &&
		!filters.propertyTypes.includes(normalizeText(listing.propertyType))
	) {
		return false;
	}
	if (
		filters.offerTypes &&
		filters.offerTypes.length > 0 &&
		!filters.offerTypes.includes(normalizeText(listing.offerType))
	) {
		return false;
	}
	if (!matchesList(listing.city ?? listing.commune, filters.cities))
		return false;
	if (!matchesList(listing.market, filters.markets)) return false;
	if (!matchesList(listing.condition, filters.conditions)) return false;
	if (!matchesList(listing.heating, filters.heatingTypes)) return false;
	if (!matchesList(listing.windowsType, filters.windowsTypes)) return false;

	if (filters.buildingTypes && filters.buildingTypes.length > 0) {
		const buildingType = listing.buildingType
			? normalizeBuildingType(listing.buildingType)
			: null;
		if (
			buildingType === null ||
			!expandedBuildingTypes(filters.buildingTypes).has(buildingType)
		) {
			return false;
		}
	}
	if (!matchesBooleanFilter(listing.elevator, filters.elevator)) return false;
	if (!matchesBooleanFilter(listingHasParking(listing), filters.parking))
		return false;
	if (!matchesBooleanFilter(listing.balcony, filters.balcony)) return false;
	if (!matchesBooleanFilter(listing.terrace, filters.terrace)) return false;
	if (!matchesBooleanFilter(listing.garden, filters.garden)) return false;
	if (!matchesBooleanFilter(listing.internet, filters.internet)) return false;
	if (!matchesBooleanFilter(listing.airConditioning, filters.airConditioning)) {
		return false;
	}
	if (
		!matchesBooleanFilter(
			listingHasDistrictHeating(listing),
			filters.districtHeating,
		)
	) {
		return false;
	}

	const price = listingPrice(listing);
	const pricePerM2 = derivePricePerM2Pln(listing);
	if (!inRange(price, filters.priceMin, filters.priceMax)) return false;
	if (!inRange(pricePerM2, filters.pricePerM2Min, filters.pricePerM2Max)) {
		return false;
	}
	if (
		!inRange(finiteNumber(listing.areaM2), filters.areaMin, filters.areaMax)
	) {
		return false;
	}
	if (
		!inRange(
			finiteNumber(listing.landAreaM2),
			filters.landAreaMin,
			filters.landAreaMax,
		)
	) {
		return false;
	}
	if (
		!inRange(finiteNumber(listing.rooms), filters.roomsMin, filters.roomsMax)
	) {
		return false;
	}
	if (
		!inRange(
			finiteNumber(listing.buildYear),
			filters.buildYearMin,
			filters.buildYearMax,
		)
	) {
		return false;
	}

	if (
		filters.center &&
		filters.maxCenterDistanceKm !== null &&
		filters.maxCenterDistanceKm !== undefined
	) {
		const distance = distanceFromCenter(listing, filters.center);
		if (distance === null || distance > filters.maxCenterDistanceKm)
			return false;
	}
	if (
		filters.minCeilingHeight !== null &&
		filters.minCeilingHeight !== undefined
	) {
		const ceiling = listingCeilingHeight(listing);
		if (ceiling === null || ceiling < filters.minCeilingHeight) return false;
	}
	if (
		!matchesBooleanFilter(listingLargeWindows(listing), filters.largeWindows)
	) {
		return false;
	}

	if (filters.postedWithin && filters.postedWithin !== "all") {
		const published = postedAt(listing);
		const cutoff = postedWithinCutoff(filters.postedWithin, now);
		if (published === null || cutoff === null || published < cutoff)
			return false;
	}
	return matchesPriceOptions(listing, config.options);
}

export function matchesRankingFilters(
	listing: RankingListing,
	config: RankingConfig,
	now: number = Date.now(),
): boolean {
	const sanitized = sanitizeRankingConfig(config);
	return matchesSanitizedFilters(
		listing,
		sanitized,
		dateMilliseconds(now) ?? Date.now(),
	);
}

function signalValue(
	values: Record<string, number | null> | undefined,
	code: string,
): number | null {
	const value = values?.[code];
	return finiteNumber(value);
}

function ruleValue(
	profileSignals: RankingProfileSignals | undefined,
	condition: Extract<
		RankingRuleCondition,
		{ signal: string } | { penalty: string }
	>,
): number | boolean | string | null {
	if ("signal" in condition) {
		return signalValue(profileSignals?.signals, condition.signal);
	}
	return signalValue(profileSignals?.penalties, condition.penalty);
}

function matchesCondition(
	condition: RankingRuleCondition,
	listing: RankingListing,
	profileSignals: RankingProfileSignals | undefined,
): boolean {
	if ("all" in condition) {
		return condition.all.every((item) =>
			matchesCondition(item, listing, profileSignals),
		);
	}
	if ("any" in condition) {
		return condition.any.some((item) =>
			matchesCondition(item, listing, profileSignals),
		);
	}
	if ("not" in condition) {
		return !matchesCondition(condition.not, listing, profileSignals);
	}
	if ("field" in condition) {
		const included = condition.values.some(
			(value) =>
				normalizeText(String(value)) === normalizeText(listing.propertyType),
		);
		return condition.op === "in" ? included : !included;
	}

	const value = ruleValue(profileSignals, condition);
	const present = value !== null;
	if (condition.op === "present") return present;
	if (condition.op === "missing") return !present;
	if (condition.op === "eq") return present && value === condition.value;
	if (condition.op === "gte") {
		return present &&
			typeof value === "number" &&
			typeof condition.value === "number"
			? value >= condition.value
			: false;
	}
	return (
		present &&
		typeof value === "number" &&
		typeof condition.value === "number" &&
		value <= condition.value
	);
}

function roundPoints(value: number): number {
	return Math.round((value + Number.EPSILON) * 1_000_000) / 1_000_000;
}

function categoryBudget(
	weights: Record<RankingCategory, number>,
	category: (typeof BUDGET_CATEGORIES)[number],
): number {
	return weights[category];
}

function scoreListingWithConfig(
	listing: RankingListing,
	config: RankingConfig,
): RankingBreakdown {
	const spec = getRankingProfileSpec(config.profile);
	const profileSignals = listing.scoreSignals?.profiles?.[config.profile];
	const categoryWeights = normalizeCategoryWeights(config.categoryWeights);
	const categoryDenominators = new Map<RankingCategory, number>();
	for (const category of BUDGET_CATEGORIES) {
		let denominator = 0;
		for (const signal of spec.signals) {
			if (signal.scored === false || signal.category !== category) continue;
			denominator += config.signalWeights[signal.code] ?? signal.defaultWeight;
		}
		categoryDenominators.set(category, denominator);
	}

	const contributions: RankingContribution[] = [];
	const penalties: RankingContribution[] = [];
	const zero: RankingContribution[] = [];
	const missing: string[] = [];

	for (const signal of spec.signals) {
		if (signal.scored === false) continue;
		const weight = config.signalWeights[signal.code] ?? signal.defaultWeight;
		const denominator = categoryDenominators.get(signal.category) ?? 0;
		const budget = categoryBudget(categoryWeights, signal.category);
		const effectiveMaxPoints =
			denominator > 0 ? (budget * weight) / denominator : 0;
		const rawValue = signalValue(profileSignals?.signals, signal.code);
		if (rawValue === null && weight > 0 && budget > 0)
			missing.push(signal.label);
		const points = roundPoints(
			rawValue === null ? 0 : effectiveMaxPoints * rawValue,
		);
		const contribution: RankingContribution = {
			code: signal.code,
			label: signal.label,
			category: signal.category,
			source: signal.source,
			weight,
			rawValue,
			points,
			effectiveMaxPoints: roundPoints(effectiveMaxPoints),
			maxPoints: roundPoints(effectiveMaxPoints),
		};
		if (points > 0) contributions.push(contribution);
		else if (points < 0) penalties.push(contribution);
		else zero.push(contribution);
	}

	for (const penalty of spec.penalties) {
		const weight = config.penaltyWeights[penalty.code] ?? penalty.defaultWeight;
		const rawValue = signalValue(profileSignals?.penalties, penalty.code);
		const points = roundPoints(rawValue === null ? 0 : -weight * rawValue);
		const contribution: RankingContribution = {
			code: penalty.code,
			label: penalty.label,
			category: "penalty",
			source: penalty.source,
			weight,
			rawValue,
			points,
			effectiveMaxPoints: weight,
			maxPoints: 0,
		};
		if (points < 0) penalties.push(contribution);
		else zero.push(contribution);
	}

	const allContributions = [...contributions, ...penalties, ...zero];
	const rawScore = roundPoints(
		allContributions.reduce(
			(sum, contribution) => sum + contribution.points,
			0,
		),
	);
	const categories: RankingCategoryBreakdown[] = RANKING_CATEGORIES.map(
		(category) => ({
			code: category,
			label: RANKING_CATEGORY_LABELS[category],
			points: roundPoints(
				allContributions
					.filter((contribution) => contribution.category === category)
					.reduce((sum, contribution) => sum + contribution.points, 0),
			),
			maxPoints:
				category === "penalty" ? 0 : roundPoints(categoryWeights[category]),
		}),
	);

	let cap: number | null = null;
	let capLabel: string | null = null;
	let eligible: boolean | null = true;
	const appliedRules: RankingBreakdown["appliedRules"] = [];

	for (const rule of spec.capRules) {
		if (!matchesCondition(rule.when, listing, profileSignals)) continue;
		if (cap === null || rule.cap < cap) {
			cap = rule.cap;
			capLabel = rule.label;
		}
		appliedRules.push({
			code: rule.code,
			label: rule.label,
			cap: rule.cap,
			eligible: null,
		});
	}

	for (const rule of spec.eligibilityRules) {
		if (!matchesCondition(rule.when, listing, profileSignals)) continue;
		if (rule.eligible === false) eligible = false;
		else if (eligible !== false && rule.eligible === null) eligible = null;
		if (rule.cap !== undefined && (cap === null || rule.cap < cap)) {
			cap = rule.cap;
			capLabel = rule.label;
		}
		for (const label of rule.missing ?? []) missing.push(label);
		appliedRules.push({
			code: rule.code,
			label: rule.label,
			cap: rule.cap ?? null,
			eligible: rule.eligible,
		});
	}

	if (config.options.missingPolicy === "exclude" && missing.length > 0) {
		eligible = false;
		cap = 0;
		capLabel = "Brak wymaganych danych sygnałowych";
		appliedRules.push({
			code: "missing_signal_data",
			label: capLabel,
			cap: 0,
			eligible: false,
		});
	}

	const uniqueMissing = [...new Set(missing)];
	const effectiveCap = eligible === false ? 0 : cap;
	const capped =
		effectiveCap === null ? rawScore : Math.min(rawScore, effectiveCap);
	const score = Math.floor(Math.max(0, Math.min(100, capped)) + 0.5);

	return {
		score,
		rawScore,
		cap: effectiveCap,
		capLabel,
		eligible,
		categories,
		contributions,
		penalties,
		zero,
		missing: uniqueMissing,
		appliedRules,
	};
}

export function scoreListing(
	listing: RankingListing,
	config: RankingConfig,
): RankingBreakdown {
	return scoreListingWithConfig(listing, sanitizeRankingConfig(config));
}

function compareNumbers(a: number, b: number): number {
	if (a === b) return 0;
	return a < b ? -1 : 1;
}

function compareListings(
	a: RankedListing,
	b: RankedListing,
	sort: RankingSort,
): number {
	if (sort === "score") {
		const scoreOrder = compareNumbers(b.score, a.score);
		if (scoreOrder !== 0) return scoreOrder;
	} else if (sort === "newest") {
		const newestOrder = compareNumbers(
			postedAt(b.listing) ?? Number.NEGATIVE_INFINITY,
			postedAt(a.listing) ?? Number.NEGATIVE_INFINITY,
		);
		if (newestOrder !== 0) return newestOrder;
	} else {
		const aPrice = listingPrice(a.listing) ?? Number.POSITIVE_INFINITY;
		const bPrice = listingPrice(b.listing) ?? Number.POSITIVE_INFINITY;
		const priceOrder = compareNumbers(aPrice, bPrice);
		if (priceOrder !== 0) return priceOrder;
	}

	const scoreOrder = compareNumbers(b.score, a.score);
	if (scoreOrder !== 0) return scoreOrder;
	const newestOrder = compareNumbers(
		postedAt(b.listing) ?? Number.NEGATIVE_INFINITY,
		postedAt(a.listing) ?? Number.NEGATIVE_INFINITY,
	);
	if (newestOrder !== 0) return newestOrder;
	const priceOrder = compareNumbers(
		listingPrice(a.listing) ?? Number.POSITIVE_INFINITY,
		listingPrice(b.listing) ?? Number.POSITIVE_INFINITY,
	);
	if (priceOrder !== 0) return priceOrder;
	const idOrder = compareNumbers(a.listing.id, b.listing.id);
	if (idOrder !== 0) return idOrder;
	return (
		normalizeText(a.listing.canonicalKey ?? "").localeCompare(
			normalizeText(b.listing.canonicalKey ?? ""),
		) ||
		normalizeText(a.listing.sourceId ?? "").localeCompare(
			normalizeText(b.listing.sourceId ?? ""),
		) ||
		normalizeText(a.listing.externalId ?? "").localeCompare(
			normalizeText(b.listing.externalId ?? ""),
		)
	);
}

/** Filter, score, deterministically sort, and limit canonical listings. */
export function rankListings(
	listings: readonly RankingListing[],
	config: RankingConfig,
	now: number = Date.now(),
): RankingResult {
	const sanitized = sanitizeRankingConfig(config);
	const generatedAt = dateMilliseconds(now) ?? Date.now();
	const ranked: RankedListing[] = [];
	let totalMatched = 0;
	let totalScored = 0;
	let excludedMissing = 0;
	for (const listing of listings) {
		if (!matchesSanitizedFilters(listing, sanitized, generatedAt)) continue;
		totalMatched += 1;
		const breakdown = scoreListingWithConfig(listing, sanitized);
		totalScored += 1;
		if (breakdown.eligible === false) {
			if (
				sanitized.options.missingPolicy === "exclude" &&
				breakdown.missing.length > 0
			) {
				excludedMissing += 1;
			}
			continue;
		}
		ranked.push({
			rank: 0,
			listing,
			score: breakdown.score,
			eligible: breakdown.eligible,
			breakdown,
			pricePerM2Pln: derivePricePerM2Pln(listing),
			distanceKm: distanceFromCenter(listing, sanitized.filters.center ?? null),
		});
	}
	ranked.sort((a, b) =>
		compareListings(a, b, sanitized.filters.sort ?? "score"),
	);
	for (const [index, item] of ranked.entries()) item.rank = index + 1;
	const items = ranked.slice(0, sanitized.filters.resultLimit);
	return {
		items,
		totalMatched,
		totalScored,
		excludedMissing,
		generatedAt,
	};
}

function roundOne(value: number): number {
	return Math.round((value + Number.EPSILON) * 10) / 10;
}

export function summarizeResults(
	input: RankingResult | readonly RankedListing[],
): RankingSummary {
	const ranked = "items" in input ? input.items : input;
	const scores = ranked.map((item) => item.score).sort((a, b) => a - b);
	const total = scores.reduce((sum, score) => sum + score, 0);
	const middle = Math.floor(scores.length / 2);
	const median =
		scores.length === 0
			? null
			: scores.length % 2 === 1
				? scores[middle]
				: roundOne(((scores[middle - 1] ?? 0) + (scores[middle] ?? 0)) / 2);
	return {
		count: ranked.length,
		averageScore: ranked.length === 0 ? 0 : roundOne(total / ranked.length),
		medianScore: median,
		minScore: scores.length === 0 ? null : scores[0],
		maxScore: scores.length === 0 ? null : scores[scores.length - 1],
		eligibleCount: ranked.filter((item) => item.breakdown.eligible === true)
			.length,
		unknownEligibilityCount: ranked.filter(
			(item) => item.breakdown.eligible === null,
		).length,
		withPriceEvidenceCount: ranked.filter(
			(item) => item.listing.scoreSignals?.priceEvidence != null,
		).length,
	};
}

export const summarizeRankingResults = summarizeResults;

export type {
	RankingProfileSpec,
	RankingSignalSpec,
} from "#/lib/ranking-spec";
export {
	BUILDING_TYPE_OPTIONS,
	CITY_CENTERS,
	createDefaultRankingConfig,
	DEFAULT_RANKING_CONFIG,
	PROFILE_SPECS,
	RANKING_PROFILE_SPECS,
} from "#/lib/ranking-spec";
export type {
	RankedListing,
	RankingBreakdown,
	RankingCategory,
	RankingConfig,
	RankingFilters,
	RankingListing,
	RankingOptions,
	RankingResult,
	RankingSignals,
	ScoreProfile,
} from "#/lib/ranking-types";

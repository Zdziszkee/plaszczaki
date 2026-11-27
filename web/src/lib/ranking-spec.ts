import type {
	RankingBudgetCategory,
	RankingCategory,
	RankingConfig,
	ScoreProfile,
} from "#/lib/ranking-types";
import {
	DISTANCE_SIGNAL_CODES,
	type DistanceWeights,
} from "#/lib/ranking-weights";

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

export const RANKING_CATEGORY_LABELS: Record<RankingCategory, string> = {
	price: "Cena i wartość",
	location: "Lokalizacja i odległości",
	attributes: "Atrybuty i dopasowanie",
	nature: "Natura i turystyka",
	opportunity: "Okazja rynkowa",
	penalty: "Kary",
};

/** Backwards-friendly short name for consumers rendering category headings. */
export const CATEGORY_LABELS = RANKING_CATEGORY_LABELS;

export const SCORE_PROFILE_LABELS: Record<ScoreProfile, string> = {
	universal: "Twój ranking",
};

export const DISTANCE_SIGNAL_LABELS = {
	rail_station: "Stacja kolejowa",
	tram_stop: "Stacja tramwajowa",
	lake: "Jezioro / zbiornik",
	river: "Rzeka / strumień",
	forest: "Las",
	city_center: "Centrum miasta",
} as const;

export const CITY_CENTERS = {
	krakow: {
		city: "Kraków",
		label: "Kraków — Rynek Główny",
		lat: 50.0614,
		lng: 19.9366,
	},
	katowice: {
		city: "Katowice",
		label: "Katowice — Rynek",
		lat: 50.2599,
		lng: 19.0216,
	},
} as const;

export const BUILDING_TYPE_GROUPS = [
	{
		code: "tenement",
		label: "Kamienice",
		values: ["tenement"],
	},
	{
		code: "apartment_building",
		label: "Apartamentowce",
		values: ["apartment_building"],
	},
	{
		code: "block",
		label: "Bloki",
		values: ["block", "prefabricated_concrete"],
	},
	{
		code: "house",
		label: "Domy",
		values: [
			"house",
			"detached_house",
			"row_house",
			"semi_detached_house",
			"detached",
			"terraced",
			"villa",
		],
	},
	{
		code: "other",
		label: "Pozostałe",
		values: ["other"],
	},
] as const;

export const BUILDING_TYPE_OPTIONS = [
	{ value: "tenement", label: "Kamienica", group: "tenement" },
	{
		value: "apartment_building",
		label: "Apartamentowiec",
		group: "apartment_building",
	},
	{ value: "block", label: "Blok", group: "block" },
	{
		value: "prefabricated_concrete",
		label: "Budownictwo systemowe",
		group: "block",
	},
	{ value: "house", label: "Dom", group: "house" },
	{
		value: "detached_house",
		label: "Dom wolnostojący",
		group: "house",
	},
	{ value: "row_house", label: "Dom szeregowy", group: "house" },
	{
		value: "semi_detached_house",
		label: "Dom bliźniak",
		group: "house",
	},
	{ value: "detached", label: "Dom wolnostojący", group: "house" },
	{ value: "terraced", label: "Dom szeregowy", group: "house" },
	{ value: "villa", label: "Willa", group: "house" },
	{ value: "other", label: "Inny", group: "other" },
] as const;

export const BUILDING_TYPE_ALIASES: Readonly<Record<string, string>> = {
	blok: "block",
	"niski blok": "block",
	"blok mieszkalny": "block",
	kamienica: "tenement",
	"dom wielorodzinny": "apartment_building",
	"apartment building": "apartment_building",
	apartamentowiec: "apartment_building",
	"budynek wielorodzinny": "apartment_building",
	niski_wielorodzinny: "apartment_building",
	prefabrykowany: "prefabricated_concrete",
	"wielka plyta": "prefabricated_concrete",
	"dom wolnostojacy": "detached_house",
	wolnostojacy: "detached_house",
	budynek_wolnostojacy: "detached_house",
	private_house: "detached_house",
	"dom szeregowy": "row_house",
	szeregowy: "row_house",
	terraced: "row_house",
	blizniak: "semi_detached_house",
	rezydencja: "villa",
	palac_lub_dworek: "villa",
	inny: "other",
};

export type RankingSignalSpec = {
	code: string;
	label: string;
	category: RankingBudgetCategory;
	defaultWeight: number;
	source: string;
	/** Control signals participate in rules but do not earn category points. */
	scored?: boolean;
	metric?: "ceiling_height_m" | "large_windows";
};

export type RankingPenaltyDefinition = {
	code: string;
	label: string;
	defaultWeight: number;
	source: string;
};

type RankingRuleValue = string | number | boolean;

export type RankingRuleCondition =
	| {
			signal: string;
			op: "present" | "missing" | "eq" | "gte" | "lte";
			value?: RankingRuleValue;
	  }
	| {
			penalty: string;
			op: "present" | "missing" | "eq" | "gte" | "lte";
			value?: RankingRuleValue;
	  }
	| {
			field: "propertyType";
			op: "in" | "notIn";
			values: readonly RankingRuleValue[];
	  }
	| { all: readonly RankingRuleCondition[] }
	| { any: readonly RankingRuleCondition[] }
	| { not: RankingRuleCondition };

export type RankingCapRule = {
	code: string;
	label: string;
	when: RankingRuleCondition;
	cap: number;
};

export type RankingEligibilityRule = {
	code: string;
	label: string;
	when: RankingRuleCondition;
	eligible: boolean | null;
	cap?: number;
	missing?: readonly string[];
};

export type RankingProfileSpec = {
	profile: ScoreProfile;
	label: string;
	propertyTypes: readonly string[];
	categoryWeights: Record<RankingBudgetCategory, number>;
	signals: readonly RankingSignalSpec[];
	penalties: readonly RankingPenaltyDefinition[];
	capRules: readonly RankingCapRule[];
	eligibilityRules: readonly RankingEligibilityRule[];
};

const PRICE_SIGNALS = [
	{
		code: "price_quantile",
		label: "Cena wobec lokalnego kwantyla",
		category: "price",
		defaultWeight: 40,
		source: "database",
	},
	{
		code: "comparable_discount",
		label: "Rabat względem podobnych ofert i transakcji",
		category: "price",
		defaultWeight: 60,
		source: "database",
	},
] as const satisfies readonly RankingSignalSpec[];

const DISTANCE_SIGNALS = [
	{
		code: "rail_station",
		label: "Odległość do stacji kolejowej",
		category: "location",
		defaultWeight: 25,
		source: "geo",
	},
	{
		code: "tram_stop",
		label: "Odległość do stacji tramwajowej",
		category: "location",
		defaultWeight: 15,
		source: "geo",
	},
	{
		code: "lake",
		label: "Odległość do jeziora / zbiornika",
		category: "location",
		defaultWeight: 10,
		source: "geo",
	},
	{
		code: "river",
		label: "Odległość do rzeki / strumienia",
		category: "location",
		defaultWeight: 10,
		source: "geo",
	},
	{
		code: "forest",
		label: "Odległość do lasu",
		category: "location",
		defaultWeight: 15,
		source: "geo",
	},
	{
		code: "city_center",
		label: "Odległość od centrum miasta",
		category: "location",
		defaultWeight: 25,
		source: "geo",
	},
] as const satisfies readonly RankingSignalSpec[];

const ATTRIBUTE_SIGNALS = [
	{
		code: "building_character",
		label: "Kamienica / starsza zabudowa",
		category: "attributes",
		defaultWeight: 10,
		source: "structured",
	},
	{
		code: "building_age",
		label: "Rok budowy",
		category: "attributes",
		defaultWeight: 8,
		source: "structured",
	},
	{
		code: "charm",
		label: "Oryginalne detale / klimat",
		category: "attributes",
		defaultWeight: 8,
		source: "text",
	},
	{
		code: "condition",
		label: "Standard / gotowość do zamieszkania",
		category: "attributes",
		defaultWeight: 10,
		source: "structured",
	},
	{
		code: "house_condition",
		label: "Stan techniczny domu",
		category: "attributes",
		defaultWeight: 8,
		source: "structured",
	},
	{
		code: "district_heating",
		label: "Ogrzewanie miejskie",
		category: "attributes",
		defaultWeight: 6,
		source: "structured",
	},
	{
		code: "insulation",
		label: "Ocieplenie / termomodernizacja",
		category: "attributes",
		defaultWeight: 5,
		source: "text",
	},
	{
		code: "windows",
		label: "Nowa / wymieniona stolarka",
		category: "attributes",
		defaultWeight: 5,
		source: "structured",
	},
	{
		code: "parking",
		label: "Parking",
		category: "attributes",
		defaultWeight: 5,
		source: "structured",
	},
	{
		code: "elevator",
		label: "Winda",
		category: "attributes",
		defaultWeight: 4,
		source: "structured",
	},
	{
		code: "outdoor",
		label: "Balkon / taras / ogród",
		category: "attributes",
		defaultWeight: 5,
		source: "structured",
	},
	{
		code: "internet",
		label: "Internet",
		category: "attributes",
		defaultWeight: 3,
		source: "structured",
	},
	{
		code: "air_conditioning",
		label: "Klimatyzacja",
		category: "attributes",
		defaultWeight: 3,
		source: "structured",
	},
	{
		code: "building_plot",
		label: "Działka budowlana / MPZP / WZ",
		category: "attributes",
		defaultWeight: 12,
		source: "text",
	},
	{
		code: "utilities",
		label: "Media w okolicy",
		category: "attributes",
		defaultWeight: 4,
		source: "text",
	},
	{
		code: "fenced",
		label: "Ogrodzona działka",
		category: "attributes",
		defaultWeight: 3,
		source: "structured",
	},
	{
		code: "ceiling",
		label: "Wysokie sufity",
		category: "attributes",
		defaultWeight: 4,
		source: "structured",
		metric: "ceiling_height_m",
	},
	{
		code: "large_windows",
		label: "Duże okna",
		category: "attributes",
		defaultWeight: 4,
		source: "structured",
		metric: "large_windows",
	},
] as const satisfies readonly RankingSignalSpec[];

const NATURE_SIGNALS = [
	{
		code: "mountains",
		label: "Region górski",
		category: "nature",
		defaultWeight: 45,
		source: "geo",
	},
	{
		code: "tourist_area",
		label: "Okolica turystyczna",
		category: "nature",
		defaultWeight: 30,
		source: "geo",
	},
	{
		code: "elevation",
		label: "Wysokość nad poziomem morza",
		category: "nature",
		defaultWeight: 25,
		source: "geo",
	},
] as const satisfies readonly RankingSignalSpec[];

const RESIDENTIAL_OPPORTUNITY_SIGNALS = [
	{
		code: "auction_discount",
		label: "Rabat licytacyjny do wyceny",
		category: "opportunity",
		defaultWeight: 30,
		source: "database",
	},
	{
		code: "market_time",
		label: "Czas oferty na rynku",
		category: "opportunity",
		defaultWeight: 20,
		source: "database",
	},
	{
		code: "price_cut",
		label: "Obniżka ceny",
		category: "opportunity",
		defaultWeight: 25,
		source: "database",
	},
	{
		code: "rental_yield",
		label: "Szacowany zwrot z najmu",
		category: "opportunity",
		defaultWeight: 25,
		source: "database",
	},
] as const satisfies readonly RankingSignalSpec[];

const OVERPRICED_PENALTY = {
	code: "overpriced",
	label: "Cena powyżej rynku",
	defaultWeight: 10,
	source: "database",
} as const satisfies RankingPenaltyDefinition;

const OVERPRICED_CAP = {
	code: "overpriced_cap",
	label: "Limit dla ceny powyżej rynku",
	when: { penalty: "overpriced", op: "eq", value: 1 },
	cap: 65,
} as const satisfies RankingCapRule;

const UNIVERSAL_SPEC = {
	profile: "universal",
	label: SCORE_PROFILE_LABELS.universal,
	propertyTypes: ["flat", "house", "plot"],
	categoryWeights: {
		price: 25,
		location: 35,
		attributes: 20,
		nature: 10,
		opportunity: 10,
	},
	signals: [
		...PRICE_SIGNALS,
		...DISTANCE_SIGNALS,
		...ATTRIBUTE_SIGNALS,
		...NATURE_SIGNALS,
		...RESIDENTIAL_OPPORTUNITY_SIGNALS,
	],
	penalties: [
		OVERPRICED_PENALTY,
		{
			code: "non_building_plot",
			label: "Działka rolna / leśna / rekreacyjna",
			defaultWeight: 15,
			source: "text",
		},
	],
	capRules: [OVERPRICED_CAP],
	eligibilityRules: [],
} as const satisfies RankingProfileSpec;

export const RANKING_PROFILE_SPECS: Record<ScoreProfile, RankingProfileSpec> = {
	universal: UNIVERSAL_SPEC,
};

/** Backwards-friendly alias for callers that only need the selected declaration. */
export const PROFILE_SPECS = RANKING_PROFILE_SPECS;

export function getRankingProfileSpec(
	profile: ScoreProfile,
): RankingProfileSpec {
	return RANKING_PROFILE_SPECS[profile];
}

export function createDefaultDistanceWeights(): DistanceWeights {
	const weights = {} as DistanceWeights;
	for (const code of DISTANCE_SIGNAL_CODES) {
		weights[code] =
			UNIVERSAL_SPEC.signals.find((signal) => signal.code === code)
				?.defaultWeight ?? 0;
	}
	return weights;
}

export function createDefaultRankingConfig(
	profile: ScoreProfile = "universal",
): RankingConfig {
	const spec = getRankingProfileSpec(profile);
	return {
		version: "ranking-v2",
		profile,
		categoryWeights: { ...spec.categoryWeights, penalty: 0 },
		signalWeights: Object.fromEntries(
			spec.signals.map((signal) => [signal.code, signal.defaultWeight]),
		),
		penaltyWeights: Object.fromEntries(
			spec.penalties.map((penalty) => [penalty.code, penalty.defaultWeight]),
		),
		distanceWeights: createDefaultDistanceWeights(),
		filters: {
			propertyTypes: [],
			offerTypes: ["sale", "auction"],
			cities: [],
			center: "nearest",
			maxCenterDistanceKm: null,
			areaMin: null,
			areaMax: null,
			landAreaMin: null,
			landAreaMax: null,
			roomsMin: null,
			roomsMax: null,
			priceMin: null,
			priceMax: null,
			pricePerM2Min: null,
			pricePerM2Max: null,
			buildYearMin: null,
			buildYearMax: null,
			buildingTypes: [],
			elevator: "any",
			parking: "any",
			balcony: "any",
			terrace: "any",
			garden: "any",
			internet: "any",
			airConditioning: "any",
			districtHeating: "any",
			minCeilingHeight: null,
			largeWindows: "any",
			markets: [],
			conditions: [],
			heatingTypes: [],
			windowsTypes: [],
			postedWithin: "all",
			sort: "score",
			resultLimit: 25,
		},
		options: {
			requirePriceEvidence: false,
			minPriceConfidence: "any",
			missingPolicy: "zero",
		},
	};
}

export const DEFAULT_RANKING_CONFIG = createDefaultRankingConfig();

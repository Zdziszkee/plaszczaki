import type {
	RankingBudgetCategory,
	RankingCategory,
	RankingConfig,
	ScoreProfile,
} from "#/lib/ranking-types";

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
	location: "Lokalizacja i kolej",
	attributes: "Atrybuty i dopasowanie",
	nature: "Natura i turystyka",
	opportunity: "Okazja rynkowa",
	penalty: "Kary",
};

/** Backwards-friendly short name for consumers rendering category headings. */
export const CATEGORY_LABELS = RANKING_CATEGORY_LABELS;

export const SCORE_PROFILE_LABELS: Record<ScoreProfile, string> = {
	kamienica: "Klimatyczna kamienica",
	nowe_mieszkanie: "Nowe mieszkanie",
	dzialka_kolej: "Działka pod kolej",
	dom_airbnb: "Dom pod Airbnb",
	dom_kolej: "Dom blisko kolei",
	dzialka_airbnb: "Działka przy naturze",
};

export const CITY_CENTERS = {
	krakow: {
		label: "Kraków — Rynek Główny",
		lat: 50.0614,
		lng: 19.9366,
	},
	katowice: {
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

const FLAT_LOCATION_SIGNALS = [
	{
		code: "walkability",
		label: "Użyteczność okolicy",
		category: "location",
		defaultWeight: 60,
		source: "geo",
	},
	{
		code: "commute",
		label: "Bliskość stacji lub przystanku",
		category: "location",
		defaultWeight: 40,
		source: "geo",
	},
] as const satisfies readonly RankingSignalSpec[];

const RAIL_LOCATION_SIGNALS = [
	{
		code: "station",
		label: "Odległość do stacji docelowej",
		category: "location",
		defaultWeight: 50,
		source: "geo",
	},
	{
		code: "station_frequency",
		label: "Częstotliwość połączeń do celu",
		category: "location",
		defaultWeight: 30,
		source: "geo",
	},
	{
		code: "planned_rail",
		label: "Planowana inwestycja kolejowa",
		category: "location",
		defaultWeight: 20,
		source: "geo",
	},
	{
		code: "station_gate",
		label: "Stacja z połączeniami w promieniu 3 km",
		category: "location",
		defaultWeight: 0,
		source: "geo",
		scored: false,
	},
	{
		code: "station_data_available",
		label: "Dane stacji dostępne",
		category: "location",
		defaultWeight: 0,
		source: "geo",
		scored: false,
	},
] as const satisfies readonly RankingSignalSpec[];

const NATURE_SIGNALS = [
	{
		code: "forest",
		label: "Las",
		category: "nature",
		defaultWeight: 35,
		source: "geo",
	},
	{
		code: "water",
		label: "Woda",
		category: "nature",
		defaultWeight: 30,
		source: "geo",
	},
	{
		code: "mountains",
		label: "Region górski",
		category: "nature",
		defaultWeight: 20,
		source: "geo",
	},
	{
		code: "tourist_area",
		label: "Okolica turystyczna",
		category: "nature",
		defaultWeight: 10,
		source: "geo",
	},
	{
		code: "elevation",
		label: "Wysokość nad poziomem morza",
		category: "nature",
		defaultWeight: 5,
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

const PLOT_OPPORTUNITY_SIGNALS = [
	{
		code: "auction_discount",
		label: "Rabat licytacyjny do wyceny",
		category: "opportunity",
		defaultWeight: 35,
		source: "database",
	},
	{
		code: "market_time",
		label: "Czas oferty na rynku",
		category: "opportunity",
		defaultWeight: 25,
		source: "database",
	},
	{
		code: "price_cut",
		label: "Obniżka ceny",
		category: "opportunity",
		defaultWeight: 40,
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

function propertyEligibility(
	propertyTypes: readonly string[],
): RankingEligibilityRule {
	return {
		code: "property_type",
		label: "Niezgodny typ nieruchomości",
		when: { field: "propertyType", op: "notIn", values: propertyTypes },
		eligible: false,
		cap: 0,
	};
}

const STATION_ELIGIBILITY_RULES = [
	{
		code: "station_data_unavailable",
		label: "Brak cache stacji kolejowych",
		when: { signal: "station_data_available", op: "eq", value: 0 },
		eligible: null,
		missing: ["brak cache stacji kolejowych"],
	},
	{
		code: "station_data_missing",
		label: "Brak danych o stacji",
		when: {
			all: [
				{ signal: "station_data_available", op: "missing" },
				{ signal: "station_gate", op: "missing" },
			],
		},
		eligible: null,
		missing: ["brak danych o stacji kolejowej"],
	},
	{
		code: "station_gate_failed",
		label: "Brak stacji z połączeniami do celu w promieniu 3 km",
		when: { signal: "station_gate", op: "eq", value: 0 },
		eligible: false,
		cap: 0,
	},
] as const satisfies readonly RankingEligibilityRule[];

const PLOT_BUILDING_UNKNOWN_RULE = {
	code: "building_rights_unknown",
	label: "Brak potwierdzonego prawa do zabudowy",
	when: {
		any: [
			{
				all: [
					{ signal: "building_plot", op: "eq", value: 0 },
					{ penalty: "non_building_plot", op: "eq", value: 0 },
				],
			},
			{
				all: [
					{ signal: "building_plot", op: "missing" },
					{ penalty: "non_building_plot", op: "missing" },
				],
			},
		],
	},
	eligible: null,
	missing: ["brak potwierdzonego prawa do zabudowy"],
} as const satisfies RankingEligibilityRule;

const KAMIENICA_SPEC = {
	profile: "kamienica",
	label: SCORE_PROFILE_LABELS.kamienica,
	propertyTypes: ["flat"],
	categoryWeights: {
		price: 30,
		location: 15,
		attributes: 45,
		nature: 0,
		opportunity: 10,
	},
	signals: [
		...PRICE_SIGNALS,
		...FLAT_LOCATION_SIGNALS,
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
			defaultWeight: 6,
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
			code: "district_heating",
			label: "Ogrzewanie miejskie",
			category: "attributes",
			defaultWeight: 8,
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
			defaultWeight: 4,
			source: "structured",
		},
		{
			code: "ceiling",
			label: "Wysokie sufity",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
			metric: "ceiling_height_m",
		},
		{
			code: "large_windows",
			label: "Duże okna",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
			metric: "large_windows",
		},
		{
			code: "outdoor",
			label: "Balkon / taras",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
		},
		...RESIDENTIAL_OPPORTUNITY_SIGNALS,
	],
	penalties: [
		OVERPRICED_PENALTY,
		{
			code: "prefab_penalty",
			label: "Wielka płyta / budownictwo systemowe",
			defaultWeight: 12,
			source: "structured",
		},
		{
			code: "newer_building",
			label: "Budynek po 1970 r. bez starszego klimatu",
			defaultWeight: 3,
			source: "structured",
		},
	],
	capRules: [
		OVERPRICED_CAP,
		{
			code: "prefab_cap",
			label: "Limit dla budownictwa systemowego",
			when: { penalty: "prefab_penalty", op: "eq", value: 1 },
			cap: 30,
		},
	],
	eligibilityRules: [propertyEligibility(["flat"])],
} as const satisfies RankingProfileSpec;

const NOWE_MIESZKANIE_SPEC = {
	profile: "nowe_mieszkanie",
	label: SCORE_PROFILE_LABELS.nowe_mieszkanie,
	propertyTypes: ["flat"],
	categoryWeights: {
		price: 30,
		location: 30,
		attributes: 25,
		nature: 0,
		opportunity: 15,
	},
	signals: [
		...PRICE_SIGNALS,
		...FLAT_LOCATION_SIGNALS,
		{
			code: "building_age",
			label: "Rok budowy",
			category: "attributes",
			defaultWeight: 8,
			source: "structured",
		},
		{
			code: "condition",
			label: "Wysoki standard / gotowe do zamieszkania",
			category: "attributes",
			defaultWeight: 6,
			source: "structured",
		},
		{
			code: "primary_market",
			label: "Rynek pierwotny",
			category: "attributes",
			defaultWeight: 3,
			source: "structured",
		},
		{
			code: "parking",
			label: "Parking",
			category: "attributes",
			defaultWeight: 3,
			source: "structured",
		},
		{
			code: "elevator",
			label: "Winda",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
		},
		{
			code: "outdoor",
			label: "Balkon / taras",
			category: "attributes",
			defaultWeight: 1,
			source: "structured",
		},
		{
			code: "air_conditioning",
			label: "Klimatyzacja",
			category: "attributes",
			defaultWeight: 1,
			source: "structured",
		},
		{
			code: "ceiling",
			label: "Wysokie sufity",
			category: "attributes",
			defaultWeight: 1,
			source: "structured",
			metric: "ceiling_height_m",
		},
		{
			code: "large_windows",
			label: "Duże okna",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
			metric: "large_windows",
		},
		...RESIDENTIAL_OPPORTUNITY_SIGNALS,
	],
	penalties: [
		OVERPRICED_PENALTY,
		{
			code: "prefab_penalty",
			label: "Wielka płyta / budownictwo systemowe",
			defaultWeight: 12,
			source: "structured",
		},
		{
			code: "old_building_penalty",
			label: "Przedwojenny budynek, nie nowy standard",
			defaultWeight: 6,
			source: "structured",
		},
	],
	capRules: [
		OVERPRICED_CAP,
		{
			code: "prefab_cap",
			label: "Limit dla budownictwa systemowego",
			when: { penalty: "prefab_penalty", op: "eq", value: 1 },
			cap: 30,
		},
	],
	eligibilityRules: [propertyEligibility(["flat"])],
} as const satisfies RankingProfileSpec;

const DOM_AIRBNB_SPEC = {
	profile: "dom_airbnb",
	label: SCORE_PROFILE_LABELS.dom_airbnb,
	propertyTypes: ["house"],
	categoryWeights: {
		price: 20,
		location: 10,
		attributes: 30,
		nature: 25,
		opportunity: 15,
	},
	signals: [
		...PRICE_SIGNALS,
		{
			code: "commute",
			label: "Bliskość stacji lub przystanku",
			category: "location",
			defaultWeight: 100,
			source: "geo",
		},
		{
			code: "old_house",
			label: "Rok budowy / stary dom",
			category: "attributes",
			defaultWeight: 8,
			source: "structured",
		},
		{
			code: "traditional_material",
			label: "Drewno / cegła — tradycyjna zabudowa",
			category: "attributes",
			defaultWeight: 5,
			source: "structured",
		},
		{
			code: "charm",
			label: "Klimat / charakter / zabytkowe detale",
			category: "attributes",
			defaultWeight: 12,
			source: "text",
		},
		{
			code: "airbnb",
			label: "Potencjał noclegowy / inwestycyjny",
			category: "attributes",
			defaultWeight: 5,
			source: "text",
		},
		{
			code: "ceiling",
			label: "Wysokie sufity",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
			metric: "ceiling_height_m",
		},
		{
			code: "large_windows",
			label: "Duże okna",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
			metric: "large_windows",
		},
		...NATURE_SIGNALS,
		...RESIDENTIAL_OPPORTUNITY_SIGNALS,
	],
	penalties: [
		OVERPRICED_PENALTY,
		{
			code: "modern_house",
			label: "Nowoczesny dom bez starszego klimatu",
			defaultWeight: 6,
			source: "structured",
		},
		{
			code: "no_charm",
			label: "Brak sygnałów klimatu / starego domu",
			defaultWeight: 6,
			source: "structured",
		},
	],
	capRules: [
		OVERPRICED_CAP,
		{
			code: "no_charm_cap",
			label: "Limit bez sygnałów klimatu",
			when: { penalty: "no_charm", op: "eq", value: 1 },
			cap: 45,
		},
	],
	eligibilityRules: [propertyEligibility(["house"])],
} as const satisfies RankingProfileSpec;

const DOM_KOLEJ_SPEC = {
	profile: "dom_kolej",
	label: SCORE_PROFILE_LABELS.dom_kolej,
	propertyTypes: ["house"],
	categoryWeights: {
		price: 25,
		location: 30,
		attributes: 25,
		nature: 10,
		opportunity: 10,
	},
	signals: [
		...PRICE_SIGNALS,
		...RAIL_LOCATION_SIGNALS,
		{
			code: "house_condition",
			label: "Stan techniczny / rok budowy",
			category: "attributes",
			defaultWeight: 10,
			source: "structured",
		},
		{
			code: "district_heating",
			label: "Ogrzewanie miejskie",
			category: "attributes",
			defaultWeight: 5,
			source: "structured",
		},
		{
			code: "parking",
			label: "Parking",
			category: "attributes",
			defaultWeight: 4,
			source: "structured",
		},
		{
			code: "outdoor",
			label: "Ogród / taras",
			category: "attributes",
			defaultWeight: 3,
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
			code: "ceiling",
			label: "Wysokie sufity",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
			metric: "ceiling_height_m",
		},
		{
			code: "large_windows",
			label: "Duże okna",
			category: "attributes",
			defaultWeight: 2,
			source: "structured",
			metric: "large_windows",
		},
		...NATURE_SIGNALS,
		...RESIDENTIAL_OPPORTUNITY_SIGNALS,
	],
	penalties: [OVERPRICED_PENALTY],
	capRules: [OVERPRICED_CAP],
	eligibilityRules: [
		propertyEligibility(["house"]),
		...STATION_ELIGIBILITY_RULES,
	],
} as const satisfies RankingProfileSpec;

const DZIALKA_KOLEJ_SPEC = {
	profile: "dzialka_kolej",
	label: SCORE_PROFILE_LABELS.dzialka_kolej,
	propertyTypes: ["plot"],
	categoryWeights: {
		price: 25,
		location: 30,
		attributes: 30,
		nature: 5,
		opportunity: 10,
	},
	signals: [
		...PRICE_SIGNALS,
		...RAIL_LOCATION_SIGNALS,
		{
			code: "building_plot",
			label: "Działka budowlana / MPZP / WZ",
			category: "attributes",
			defaultWeight: 22,
			source: "text",
		},
		{
			code: "utilities",
			label: "Media w okolicy",
			category: "attributes",
			defaultWeight: 5,
			source: "text",
		},
		{
			code: "fenced",
			label: "Ogrodzona działka",
			category: "attributes",
			defaultWeight: 3,
			source: "structured",
		},
		...NATURE_SIGNALS,
		...PLOT_OPPORTUNITY_SIGNALS,
	],
	penalties: [
		OVERPRICED_PENALTY,
		{
			code: "non_building_plot",
			label: "Działka rolna / leśna / rekreacyjna",
			defaultWeight: 20,
			source: "text",
		},
	],
	capRules: [OVERPRICED_CAP],
	eligibilityRules: [
		propertyEligibility(["plot"]),
		{
			code: "non_residential_plot",
			label: "Brak przeznaczenia do zabudowy",
			when: { penalty: "non_building_plot", op: "eq", value: 1 },
			eligible: false,
			cap: 0,
		},
		PLOT_BUILDING_UNKNOWN_RULE,
		...STATION_ELIGIBILITY_RULES,
	],
} as const satisfies RankingProfileSpec;

const DZIALKA_AIRBNB_SPEC = {
	profile: "dzialka_airbnb",
	label: SCORE_PROFILE_LABELS.dzialka_airbnb,
	propertyTypes: ["plot"],
	categoryWeights: {
		price: 20,
		location: 5,
		attributes: 25,
		nature: 40,
		opportunity: 10,
	},
	signals: [
		...PRICE_SIGNALS,
		...RAIL_LOCATION_SIGNALS,
		{
			code: "building_plot",
			label: "Możliwość zabudowy / MPZP / WZ",
			category: "attributes",
			defaultWeight: 17,
			source: "text",
		},
		{
			code: "airbnb",
			label: "Potencjał inwestycyjny / turystyczny",
			category: "attributes",
			defaultWeight: 5,
			source: "text",
		},
		{
			code: "utilities",
			label: "Media w okolicy",
			category: "attributes",
			defaultWeight: 3,
			source: "text",
		},
		...NATURE_SIGNALS,
		...PLOT_OPPORTUNITY_SIGNALS,
	],
	penalties: [
		OVERPRICED_PENALTY,
		{
			code: "non_building_plot",
			label: "Działka rolna / leśna / rekreacyjna",
			defaultWeight: 20,
			source: "text",
		},
		{
			code: "build_unconfirmed",
			label: "Brak potwierdzonej możliwości zabudowy",
			defaultWeight: 5,
			source: "text",
		},
	],
	capRules: [
		OVERPRICED_CAP,
		{
			code: "non_residential_cap",
			label: "Limit dla terenu bez zabudowy",
			when: { penalty: "non_building_plot", op: "eq", value: 1 },
			cap: 35,
		},
		{
			code: "unconfirmed_build_cap",
			label: "Limit bez potwierdzonej zabudowy",
			when: { penalty: "build_unconfirmed", op: "eq", value: 1 },
			cap: 55,
		},
	],
	eligibilityRules: [propertyEligibility(["plot"])],
} as const satisfies RankingProfileSpec;

export const RANKING_PROFILE_SPECS: Record<ScoreProfile, RankingProfileSpec> = {
	kamienica: KAMIENICA_SPEC,
	nowe_mieszkanie: NOWE_MIESZKANIE_SPEC,
	dzialka_kolej: DZIALKA_KOLEJ_SPEC,
	dom_airbnb: DOM_AIRBNB_SPEC,
	dom_kolej: DOM_KOLEJ_SPEC,
	dzialka_airbnb: DZIALKA_AIRBNB_SPEC,
};

/** Short alias for callers that only need the selected profile's declaration. */
export const PROFILE_SPECS = RANKING_PROFILE_SPECS;

export function getRankingProfileSpec(
	profile: ScoreProfile,
): RankingProfileSpec {
	return RANKING_PROFILE_SPECS[profile];
}

export function createDefaultRankingConfig(
	profile: ScoreProfile,
): RankingConfig {
	const spec = getRankingProfileSpec(profile);
	return {
		version: "ranking-v1",
		profile,
		categoryWeights: { ...spec.categoryWeights, penalty: 0 },
		signalWeights: Object.fromEntries(
			spec.signals.map((signal) => [signal.code, signal.defaultWeight]),
		),
		penaltyWeights: Object.fromEntries(
			spec.penalties.map((penalty) => [penalty.code, penalty.defaultWeight]),
		),
		filters: {
			propertyTypes: [],
			offerTypes: ["sale", "auction"],
			cities: [],
			center: null,
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

export const DEFAULT_RANKING_CONFIG = createDefaultRankingConfig("kamienica");

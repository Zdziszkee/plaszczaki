import type { ScoreProfile } from "#/lib/listing-filter-types";

export const SCORE_PROFILE_LABELS: Record<ScoreProfile, string> = {
	kamienica: "Klimatyczna kamienica",
	nowe_mieszkanie: "Nowe mieszkanie",
	dzialka_kolej: "Działka pod kolej",
	dom_airbnb: "Dom pod Airbnb",
	dom_kolej: "Dom blisko kolei",
	dzialka_airbnb: "Działka przy naturze",
};

export const SCORE_PROFILE_SHORT_LABELS: Record<ScoreProfile, string> = {
	kamienica: "Kamienica",
	nowe_mieszkanie: "Nowe mieszkanie",
	dzialka_kolej: "Pod kolej",
	dom_airbnb: "Airbnb",
	dom_kolej: "Kolej",
	dzialka_airbnb: "Przy naturze",
};

export const SCORE_CATEGORY_LABELS = {
	price: "Cena i wartość",
	location: "Lokalizacja i kolej",
	attributes: "Atrybuty i dopasowanie",
	nature: "Natura i turystyka",
	opportunity: "Okazja rynkowa",
	penalty: "Kary",
} as const;

export type ScoreCategoryCode = keyof typeof SCORE_CATEGORY_LABELS;

export type ScoreContribution = {
	code: string;
	label: string;
	points: number;
	category: ScoreCategoryCode;
	maxPoints: number;
	source: string;
};

export type ScoreCategorySummary = {
	code: ScoreCategoryCode;
	label: string;
	points: number;
	maxPoints: number;
};

export type ScoreProfileBreakdown = {
	score: number;
	rawScore: number;
	cap: number | null;
	capLabel: string | null;
	eligible: boolean | null;
	categories: ScoreCategorySummary[];
	contributions: ScoreContribution[];
	penalties: ScoreContribution[];
	zero: ScoreContribution[];
	missing: string[];
};

export type ScoreBreakdown = {
	version: string;
	winner: ScoreProfile | "other";
	profiles: Partial<Record<ScoreProfile, ScoreProfileBreakdown>>;
};

export const SCORE_PROFILE_WEIGHTS: Record<
	ScoreProfile,
	Record<Exclude<ScoreCategoryCode, "penalty">, number>
> = {
	kamienica: {
		price: 30,
		location: 15,
		attributes: 45,
		nature: 0,
		opportunity: 10,
	},
	nowe_mieszkanie: {
		price: 30,
		location: 30,
		attributes: 25,
		nature: 0,
		opportunity: 15,
	},
	dom_airbnb: {
		price: 20,
		location: 10,
		attributes: 30,
		nature: 25,
		opportunity: 15,
	},
	dom_kolej: {
		price: 25,
		location: 30,
		attributes: 25,
		nature: 10,
		opportunity: 10,
	},
	dzialka_kolej: {
		price: 25,
		location: 30,
		attributes: 30,
		nature: 5,
		opportunity: 10,
	},
	dzialka_airbnb: {
		price: 20,
		location: 5,
		attributes: 25,
		nature: 40,
		opportunity: 10,
	},
};

export function formatScorePoints(value: number): string {
	return new Intl.NumberFormat("pl-PL", {
		minimumFractionDigits: 1,
		maximumFractionDigits: 1,
	}).format(value);
}

import type {
	RankingBudgetCategory,
	RankingCategory,
	RankingCategoryWeights,
} from "#/lib/ranking-types";

export const RANKING_BUDGET_CATEGORIES = [
	"price",
	"location",
	"attributes",
	"nature",
	"opportunity",
] as const satisfies readonly RankingBudgetCategory[];

export const DISTANCE_SIGNAL_CODES = [
	"rail_station",
	"tram_stop",
	"lake",
	"river",
	"forest",
	"city_center",
] as const;

export type DistanceSignalCode = (typeof DISTANCE_SIGNAL_CODES)[number];
export type DistanceWeights = Record<DistanceSignalCode, number>;

function clamp(value: number, min: number, max: number): number {
	return Math.min(max, Math.max(min, value));
}

/** Round proportional shares and correct the final share so they total 100 exactly. */
export function normalizePercentageWeights<T extends string>(
	keys: readonly T[],
	weights: Record<T, number>,
): Record<T, number> {
	const normalized = Object.fromEntries(keys.map((key) => [key, 0])) as Record<
		T,
		number
	>;
	const safeWeights = keys.map((key) => {
		const value = weights[key];
		return Number.isFinite(value) && value > 0 ? value : 0;
	});
	const total = safeWeights.reduce((sum, value) => sum + value, 0);
	if (total <= 0 || keys.length === 0) return normalized;

	const shares = safeWeights.map((value) => (value / total) * 100);
	const rounded = shares.map((share) => Math.floor(share));
	const remainder = 100 - rounded.reduce((sum, value) => sum + value, 0);
	const order = shares
		.map((share, index) => ({ index, fraction: share - rounded[index] }))
		.sort((a, b) => b.fraction - a.fraction || a.index - b.index);
	for (let index = 0; index < remainder; index += 1) {
		rounded[order[index % order.length].index] += 1;
	}
	for (const [index, key] of keys.entries()) {
		normalized[key] = clamp(rounded[index], 0, 100);
	}
	return normalized;
}

/**
 * Change one weight and redistribute the remaining percentage points in proportion
 * to the other weights. Values remain integers and always sum to exactly 100.
 */
export function redistributePercentageWeights<T extends string>(
	keys: readonly T[],
	weights: Record<T, number>,
	changedKey: T,
	requestedValue: number,
): Record<T, number> {
	const current = normalizePercentageWeights(keys, weights);
	const index = keys.indexOf(changedKey);
	if (index < 0) return current;
	const value = clamp(Math.round(requestedValue), 0, 100);
	current[changedKey] = value;

	const otherKeys = keys.filter((key) => key !== changedKey);
	const remaining = 100 - value;
	const otherTotal = otherKeys.reduce((sum, key) => sum + current[key], 0);
	if (otherKeys.length === 0) return current;

	const shares =
		otherTotal > 0
			? otherKeys.map((key) => (current[key] / otherTotal) * remaining)
			: otherKeys.map(() => remaining / otherKeys.length);
	const rounded = shares.map((share) => Math.floor(share));
	const leftover = remaining - rounded.reduce((sum, share) => sum + share, 0);
	const order = shares
		.map((share, shareIndex) => ({
			shareIndex,
			fraction: share - rounded[shareIndex],
		}))
		.sort((a, b) => b.fraction - a.fraction || a.shareIndex - b.shareIndex);
	for (let index = 0; index < leftover; index += 1) {
		rounded[order[index % order.length].shareIndex] += 1;
	}

	for (const [otherIndex, key] of otherKeys.entries()) {
		current[key] = rounded[otherIndex];
	}
	return current;
}

export function normalizeRankingCategoryWeights(
	weights: RankingCategoryWeights,
): Record<RankingCategory, number> {
	const normalized = normalizePercentageWeights(
		RANKING_BUDGET_CATEGORIES,
		Object.fromEntries(
			RANKING_BUDGET_CATEGORIES.map((key) => [key, weights[key] ?? 0]),
		) as Record<RankingBudgetCategory, number>,
	);
	return { ...normalized, penalty: 0 };
}

export function normalizeDistanceWeights(
	weights: DistanceWeights,
): DistanceWeights {
	return normalizePercentageWeights(DISTANCE_SIGNAL_CODES, weights);
}

export function redistributeDistanceWeights(
	weights: DistanceWeights,
	changedKey: DistanceSignalCode,
	value: number,
): DistanceWeights {
	return redistributePercentageWeights(
		DISTANCE_SIGNAL_CODES,
		weights,
		changedKey,
		value,
	);
}

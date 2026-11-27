import { DEFAULT_RCN_FILTERS, type RcnMapFilters } from "./rcn-types";

const KIND_VALUES = new Set(["all", "flat", "building", "plot", "premises"]);
const MARKET_VALUES = new Set(["all", "primary", "secondary"]);

export function sanitizeRcnFilters(value: unknown): RcnMapFilters {
	if (!value || typeof value !== "object") return { ...DEFAULT_RCN_FILTERS };
	const input = value as Record<string, unknown>;
	const kind = typeof input.kind === "string" ? input.kind : "all";
	const market = typeof input.market === "string" ? input.market : "all";
	const rawMonths = Number(input.months ?? DEFAULT_RCN_FILTERS.months);
	const months = Number.isFinite(rawMonths)
		? Math.max(1, Math.min(120, Math.trunc(rawMonths)))
		: DEFAULT_RCN_FILTERS.months;
	return {
		kind: KIND_VALUES.has(kind)
			? (kind as RcnMapFilters["kind"])
			: DEFAULT_RCN_FILTERS.kind,
		market: MARKET_VALUES.has(market)
			? (market as RcnMapFilters["market"])
			: DEFAULT_RCN_FILTERS.market,
		months,
	};
}

export function parseRcnFilters(search: URLSearchParams): RcnMapFilters {
	return sanitizeRcnFilters({
		kind: search.get("kind"),
		market: search.get("market"),
		months: search.get("months"),
	});
}

export function rcnFiltersToSearchParams(
	filters: RcnMapFilters,
): URLSearchParams {
	const params = new URLSearchParams();
	if (filters.kind !== "all") params.set("kind", filters.kind);
	if (filters.market !== "all") params.set("market", filters.market);
	params.set("months", String(filters.months));
	return params;
}

export function monthsCutoff(months: number, now = new Date()): string {
	const cutoff = new Date(now);
	cutoff.setUTCMonth(cutoff.getUTCMonth() - months);
	return cutoff.toISOString().slice(0, 10);
}

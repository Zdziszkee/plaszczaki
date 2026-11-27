import type {
	RcnCandidate,
	RcnComparables,
	RcnMarket,
	RcnObjectKind,
} from "./rcn-types";

export const RCN_GRID_DEGREES = 0.02;
export const RCN_ATTRIBUTION =
	"Wykorzystano/opracowano na podstawie materiałów państwowego zasobu geodezyjnego i kartograficznego";

export const RCN_COMPARABLE_DEFAULTS = {
	months: 24,
	minimumCount: 5,
	maximumCount: 30,
	sampleCount: 5,
	radiiKm: {
		flat: [1, 2.5, 5],
		building: [2, 4, 5],
		plot: [2, 4, 5],
	} as Record<RcnObjectKind, number[]>,
	areaRatio: [0.7, 1.4],
	plotAreaRatio: [0.5, 2],
	roomsDelta: 1,
} as const;

export type ComparableListing = {
	propertyType: string;
	market: string | null;
	lat: number;
	lng: number;
	areaM2: number;
	rooms: number | null;
	street: string | null;
	city: string | null;
};

export function haversineKm(
	lat1: number,
	lng1: number,
	lat2: number,
	lng2: number,
): number {
	const toRadians = (value: number) => (value * Math.PI) / 180;
	const dLat = toRadians(lat2 - lat1);
	const dLng = toRadians(lng2 - lng1);
	const a =
		Math.sin(dLat / 2) ** 2 +
		Math.cos(toRadians(lat1)) *
			Math.cos(toRadians(lat2)) *
			Math.sin(dLng / 2) ** 2;
	return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function gridBounds(
	lat: number,
	lng: number,
	radiusKm: number,
	gridDegrees = RCN_GRID_DEGREES,
): { minLat: number; maxLat: number; minLng: number; maxLng: number } {
	const latDelta = radiusKm / 111;
	const lngDelta =
		radiusKm / (111 * Math.max(0.2, Math.cos((lat * Math.PI) / 180)));
	const minLat = lat - latDelta;
	const maxLat = lat + latDelta;
	const minLng = lng - lngDelta;
	const maxLng = lng + lngDelta;
	return {
		minLat: Math.floor(minLat / gridDegrees + 1e-9) * gridDegrees,
		maxLat: Math.floor(maxLat / gridDegrees + 1e-9) * gridDegrees,
		minLng: Math.floor(minLng / gridDegrees + 1e-9) * gridDegrees,
		maxLng: Math.floor(maxLng / gridDegrees + 1e-9) * gridDegrees,
	};
}

export function quantile(values: number[], fraction: number): number | null {
	if (values.length === 0) return null;
	const ordered = [...values].sort((a, b) => a - b);
	const position = (ordered.length - 1) * fraction;
	const lower = Math.floor(position);
	const upper = Math.ceil(position);
	if (lower === upper) return ordered[lower];
	return (
		ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
	);
}

export function weightedMedian(
	values: Array<{ value: number; weight: number }>,
): number | null {
	const sorted = values
		.filter((item) => Number.isFinite(item.value) && item.weight > 0)
		.sort((a, b) => a.value - b.value);
	const total = sorted.reduce((sum, item) => sum + item.weight, 0);
	if (total <= 0) return null;
	let cumulative = 0;
	for (const item of sorted) {
		cumulative += item.weight;
		if (cumulative >= total / 2) return item.value;
	}
	return sorted.at(-1)?.value ?? null;
}

function normalizeAddress(value: string | null): string {
	return (value ?? "")
		.toLowerCase()
		.normalize("NFD")
		.replace(/[\u0300-\u036f]/g, "")
		.replace(/\b(ul|ulica|al|aleja|os|osiedle|pl|plac|msc)\b/g, "")
		.replace(/[^a-z0-9]+/g, " ")
		.trim();
}

function targetKind(propertyType: string): RcnObjectKind | null {
	if (propertyType === "flat") return "flat";
	if (propertyType === "house") return "building";
	if (propertyType === "plot") return "plot";
	return null;
}

function areaRatio(listing: ComparableListing): [number, number] {
	return listing.propertyType === "plot"
		? [...RCN_COMPARABLE_DEFAULTS.plotAreaRatio]
		: [...RCN_COMPARABLE_DEFAULTS.areaRatio];
}

function removeOutliers(candidates: RcnCandidate[]): RcnCandidate[] {
	if (candidates.length < 8) return candidates;
	const values = candidates.map((candidate) => candidate.pricePerM2Pln);
	const q1 = quantile(values, 0.25) ?? 0;
	const q3 = quantile(values, 0.75) ?? 0;
	const spread = q3 - q1;
	if (spread <= 0) return candidates;
	const lower = q1 - spread * 1.5;
	const upper = q3 + spread * 1.5;
	return candidates.filter(
		(candidate) =>
			candidate.pricePerM2Pln >= lower && candidate.pricePerM2Pln <= upper,
	);
}

export function summarizeComparables(
	listing: ComparableListing,
	candidates: RcnCandidate[],
	options: {
		months?: number;
		minimumCount?: number;
		maximumCount?: number;
		sampleCount?: number;
		importedAt?: string | null;
	} = {},
): RcnComparables {
	const months = options.months ?? RCN_COMPARABLE_DEFAULTS.months;
	const minimumCount =
		options.minimumCount ?? RCN_COMPARABLE_DEFAULTS.minimumCount;
	const maximumCount =
		options.maximumCount ?? RCN_COMPARABLE_DEFAULTS.maximumCount;
	const sampleCount =
		options.sampleCount ?? RCN_COMPARABLE_DEFAULTS.sampleCount;
	const kind = targetKind(listing.propertyType);
	const base = {
		months,
		importedAt: options.importedAt ?? null,
		attribution: RCN_ATTRIBUTION,
	};
	if (!kind) {
		return {
			...base,
			status: "unavailable",
			reason: "unsupported_property_type",
			count: 0,
			medianPricePerM2Pln: null,
			p25PricePerM2Pln: null,
			p75PricePerM2Pln: null,
			weightedPricePerM2Pln: null,
			radiusKm: 0,
			confidence: null,
			samples: [],
			sameAddress: [],
		};
	}

	const [ratioLow, ratioHigh] = areaRatio(listing);
	const maxRadius = Math.max(...RCN_COMPARABLE_DEFAULTS.radiiKm[kind]);
	const eligible = candidates.filter((candidate) => {
		if (candidate.distanceKm > maxRadius) return false;
		if (candidate.objectKind !== kind) return false;
		if (listing.market === "primary" || listing.market === "secondary") {
			if (candidate.market !== listing.market) return false;
		}
		const ratio = candidate.areaM2 / listing.areaM2;
		if (!(ratio >= ratioLow && ratio <= ratioHigh)) return false;
		if (
			kind === "flat" &&
			listing.rooms !== null &&
			candidate.rooms !== null &&
			Math.abs(candidate.rooms - listing.rooms) >
				RCN_COMPARABLE_DEFAULTS.roomsDelta
		) {
			return false;
		}
		return true;
	});

	const normalizedListingAddress = normalizeAddress(
		[listing.street, listing.city].filter(Boolean).join(" ") || null,
	);
	const sameAddress = normalizedListingAddress
		? eligible
				.filter(
					(candidate) =>
						normalizeAddress(
							[candidate.street, candidate.city].filter(Boolean).join(" "),
						) === normalizedListingAddress,
				)
				.slice(0, sampleCount)
		: [];

	for (const radiusKm of RCN_COMPARABLE_DEFAULTS.radiiKm[kind]) {
		const local = removeOutliers(
			eligible.filter((candidate) => candidate.distanceKm <= radiusKm),
		);
		if (local.length < minimumCount) continue;
		const selected = [...local]
			.sort((a, b) => a.distanceKm - b.distanceKm)
			.slice(0, maximumCount);
		const values = selected.map((candidate) => candidate.pricePerM2Pln);
		const weighted = weightedMedian(
			selected.map((candidate) => ({
				value: candidate.pricePerM2Pln,
				weight:
					(1 / (1 + candidate.distanceKm)) *
					(1 - Math.min(0.25, Math.abs(candidate.areaM2 / listing.areaM2 - 1))),
			})),
		);
		const confidence =
			kind === "building"
				? "low"
				: selected.length >= 10 && radiusKm <= 2.5
					? "high"
					: selected.length >= minimumCount
						? "medium"
						: "low";
		return {
			...base,
			status: "ok",
			count: selected.length,
			medianPricePerM2Pln: quantile(values, 0.5),
			p25PricePerM2Pln: quantile(values, 0.25),
			p75PricePerM2Pln: quantile(values, 0.75),
			weightedPricePerM2Pln: weighted,
			radiusKm,
			confidence,
			samples: [...selected]
				.sort((a, b) => a.distanceKm - b.distanceKm)
				.slice(0, sampleCount),
			sameAddress,
		};
	}

	return {
		...base,
		status: "unavailable",
		reason: "insufficient_comparables",
		count: eligible.length,
		medianPricePerM2Pln: null,
		p25PricePerM2Pln: null,
		p75PricePerM2Pln: null,
		weightedPricePerM2Pln: null,
		radiusKm: maxRadius,
		confidence: null,
		samples: [],
		sameAddress,
	};
}

export function comparableMarket(value: string | null): RcnMarket | null {
	return value === "primary" || value === "secondary" ? value : null;
}

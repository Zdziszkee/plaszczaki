import { useCallback, useEffect, useRef, useState } from "react";
import {
	filtersToSearchParams,
	type ListingFilters,
} from "#/lib/listing-filter-types";

export type SourceStat = {
	id: string;
	label: string;
	enabled: boolean;
	color: string;
	count: number;
};

export type ListingsStats = {
	/** Every geocoded offer, ignoring filters. */
	total: number;
	/** Offers matching the active filters. */
	filteredTotal: number;
	byOfferType: Record<string, number>;
	byPeriod: { all: number; today: number; week: number; month: number };
	bySource: SourceStat[];
};

export type ListingsStatsState = {
	stats: ListingsStats | null;
	loading: boolean;
	error: string | null;
	refresh: () => void;
};

const DEBOUNCE_MS = 250;

function isAbortError(error: unknown): boolean {
	return error instanceof DOMException && error.name === "AbortError";
}

/**
 * Global offer counters for the filter UI: totals, per-type counts, rolling
 * publication windows and per-source counts. Counts are global (viewport
 * independent); the in-view count comes from the listings endpoint itself.
 */
export function useListingsStats(filters: ListingFilters): ListingsStatsState {
	const [stats, setStats] = useState<ListingsStats | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [nonce, setNonce] = useState(0);

	const abortRef = useRef<AbortController | null>(null);
	const timerRef = useRef<number | null>(null);

	const refresh = useCallback(() => {
		setNonce((value) => value + 1);
	}, []);

	useEffect(() => {
		const schedule = () => {
			abortRef.current?.abort();
			const controller = new AbortController();
			abortRef.current = controller;

			setLoading(true);
			setError(null);

			const params = filtersToSearchParams(filters);
			fetch(`/api/listings/stats?${params.toString()}`, {
				signal: controller.signal,
			})
				.then(async (response) => {
					if (!response.ok) throw new Error(`HTTP ${response.status}`);
					const payload = (await response.json()) as ListingsStats;
					if (controller.signal.aborted) return;
					setStats(payload);
					setLoading(false);
				})
				.catch((reason: unknown) => {
					if (isAbortError(reason) || controller.signal.aborted) return;
					setError(reason instanceof Error ? reason.message : "Błąd");
					setLoading(false);
				});
		};

		if (timerRef.current !== null) window.clearTimeout(timerRef.current);
		timerRef.current = window.setTimeout(schedule, nonce > 0 ? 0 : DEBOUNCE_MS);

		return () => {
			if (timerRef.current !== null) {
				window.clearTimeout(timerRef.current);
				timerRef.current = null;
			}
			abortRef.current?.abort();
		};
	}, [filters, nonce]);

	return { stats, loading, error, refresh };
}

import type { Map as MapboxMap } from "mapbox-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import {
	filtersToSearchParams,
	type ListingFilters,
} from "#/lib/listing-filter-types";
import { LISTINGS_POINTS_MIN_ZOOM } from "./constants";
import type { ListingFeatureCollection, ListingsMode } from "./listings-types";

type ResponseMeta = {
	mode?: string;
	total?: number;
	returned?: number;
	precision?: number;
	limit?: number;
};

type ListingsResponse = ListingFeatureCollection & {
	meta?: ResponseMeta;
};

export type ListingsLayerState = {
	data: ListingFeatureCollection | null;
	mode: ListingsMode;
	total: number;
	loading: boolean;
	error: string | null;
	refresh: () => void;
};

const DEBOUNCE_MS = 250;

function precisionForZoom(zoom: number): number {
	if (zoom < 7) return 0.5;
	if (zoom < 8) return 0.25;
	if (zoom < 9) return 0.1;
	return 0.05;
}

function bboxSearchParams(map: MapboxMap): Record<string, string> | null {
	const bounds = map.getBounds();
	if (!bounds) return null;
	const minLng = bounds.getWest();
	const maxLng = bounds.getEast();
	const minLat = bounds.getSouth();
	const maxLat = bounds.getNorth();
	if (
		!Number.isFinite(minLng) ||
		!Number.isFinite(maxLng) ||
		!Number.isFinite(minLat) ||
		!Number.isFinite(maxLat)
	) {
		return null;
	}
	return {
		minLng: minLng.toFixed(4),
		maxLng: maxLng.toFixed(4),
		minLat: minLat.toFixed(4),
		maxLat: maxLat.toFixed(4),
	};
}

function isAbortError(error: unknown): boolean {
	return error instanceof DOMException && error.name === "AbortError";
}

export function useListingsLayer({
	map,
	enabled,
	filters,
}: {
	map: MapboxMap | null;
	enabled: boolean;
	filters: ListingFilters;
}): ListingsLayerState {
	const [data, setData] = useState<ListingFeatureCollection | null>(null);
	const [mode, setMode] = useState<ListingsMode>("clusters");
	const [total, setTotal] = useState(0);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [nonce, setNonce] = useState(0);

	const abortRef = useRef<AbortController | null>(null);
	const timerRef = useRef<number | null>(null);
	const lastKeyRef = useRef<string>("");
	const forceRef = useRef(false);
	const modeRef = useRef<ListingsMode>("clusters");

	const refresh = useCallback(() => {
		forceRef.current = true;
		setNonce((value) => value + 1);
	}, []);

	const filtersKey = JSON.stringify(filters);

	useEffect(() => {
		if (!map || !enabled) {
			abortRef.current?.abort();
			setLoading(false);
			return;
		}

		const request = () => {
			const bbox = bboxSearchParams(map);
			if (!bbox) return;

			const zoom = map.getZoom();
			const nextMode: ListingsMode =
				zoom >= LISTINGS_POINTS_MIN_ZOOM ? "points" : "clusters";
			const precision = precisionForZoom(zoom);
			const key = `${nextMode}|${bbox.minLng},${bbox.minLat},${bbox.maxLng},${bbox.maxLat}|${filtersKey}|${precision}|${nonce}`;
			if (!forceRef.current && key === lastKeyRef.current) return;
			forceRef.current = false;
			lastKeyRef.current = key;

			abortRef.current?.abort();
			const controller = new AbortController();
			abortRef.current = controller;

			const params = filtersToSearchParams(filters);
			for (const [name, value] of Object.entries(bbox)) {
				params.set(name, value);
			}
			if (nextMode === "clusters") {
				params.set("precision", String(precision));
				params.set("limit", "3000");
			} else {
				params.set("all", "true");
			}

			const path =
				nextMode === "clusters" ? "/api/listings/clusters" : "/api/listings";

			setMode(nextMode);
			if (modeRef.current !== nextMode) {
				modeRef.current = nextMode;
				setData(null);
			}
			setLoading(true);
			setError(null);

			fetch(`${path}?${params.toString()}`, { signal: controller.signal })
				.then(async (response) => {
					if (!response.ok) {
						throw new Error(`HTTP ${response.status}`);
					}
					const payload = (await response.json()) as ListingsResponse;
					if (controller.signal.aborted) return;
					setData(payload);
					setTotal(payload.meta?.total ?? payload.features.length);
					setLoading(false);
				})
				.catch((reason: unknown) => {
					if (isAbortError(reason) || controller.signal.aborted) return;
					setError(reason instanceof Error ? reason.message : "Błąd");
					setLoading(false);
				});
		};

		const schedule = (delay = DEBOUNCE_MS) => {
			if (timerRef.current !== null) {
				window.clearTimeout(timerRef.current);
			}
			timerRef.current = window.setTimeout(request, delay);
		};

		const onLoad = () => schedule(0);
		const onMoveEnd = () => schedule();

		map.on("load", onLoad);
		map.on("moveend", onMoveEnd);
		if (map.isStyleLoaded()) {
			schedule(0);
		}

		return () => {
			map.off("load", onLoad);
			map.off("moveend", onMoveEnd);
			if (timerRef.current !== null) {
				window.clearTimeout(timerRef.current);
				timerRef.current = null;
			}
			abortRef.current?.abort();
		};
	}, [map, enabled, filtersKey, filters, nonce]);

	useEffect(() => {
		if (!enabled) return;
		const refreshIfVisible = () => {
			if (document.visibilityState === "visible") {
				refresh();
			}
		};
		const interval = window.setInterval(refreshIfVisible, 60_000);
		document.addEventListener("visibilitychange", refreshIfVisible);
		return () => {
			window.clearInterval(interval);
			document.removeEventListener("visibilitychange", refreshIfVisible);
		};
	}, [enabled, refresh]);

	return { data, mode, total, loading, error, refresh };
}

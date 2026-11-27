import type { Map as MapboxMap } from "mapbox-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import { rcnFiltersToSearchParams } from "#/lib/rcn-filters";
import type { RcnFeatureCollection, RcnMapFilters } from "#/lib/rcn-types";
import { RCN_POINTS_MIN_ZOOM } from "./constants";
import type { ListingsMode } from "./listings-types";

type RcnResponse = RcnFeatureCollection & {
	meta?: {
		total?: number;
	};
};

export type RcnLayerState = {
	data: RcnFeatureCollection | null;
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
	const values = {
		minLng: bounds.getWest(),
		maxLng: bounds.getEast(),
		minLat: bounds.getSouth(),
		maxLat: bounds.getNorth(),
	};
	if (!Object.values(values).every(Number.isFinite)) return null;
	return {
		minLng: values.minLng.toFixed(4),
		maxLng: values.maxLng.toFixed(4),
		minLat: values.minLat.toFixed(4),
		maxLat: values.maxLat.toFixed(4),
	};
}

function isAbortError(error: unknown): boolean {
	return error instanceof DOMException && error.name === "AbortError";
}

export function useRcnLayer({
	map,
	enabled,
	filters,
}: {
	map: MapboxMap | null;
	enabled: boolean;
	filters: RcnMapFilters;
}): RcnLayerState {
	const [data, setData] = useState<RcnFeatureCollection | null>(null);
	const [mode, setMode] = useState<ListingsMode>("clusters");
	const [total, setTotal] = useState(0);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [nonce, setNonce] = useState(0);
	const abortRef = useRef<AbortController | null>(null);
	const timerRef = useRef<number | null>(null);
	const lastKeyRef = useRef("");
	const forceRef = useRef(false);
	const modeRef = useRef<ListingsMode>("clusters");
	const filtersKey = JSON.stringify(filters);

	const refresh = useCallback(() => {
		forceRef.current = true;
		setNonce((value) => value + 1);
	}, []);

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
				zoom >= RCN_POINTS_MIN_ZOOM ? "points" : "clusters";
			const precision = precisionForZoom(zoom);
			const key = `${nextMode}|${bbox.minLng},${bbox.minLat},${bbox.maxLng},${bbox.maxLat}|${filtersKey}|${precision}|${nonce}`;
			if (!forceRef.current && key === lastKeyRef.current) return;
			forceRef.current = false;
			lastKeyRef.current = key;
			abortRef.current?.abort();
			const controller = new AbortController();
			abortRef.current = controller;
			const params = rcnFiltersToSearchParams(filters);
			for (const [name, value] of Object.entries(bbox)) params.set(name, value);
			if (nextMode === "clusters") {
				params.set("precision", String(precision));
				params.set("limit", "3000");
			} else {
				params.set("limit", "2000");
			}
			setMode(nextMode);
			if (modeRef.current !== nextMode) {
				modeRef.current = nextMode;
				setData(null);
			}
			setLoading(true);
			setError(null);
			fetch(`/api/rcn?${params.toString()}`, { signal: controller.signal })
				.then(async (response) => {
					if (!response.ok) throw new Error(`HTTP ${response.status}`);
					return (await response.json()) as RcnResponse;
				})
				.then((payload) => {
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
			if (timerRef.current !== null) window.clearTimeout(timerRef.current);
			timerRef.current = window.setTimeout(request, delay);
		};
		const onLoad = () => schedule(0);
		const onMoveEnd = () => schedule();
		map.on("load", onLoad);
		map.on("moveend", onMoveEnd);
		if (map.isStyleLoaded()) schedule(0);
		return () => {
			map.off("load", onLoad);
			map.off("moveend", onMoveEnd);
			if (timerRef.current !== null) {
				window.clearTimeout(timerRef.current);
				timerRef.current = null;
			}
			abortRef.current?.abort();
		};
	}, [map, enabled, filters, filtersKey, nonce]);

	useEffect(() => {
		if (!enabled) return;
		const refreshIfVisible = () => {
			if (document.visibilityState === "visible") refresh();
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

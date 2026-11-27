import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeftIcon } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
	DEFAULT_TOGGLES,
	type LayerToggles,
	LISTINGS_FILTERS_STORAGE_KEY,
	RCN_FILTERS_STORAGE_KEY,
	TOGGLES_STORAGE_KEY,
} from "#/components/map/constants";
import MapControls from "#/components/map/MapControls";
import { useListingsStats } from "#/components/map/useListingsStats";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { Spinner } from "#/components/ui/spinner";
import {
	type ListingFilters,
	sanitizeListingFilters,
} from "#/lib/listing-filter-types";
import { sanitizeRcnFilters } from "#/lib/rcn-filters";
import { DEFAULT_RCN_FILTERS, type RcnMapFilters } from "#/lib/rcn-types";

// mapbox-gl touches `window` at module load, so it must never be imported
// during SSR. The dynamic import only runs after mount on the client.
const MapView = lazy(() => import("#/components/map/MapView"));

export const Route = createFileRoute("/map")({
	head: () => ({
		meta: [{ title: "Mapa Polski — Plaszczaki" }],
	}),
	component: MapPage,
});

const TOGGLE_KEYS = Object.keys(DEFAULT_TOGGLES) as Array<keyof LayerToggles>;

function sanitizeToggles(value: unknown): Partial<LayerToggles> {
	if (!value || typeof value !== "object") return {};
	const input = value as Record<string, unknown>;
	const result: Partial<LayerToggles> = {};
	for (const key of TOGGLE_KEYS) {
		if (typeof input[key] === "boolean") {
			result[key] = input[key] as boolean;
		}
	}
	return result;
}

function MapFallback() {
	return (
		<div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
			<Spinner />
			Ładowanie mapy…
		</div>
	);
}

function MapPage() {
	const [mounted, setMounted] = useState(false);
	const [toggles, setToggles] = useState<LayerToggles>(DEFAULT_TOGGLES);
	const [filters, setFilters] = useState<ListingFilters>({});
	const filtersRef = useRef<ListingFilters>({});
	const [rcnFilters, setRcnFilters] =
		useState<RcnMapFilters>(DEFAULT_RCN_FILTERS);
	const rcnFiltersRef = useRef<RcnMapFilters>(DEFAULT_RCN_FILTERS);
	const { stats, loading: statsLoading } = useListingsStats(filters);

	useEffect(() => {
		setMounted(true);
		document.body.style.overflow = "hidden";
		try {
			const raw = window.localStorage.getItem(TOGGLES_STORAGE_KEY);
			if (raw) {
				const parsed = sanitizeToggles(JSON.parse(raw));
				setToggles((current) => ({ ...current, ...parsed }));
			}
			const rawFilters = window.localStorage.getItem(
				LISTINGS_FILTERS_STORAGE_KEY,
			);
			if (rawFilters) {
				const parsed = sanitizeListingFilters(JSON.parse(rawFilters));
				filtersRef.current = parsed;
				setFilters(parsed);
			}
			const rawRcnFilters = window.localStorage.getItem(
				RCN_FILTERS_STORAGE_KEY,
			);
			if (rawRcnFilters) {
				const parsed = sanitizeRcnFilters(JSON.parse(rawRcnFilters));
				rcnFiltersRef.current = parsed;
				setRcnFilters(parsed);
			}
		} catch {
			// Storage disabled or malformed: keep the defaults.
		}
		return () => {
			document.body.style.overflow = "";
		};
	}, []);

	const onChange = (key: keyof LayerToggles, value: boolean) => {
		setToggles((current) => {
			const next = { ...current, [key]: value };
			try {
				window.localStorage.setItem(TOGGLES_STORAGE_KEY, JSON.stringify(next));
			} catch {
				// Not fatal: the toggle just won't persist.
			}
			return next;
		});
	};

	const onFiltersChange = (patch: Partial<ListingFilters> | "reset") => {
		const next = patch === "reset" ? {} : { ...filtersRef.current, ...patch };
		filtersRef.current = next;
		setFilters(next);
		try {
			window.localStorage.setItem(
				LISTINGS_FILTERS_STORAGE_KEY,
				JSON.stringify(next),
			);
		} catch {
			// Not fatal: the filters just won't persist.
		}
	};

	const onRcnFiltersChange = (patch: Partial<RcnMapFilters> | "reset") => {
		const next =
			patch === "reset"
				? { ...DEFAULT_RCN_FILTERS }
				: { ...rcnFiltersRef.current, ...patch };
		rcnFiltersRef.current = next;
		setRcnFilters(next);
		try {
			window.localStorage.setItem(
				RCN_FILTERS_STORAGE_KEY,
				JSON.stringify(next),
			);
		} catch {
			// Not fatal: the filter just won't persist.
		}
	};

	return (
		<div className="fixed inset-0 z-[60] bg-background text-foreground">
			{mounted ? (
				<Suspense fallback={<MapFallback />}>
					<MapView
						toggles={toggles}
						filters={filters}
						rcnFilters={rcnFilters}
						totalAll={stats?.total ?? 0}
					/>
				</Suspense>
			) : (
				<MapFallback />
			)}

			<div className="pointer-events-none absolute top-4 left-4 z-20 flex flex-col gap-3">
				<div className="pointer-events-auto flex items-center gap-2">
					<Button asChild variant="secondary" size="sm">
						<Link to="/">
							<ArrowLeftIcon data-icon="inline-start" />
							Wróć
						</Link>
					</Button>
					<Badge variant="secondary" className="h-8 px-3">
						Mapa Polski
					</Badge>
				</div>
				<MapControls
					toggles={toggles}
					onChange={onChange}
					filters={filters}
					onFiltersChange={onFiltersChange}
					rcnFilters={rcnFilters}
					onRcnFiltersChange={onRcnFiltersChange}
					stats={stats}
					statsLoading={statsLoading}
				/>
			</div>
		</div>
	);
}

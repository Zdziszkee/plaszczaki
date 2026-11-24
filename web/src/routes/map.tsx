import { createFileRoute, Link } from "@tanstack/react-router";
import { lazy, Suspense, useEffect, useState } from "react";
import {
	DEFAULT_TOGGLES,
	type LayerToggles,
	TOGGLES_STORAGE_KEY,
} from "#/components/map/constants";
import MapControls from "#/components/map/MapControls";

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
		<div className="flex h-full items-center justify-center text-sm text-slate-400">
			Ładowanie mapy…
		</div>
	);
}

function MapPage() {
	const [mounted, setMounted] = useState(false);
	const [toggles, setToggles] = useState<LayerToggles>(DEFAULT_TOGGLES);

	useEffect(() => {
		setMounted(true);
		document.body.style.overflow = "hidden";
		try {
			const raw = window.localStorage.getItem(TOGGLES_STORAGE_KEY);
			if (raw) {
				const parsed = sanitizeToggles(JSON.parse(raw));
				setToggles((current) => ({ ...current, ...parsed }));
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

	return (
		<div className="fixed inset-0 z-[60] bg-slate-950 text-slate-100">
			{mounted ? (
				<Suspense fallback={<MapFallback />}>
					<MapView toggles={toggles} />
				</Suspense>
			) : (
				<MapFallback />
			)}

			<div className="pointer-events-none absolute left-4 top-4 z-20 flex flex-col gap-3">
				<div className="pointer-events-auto flex items-center gap-3">
					<Link
						to="/"
						className="rounded-full border border-white/15 bg-slate-950/80 px-3 py-1.5 text-sm font-semibold text-slate-100 no-underline shadow-lg backdrop-blur transition hover:bg-slate-900"
					>
						← Wróć
					</Link>
					<span className="rounded-full border border-white/10 bg-slate-950/70 px-3 py-1.5 text-sm text-slate-200 backdrop-blur">
						Mapa Polski
					</span>
				</div>
				<MapControls toggles={toggles} onChange={onChange} />
			</div>
		</div>
	);
}

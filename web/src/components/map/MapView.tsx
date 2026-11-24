import mapboxgl from "mapbox-gl";
import { useEffect, useRef, useState } from "react";

import "mapbox-gl/dist/mapbox-gl.css";

import {
	type LayerToggles,
	MAPBOX_TOKEN,
	POLAND_BOUNDS,
	POLAND_CENTER,
	POLAND_MAX_BOUNDS,
	POLAND_MIN_ZOOM,
} from "./constants";
import {
	addBoundaryLayers,
	addContourLayers,
	addHeatmapLayers,
	addRailLayers,
	addTerrainLayers,
	applyToggles,
	sync3d,
} from "./layers";
import { pruneBadTileCache, resetTileCache } from "./tile-cache";

function MissingToken() {
	return (
		<div className="flex h-full items-center justify-center p-6">
			<div className="max-w-md rounded-xl border border-white/10 bg-slate-900/90 p-6 text-center text-sm text-slate-200">
				<p className="mb-2 text-base font-semibold text-slate-50">
					Brak klucza Mapbox
				</p>
				<p className="text-slate-400">
					Dodaj publiczny token do <code>.env.local</code> jako{" "}
					<code>VITE_MAPBOX_TOKEN=pk.…</code> i zrestartuj serwer. Token
					utworzysz na{" "}
					<a
						className="text-sky-300 underline"
						href="https://account.mapbox.com"
						target="_blank"
						rel="noreferrer"
					>
						account.mapbox.com
					</a>
					.
				</p>
			</div>
		</div>
	);
}

export default function MapView({ toggles }: { toggles: LayerToggles }) {
	const containerRef = useRef<HTMLDivElement>(null);
	const mapRef = useRef<mapboxgl.Map | null>(null);
	const popupRef = useRef<mapboxgl.Popup | null>(null);
	const togglesRef = useRef(toggles);
	const [elevation, setElevation] = useState<number | null>(null);

	useEffect(() => {
		togglesRef.current = toggles;
		const map = mapRef.current;
		if (!map) return;
		applyToggles(map, toggles);
	}, [toggles]);

	useEffect(() => {
		if (!MAPBOX_TOKEN || !containerRef.current || mapRef.current) return;

		const map = new mapboxgl.Map({
			container: containerRef.current,
			accessToken: MAPBOX_TOKEN,
			style: "mapbox://styles/mapbox/standard",
			projection: "mercator",
			center: POLAND_CENTER,
			zoom: 5.9,
			pitch: 0,
			bearing: 0,
			minZoom: POLAND_MIN_ZOOM,
			maxZoom: 19,
			maxBounds: POLAND_MAX_BOUNDS,
			attributionControl: true,
			config: {
				basemap: {
					lightPreset: "night",
					showPlaceLabels: true,
					showRoadLabels: true,
					showPointOfInterestLabels: true,
					showTransitLabels: true,
					showAdminBoundaries: true,
					show3dObjects: false,
				},
			},
		});
		mapRef.current = map;
		// Debug hook for browser-console inspection.
		(window as unknown as { __map?: mapboxgl.Map }).__map = map;

		map.addControl(
			new mapboxgl.NavigationControl({ visualizePitch: true }),
			"top-right",
		);
		map.addControl(new mapboxgl.FullscreenControl(), "top-right");
		map.addControl(
			new mapboxgl.ScaleControl({ unit: "metric", maxWidth: 140 }),
			"bottom-left",
		);

		const syncDetail = () => sync3d(map, togglesRef.current.objects3d);

		// `style.load` fires after every style (re)load; custom sources and
		// layers must be (re)created there.
		map.on("style.load", () => {
			addTerrainLayers(map);
			addContourLayers(map);
			addRailLayers(map);
			addBoundaryLayers(map);
			addHeatmapLayers(map);
			applyToggles(map, togglesRef.current);
		});

		map.on("load", () => {
			// Fit once the style is ready: before `load` the transform still
			// uses the pre-resize canvas and fitBounds lands at town zoom.
			try {
				map.fitBounds(POLAND_BOUNDS, { padding: 24, duration: 0 });
			} catch {
				// The constructor center/zoom already frame the country.
			}
			pruneBadTileCache(true);
			syncDetail();
			map.on("zoomend", syncDetail);
		});

		map.on("idle", () => pruneBadTileCache());
		map.on("error", (event) => {
			const message = event.error?.message ?? "";
			if (message.includes("Expected varint not more than 10 bytes")) {
				resetTileCache();
			}
		});

		let pending: mapboxgl.LngLat | null = null;
		let frame = 0;
		const onMouseMove = (event: mapboxgl.MapMouseEvent) => {
			if (!togglesRef.current.elevation) {
				setElevation(null);
				return;
			}
			pending = event.lngLat;
			if (frame) return;
			frame = requestAnimationFrame(() => {
				frame = 0;
				const lngLat = pending;
				if (!lngLat) return;
				const value = map.queryTerrainElevation(lngLat, {
					exaggerated: false,
				});
				setElevation(
					typeof value === "number" && Number.isFinite(value)
						? Math.round(value)
						: null,
				);
			});
		};
		const onMouseOut = () => setElevation(null);
		map.on("mousemove", onMouseMove);
		map.on("mouseout", onMouseOut);

		map.on("click", (event) => {
			if (!togglesRef.current.elevation) return;
			const value = map.queryTerrainElevation(event.lngLat, {
				exaggerated: false,
			});
			const height =
				typeof value === "number" && Number.isFinite(value)
					? `${Math.round(value)} m n.p.m.`
					: "brak danych wysokościowych";
			const lat = event.lngLat.lat.toFixed(5);
			const lng = event.lngLat.lng.toFixed(5);
			popupRef.current?.remove();
			popupRef.current = new mapboxgl.Popup({ closeButton: true, offset: 12 })
				.setLngLat(event.lngLat)
				.setHTML(
					`<div style="font:12px/1.4 system-ui,sans-serif;color:#e2e8f0">
						<div style="font-weight:600;margin-bottom:2px">${height}</div>
						<div style="color:#94a3b8">${lat}, ${lng}</div>
					</div>`,
				)
				.addTo(map);
		});

		return () => {
			if (frame) cancelAnimationFrame(frame);
			popupRef.current?.remove();
			popupRef.current = null;
			(window as unknown as { __map?: mapboxgl.Map }).__map = undefined;
			map.remove();
			mapRef.current = null;
		};
	}, []);

	if (!MAPBOX_TOKEN) return <MissingToken />;

	return (
		<div className="relative h-full w-full">
			<div ref={containerRef} className="h-full w-full" />
			{toggles.elevation && (
				<div className="pointer-events-none absolute bottom-4 right-4 z-10 min-w-36 rounded-lg border border-white/10 bg-slate-950/85 px-3 py-2 text-slate-100 shadow-lg backdrop-blur">
					<div className="text-[10px] uppercase tracking-wide text-slate-400">
						Wysokość (n.p.m.)
					</div>
					<div className="font-mono text-base tabular-nums">
						{elevation === null ? "—" : `${elevation} m`}
					</div>
				</div>
			)}
		</div>
	);
}

import mapboxgl from "mapbox-gl";
import { useEffect, useRef, useState } from "react";

import "mapbox-gl/dist/mapbox-gl.css";

import { Button } from "#/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";
import type { ListingFilters } from "#/lib/listing-filter-types";
import {
	LAYER_IDS,
	type LayerToggles,
	LISTINGS_AGGREGATE_MIN_ZOOM,
	MAPBOX_TOKEN,
	POLAND_BOUNDS,
	POLAND_CENTER,
	POLAND_MAX_BOUNDS,
	POLAND_MIN_ZOOM,
} from "./constants";
import ListingsStatus from "./ListingsStatus";
import {
	addBoundaryLayers,
	addContourLayers,
	addHeatmapLayers,
	addKolejPlusLayers,
	addPlannedRailLayers,
	addRailLayers,
	addTerrainLayers,
	applyToggles,
	LISTINGS_SOURCE,
	PLANNED_RAIL_LINE_LAYERS,
	sync3d,
	syncListingLayer,
} from "./layers";
import type { ListingFeatureCollection, ListingsMode } from "./listings-types";
import { pruneBadTileCache, resetTileCache } from "./tile-cache";
import { useListingsLayer } from "./useListingsLayer";

const HTML_ESCAPES: Record<string, string> = {
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	'"': "&quot;",
	"'": "&#39;",
};

function escapeHtml(value: unknown): string {
	return String(value ?? "").replace(
		/[&<>"']/g,
		(character) => HTML_ESCAPES[character] ?? character,
	);
}

function featureProperties(feature: unknown): Record<string, unknown> | null {
	if (!feature || typeof feature !== "object" || !("properties" in feature)) {
		return null;
	}
	const { properties } = feature as { properties?: unknown };
	if (!properties || typeof properties !== "object") return null;
	return properties as Record<string, unknown>;
}

/** Popup content for a planned/under-construction rail segment. */
function plannedRailPopupHtml(properties: Record<string, unknown>): string {
	const status =
		properties.status === "construction"
			? "w budowie"
			: properties.status === "schematic"
				? "przebieg orientacyjny"
				: "planowana";
	const statusColor =
		properties.status === "construction"
			? "#f472b6"
			: properties.status === "schematic"
				? "#a3e635"
				: "#e879f9";
	const name = typeof properties.name === "string" ? properties.name : "";
	const ref = typeof properties.ref === "string" ? properties.ref : "";
	const project =
		typeof properties.project === "string" ? properties.project : null;
	const title =
		project || name || (ref ? `Linia kolejowa nr ${ref}` : "Linia kolejowa");

	const details: string[] = [];
	if (name && ref && !project) details.push(`Linia nr ${ref}`);
	if (properties.program === "port-polska") {
		details.push("Program: Port Polska (KDP)");
		details.push("Cel realizacji: 2035");
	} else if (properties.program === "kolej-plus") {
		details.push("Program: Kolej Plus");
		if (properties.status === "existing") {
			details.push("Modernizacja istniejącej linii");
		}
		if (properties.status === "schematic") {
			details.push("Brak oficjalnej geometrii — punkt orientacyjny");
		}
	}
	if (typeof properties.target === "string") {
		details.push(`Plan realizacji: ${properties.target}`);
	}
	if (typeof properties.stage === "string") {
		details.push(`Etap: ${properties.stage}`);
	}
	if (typeof properties.usage === "string") {
		details.push(
			properties.usage === "main"
				? "Linia magistralna"
				: properties.usage === "branch"
					? "Linia lokalna"
					: `Przeznaczenie: ${properties.usage}`,
		);
	}
	if (typeof properties.maxspeed === "string") {
		details.push(`Prędkość do ${properties.maxspeed} km/h`);
	}
	if (typeof properties.operator === "string") {
		details.push(`Inwestor: ${properties.operator}`);
	}

	const detailsHtml = details
		.map(
			(detail) =>
				`<div style="color:var(--muted-foreground)">${escapeHtml(detail)}</div>`,
		)
		.join("");

	return `<div style="font:12px/1.45 system-ui,sans-serif;color:var(--popover-foreground);max-width:280px">
		<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
			<span style="width:8px;height:8px;border-radius:9999px;background:${statusColor}"></span>
			<span style="font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:var(--muted-foreground)">${status}</span>
		</div>
		<div style="font-weight:600;margin-bottom:4px">${escapeHtml(title)}</div>
		${detailsHtml}
		<div style="margin-top:6px;font-size:10px;color:var(--muted-foreground)">Dane: OpenStreetMap (ODbL)</div>
	</div>`;
}

const PROPERTY_LABELS: Record<string, string> = {
	flat: "mieszkanie",
	house: "dom",
	plot: "działka",
	commercial: "lokal",
	garage: "garaż",
	room: "pokój",
};

const OFFER_LABELS: Record<string, string> = {
	sale: "sprzedaż",
	rent: "wynajem",
	auction: "aukcja",
	lease: "dzierżawa",
};

function formatPln(value: number): string {
	return new Intl.NumberFormat("pl-PL", {
		style: "currency",
		currency: "PLN",
		maximumFractionDigits: 0,
	}).format(value);
}

/** Popup content for a scraped property listing. */
function listingPopupHtml(properties: Record<string, unknown>): string {
	const title =
		typeof properties.title === "string" ? properties.title : "Oferta";
	const url = typeof properties.url === "string" ? properties.url : "";
	const source = typeof properties.source === "string" ? properties.source : "";
	const sourceLabel =
		typeof properties.sourceLabel === "string"
			? properties.sourceLabel
			: source;
	const color =
		typeof properties.color === "string" ? properties.color : "#94a3b8";
	const price =
		typeof properties.pricePln === "number"
			? formatPln(properties.pricePln)
			: properties.priceKind === "call"
				? "Cena do uzgodnienia"
				: "Cena nieznana";
	const pricePerM2 =
		typeof properties.pricePerM2Pln === "number"
			? `${formatPln(properties.pricePerM2Pln)}/m²`
			: "";

	const details: string[] = [];
	const offerType =
		typeof properties.offerType === "string"
			? (OFFER_LABELS[properties.offerType] ?? properties.offerType)
			: "";
	if (offerType) details.push(offerType);
	if (typeof properties.propertyType === "string") {
		details.push(
			PROPERTY_LABELS[properties.propertyType] ?? properties.propertyType,
		);
	}
	if (typeof properties.rooms === "number") {
		details.push(`${properties.rooms} pok.`);
	}
	if (typeof properties.areaM2 === "number") {
		details.push(`${properties.areaM2.toLocaleString("pl-PL")} m²`);
	}
	if (typeof properties.city === "string") details.push(properties.city);

	const image =
		typeof properties.image === "string" && properties.image
			? `<img src="${escapeHtml(properties.image)}" alt="" style="width:100%;max-height:110px;object-fit:cover;border-radius:6px;margin-bottom:6px" loading="lazy" />`
			: "";
	const link = url
		? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" style="display:inline-block;margin-top:7px;color:inherit;font-weight:600;text-decoration:underline">${sourceLabel ? `Otwórz na ${escapeHtml(sourceLabel)}` : "Otwórz ofertę"}</a>`
		: "";
	const sourceLine = sourceLabel
		? `<div style="display:flex;align-items:center;gap:5px;margin-bottom:5px">
				<span style="width:8px;height:8px;border-radius:9999px;background:${escapeHtml(color)};flex:none"></span>
				<span style="font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:var(--muted-foreground)">${escapeHtml(sourceLabel)}</span>
			</div>`
		: "";

	return `<div style="font:12px/1.45 system-ui,sans-serif;color:var(--popover-foreground);max-width:260px">
		${image}
		${sourceLine}
		<div style="font-size:16px;font-weight:750;margin-bottom:1px">${escapeHtml(price)}</div>
		${pricePerM2 ? `<div style="font-size:11px;color:var(--muted-foreground);margin-bottom:4px">${escapeHtml(pricePerM2)}</div>` : ""}
		<div style="font-weight:600;margin-bottom:4px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(title)}</div>
		<div style="color:var(--muted-foreground)">${details.map(escapeHtml).join(" · ")}</div>
		${link}
	</div>`;
}

/** Popup for a low-zoom aggregate cell. */
function aggregatePopupHtml(properties: Record<string, unknown>): string {
	const count = typeof properties.count === "number" ? properties.count : 0;
	const minPrice =
		typeof properties.minPrice === "number" ? properties.minPrice : null;
	const maxPrice =
		typeof properties.maxPrice === "number" ? properties.maxPrice : null;
	const priceRange =
		minPrice !== null && maxPrice !== null
			? minPrice === maxPrice
				? formatPln(minPrice)
				: `${formatPln(minPrice)} – ${formatPln(maxPrice)}`
			: "Brak danych o cenach";

	return `<div style="font:12px/1.45 system-ui,sans-serif;color:var(--popover-foreground);max-width:220px">
		<div style="font-size:16px;font-weight:750;margin-bottom:2px">${count.toLocaleString("pl-PL")} ofert</div>
		<div style="color:var(--muted-foreground);margin-bottom:8px">${escapeHtml(priceRange)}</div>
		<button type="button" data-aggregate-zoom="1" style="width:100%;cursor:pointer;border:1px solid var(--border);border-radius:6px;background:var(--secondary);color:var(--secondary-foreground);padding:5px 8px;font:inherit;font-weight:600">Przybliż</button>
	</div>`;
}

function MissingToken() {
	return (
		<div className="flex h-full items-center justify-center p-6">
			<Card className="max-w-md text-center">
				<CardHeader>
					<CardTitle>Brak klucza Mapbox</CardTitle>
					<CardDescription>
						Dodaj publiczny token do <code>.env.local</code> jako{" "}
						<code>VITE_MAPBOX_TOKEN=pk.…</code> i zrestartuj serwer.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<Button asChild variant="link" className="px-0">
						<a
							href="https://account.mapbox.com"
							target="_blank"
							rel="noreferrer"
						>
							Utwórz token na account.mapbox.com
						</a>
					</Button>
				</CardContent>
			</Card>
		</div>
	);
}

export default function MapView({
	toggles,
	filters,
	totalAll,
}: {
	toggles: LayerToggles;
	filters: ListingFilters;
	totalAll: number;
}) {
	const containerRef = useRef<HTMLDivElement>(null);
	const mapRef = useRef<mapboxgl.Map | null>(null);
	const popupRef = useRef<mapboxgl.Popup | null>(null);
	const togglesRef = useRef(toggles);
	const listingsDataRef = useRef<ListingFeatureCollection | null>(null);
	const listingsModeRef = useRef<ListingsMode>("clusters");
	const [map, setMap] = useState<mapboxgl.Map | null>(null);
	const [elevation, setElevation] = useState<number | null>(null);
	const listings = useListingsLayer({
		map,
		enabled: toggles.listings,
		filters,
	});

	useEffect(() => {
		togglesRef.current = toggles;
		if (!map) return;
		applyToggles(map, toggles);
	}, [map, toggles]);

	useEffect(() => {
		if (!map) return;
		listingsDataRef.current = listings.data;
		listingsModeRef.current = listings.mode;
		const sync = () => {
			if (!map.isStyleLoaded()) return;
			syncListingLayer(
				map,
				toggles.listings,
				listings.mode,
				listings.data ?? undefined,
			);
			map.off("idle", sync);
		};
		sync();
		map.on("idle", sync);
		return () => {
			map.off("idle", sync);
		};
	}, [map, toggles.listings, listings.data, listings.mode]);

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
		setMap(map);
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
			addPlannedRailLayers(map);
			addKolejPlusLayers(map);
			addBoundaryLayers(map);
			addHeatmapLayers(map);
			applyToggles(map, togglesRef.current);
			syncListingLayer(
				map,
				togglesRef.current.listings,
				listingsModeRef.current,
				listingsDataRef.current ?? undefined,
			);
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
		let pendingPoint: mapboxgl.Point | null = null;
		let frame = 0;
		const onMouseMove = (event: mapboxgl.MapMouseEvent) => {
			pending = event.lngLat;
			pendingPoint = event.point;
			if (frame) return;
			frame = requestAnimationFrame(() => {
				frame = 0;
				const lngLat = pending;
				const point = pendingPoint;
				if (!lngLat) return;

				const plannedLayers = togglesRef.current.plannedRail
					? PLANNED_RAIL_LINE_LAYERS.filter((id) => map.getLayer(id))
					: [];
				const hoveringPlanned =
					point && plannedLayers.length > 0
						? map.queryRenderedFeatures(point, { layers: plannedLayers })
								.length > 0
						: false;
				const listingLayers = togglesRef.current.listings
					? [LAYER_IDS.listingPoint].filter((id) => map.getLayer(id))
					: [];
				const hoveringListing =
					point && listingLayers.length > 0
						? map.queryRenderedFeatures(point, { layers: listingLayers })
								.length > 0
						: false;
				const aggregateLayers = togglesRef.current.listings
					? [LAYER_IDS.listingAggregate].filter((id) => map.getLayer(id))
					: [];
				const hoveringAggregate =
					point && aggregateLayers.length > 0
						? map.queryRenderedFeatures(point, { layers: aggregateLayers })
								.length > 0
						: false;
				const clusterLayers = togglesRef.current.listings
					? [LAYER_IDS.listingClusters].filter((id) => map.getLayer(id))
					: [];
				const hoveringCluster =
					point && clusterLayers.length > 0
						? map.queryRenderedFeatures(point, { layers: clusterLayers })
								.length > 0
						: false;
				map.getCanvas().style.cursor =
					hoveringPlanned ||
					hoveringListing ||
					hoveringAggregate ||
					hoveringCluster
						? "pointer"
						: "";

				if (!togglesRef.current.elevation) {
					setElevation(null);
					return;
				}
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
		const onMouseOut = () => {
			map.getCanvas().style.cursor = "";
			setElevation(null);
		};
		map.on("mousemove", onMouseMove);
		map.on("mouseout", onMouseOut);

		map.on("click", (event) => {
			const aggregateLayers = togglesRef.current.listings
				? [LAYER_IDS.listingAggregate].filter((id) => map.getLayer(id))
				: [];
			const aggregateFeature =
				aggregateLayers.length > 0
					? map.queryRenderedFeatures(event.point, {
							layers: aggregateLayers,
						})[0]
					: undefined;
			if (aggregateFeature) {
				const geometry = (
					aggregateFeature as unknown as {
						geometry?: { type: string; coordinates: [number, number] };
					}
				).geometry;
				if (geometry?.type !== "Point") return;
				const properties = featureProperties(aggregateFeature);
				if (!properties) return;
				const [lng, lat] = geometry.coordinates;
				popupRef.current?.remove();
				const popup = new mapboxgl.Popup({
					closeButton: true,
					offset: 12,
					maxWidth: "240px",
				})
					.setLngLat([lng, lat])
					.setHTML(aggregatePopupHtml(properties))
					.addTo(map);
				popupRef.current = popup;
				popup
					.getElement()
					?.querySelector<HTMLButtonElement>("[data-aggregate-zoom]")
					?.addEventListener("click", () => {
						popup.remove();
						map.easeTo({
							center: [lng, lat],
							zoom: Math.max(
								map.getZoom() + 2,
								LISTINGS_AGGREGATE_MIN_ZOOM + 1,
							),
						});
					});
				return;
			}

			const listingLayers = togglesRef.current.listings
				? [LAYER_IDS.listingPoint].filter((id) => map.getLayer(id))
				: [];
			const listingFeature =
				listingLayers.length > 0
					? map.queryRenderedFeatures(event.point, { layers: listingLayers })[0]
					: undefined;
			const listingProperties = featureProperties(listingFeature);
			if (listingProperties) {
				const geometry = (
					listingFeature as unknown as {
						geometry?: { type: string; coordinates: [number, number] };
					}
				)?.geometry;
				const center: [number, number] =
					geometry?.type === "Point"
						? geometry.coordinates
						: [event.lngLat.lng, event.lngLat.lat];
				popupRef.current?.remove();
				popupRef.current = new mapboxgl.Popup({
					closeButton: true,
					offset: 12,
					maxWidth: "300px",
				})
					.setLngLat(center)
					.setHTML(listingPopupHtml(listingProperties))
					.addTo(map);
				return;
			}

			const clusterLayers = togglesRef.current.listings
				? [LAYER_IDS.listingClusters].filter((id) => map.getLayer(id))
				: [];
			const clusterFeature =
				clusterLayers.length > 0
					? map.queryRenderedFeatures(event.point, { layers: clusterLayers })[0]
					: undefined;
			if (clusterFeature) {
				const geometry = (
					clusterFeature as unknown as {
						geometry?: { type: string; coordinates: [number, number] };
					}
				).geometry;
				const clusterId = Number(featureProperties(clusterFeature)?.cluster_id);
				const source = map.getSource(LISTINGS_SOURCE) as
					| { getClusterExpansionZoom: (id: number) => Promise<number> }
					| undefined;
				if (
					geometry?.type === "Point" &&
					source &&
					Number.isFinite(clusterId)
				) {
					const [lng, lat] = geometry.coordinates;
					source
						.getClusterExpansionZoom(clusterId)
						.then((zoom) => {
							map.easeTo({ center: [lng, lat], zoom });
						})
						.catch(() => {
							// The cluster may disappear between click and expansion.
						});
				}
				return;
			}

			const plannedLayers = PLANNED_RAIL_LINE_LAYERS.filter((id) =>
				map.getLayer(id),
			);
			const plannedFeature =
				plannedLayers.length > 0
					? map.queryRenderedFeatures(event.point, { layers: plannedLayers })[0]
					: undefined;
			const plannedProperties = featureProperties(plannedFeature);
			if (plannedProperties) {
				popupRef.current?.remove();
				popupRef.current = new mapboxgl.Popup({
					closeButton: true,
					offset: 12,
					maxWidth: "320px",
				})
					.setLngLat(event.lngLat)
					.setHTML(plannedRailPopupHtml(plannedProperties))
					.addTo(map);
				return;
			}

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
					`<div style="font:12px/1.4 system-ui,sans-serif;color:var(--popover-foreground)">
						<div style="font-weight:600;margin-bottom:2px">${height}</div>
						<div style="color:var(--muted-foreground)">${lat}, ${lng}</div>
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
			setMap(null);
		};
	}, []);

	if (!MAPBOX_TOKEN) return <MissingToken />;

	return (
		<div className="relative h-full w-full">
			<div ref={containerRef} className="h-full w-full" />
			<ListingsStatus
				enabled={toggles.listings}
				loading={listings.loading}
				total={listings.total}
				totalAll={totalAll}
				mode={listings.mode}
				error={listings.error}
				onRetry={listings.refresh}
				onRefresh={listings.refresh}
			/>
			{toggles.elevation && (
				<Card className="pointer-events-none absolute right-4 bottom-16 z-20 min-w-36 gap-0 bg-card/90 py-2 shadow-lg backdrop-blur">
					<CardContent className="px-3">
						<div className="text-xs text-muted-foreground">
							Wysokość (n.p.m.)
						</div>
						<div className="font-mono text-base tabular-nums">
							{elevation === null ? "—" : `${elevation} m`}
						</div>
					</CardContent>
				</Card>
			)}
		</div>
	);
}

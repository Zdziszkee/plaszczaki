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
import type { ListingDetails } from "#/lib/listing-details";
import type { ListingFilters } from "#/lib/listing-filter-types";
import type {
	RcnComparables,
	RcnFeatureCollection,
	RcnMapFilters,
} from "#/lib/rcn-types";
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
	syncRcnLayer,
} from "./layers";
import {
	escapeHtml,
	formatPln,
	type ListingDetailsState,
	listingPopupHtml,
} from "./listing-popup";
import type { ListingFeatureCollection, ListingsMode } from "./listings-types";
import { rcnPopupHtml } from "./rcn-popup";
import { pruneBadTileCache, resetTileCache } from "./tile-cache";
import { useListingsLayer } from "./useListingsLayer";
import { useRcnLayer } from "./useRcnLayer";

const LISTING_DETAILS_CACHE_TTL_MS = 60_000;

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
	rcnFilters,
	totalAll,
}: {
	toggles: LayerToggles;
	filters: ListingFilters;
	rcnFilters: RcnMapFilters;
	totalAll: number;
}) {
	const containerRef = useRef<HTMLDivElement>(null);
	const mapRef = useRef<mapboxgl.Map | null>(null);
	const popupRef = useRef<mapboxgl.Popup | null>(null);
	const detailsRequestRef = useRef<AbortController | null>(null);
	const rcnRequestRef = useRef<AbortController | null>(null);
	const detailsCacheRef = useRef(
		new Map<number, { details: ListingDetails; cachedAt: number }>(),
	);
	const togglesRef = useRef(toggles);
	const listingsDataRef = useRef<ListingFeatureCollection | null>(null);
	const listingsModeRef = useRef<ListingsMode>("clusters");
	const rcnDataRef = useRef<RcnFeatureCollection | null>(null);
	const rcnModeRef = useRef<ListingsMode>("clusters");
	const [map, setMap] = useState<mapboxgl.Map | null>(null);
	const [elevation, setElevation] = useState<number | null>(null);
	const listings = useListingsLayer({
		map,
		enabled: toggles.listings,
		filters,
	});
	const rcn = useRcnLayer({
		map,
		enabled: toggles.rcn,
		filters: rcnFilters,
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
		if (!map) return;
		rcnDataRef.current = rcn.data;
		rcnModeRef.current = rcn.mode;
		const sync = () => {
			if (!map.isStyleLoaded()) return;
			syncRcnLayer(map, toggles.rcn, rcn.mode, rcn.data ?? undefined);
			map.off("idle", sync);
		};
		sync();
		map.on("idle", sync);
		return () => {
			map.off("idle", sync);
		};
	}, [map, toggles.rcn, rcn.data, rcn.mode]);

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
			syncRcnLayer(
				map,
				togglesRef.current.rcn,
				rcnModeRef.current,
				rcnDataRef.current ?? undefined,
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

		const closePopup = () => {
			detailsRequestRef.current?.abort();
			detailsRequestRef.current = null;
			rcnRequestRef.current?.abort();
			rcnRequestRef.current = null;
			const popup = popupRef.current;
			popupRef.current = null;
			popup?.remove();
		};

		const showListingPopup = (
			properties: Record<string, unknown>,
			center: [number, number],
		) => {
			closePopup();
			const popup = new mapboxgl.Popup({
				closeButton: true,
				offset: 12,
				maxWidth: "360px",
				className: "listing-popup-container",
			});
			let requestController: AbortController | null = null;
			let rcnController: AbortController | null = null;

			const render = (state: ListingDetailsState) => {
				if (popupRef.current !== popup) return;
				popup.setHTML(listingPopupHtml(properties, state));
				if (state.status === "error") {
					popup
						.getElement()
						?.querySelector<HTMLButtonElement>("[data-listing-retry]")
						?.addEventListener("click", loadDetails);
				}
			};

			function loadRcn(id: number, details: ListingDetails) {
				rcnController?.abort();
				render({
					status: "ready",
					details,
					rcn: { status: "loading" },
				});
				const controller = new AbortController();
				rcnController = controller;
				rcnRequestRef.current = controller;
				fetch(`/api/listings/${id}/rcn`, {
					signal: controller.signal,
					headers: { Accept: "application/json" },
				})
					.then(async (response) => {
						if (!response.ok) throw new Error(`HTTP ${response.status}`);
						return (await response.json()) as RcnComparables;
					})
					.then((data) => {
						if (controller.signal.aborted || popupRef.current !== popup) return;
						rcnController = null;
						rcnRequestRef.current = null;
						render({
							status: "ready",
							details,
							rcn: { status: "ready", data },
						});
					})
					.catch(() => {
						if (controller.signal.aborted || popupRef.current !== popup) return;
						rcnController = null;
						rcnRequestRef.current = null;
						render({ status: "ready", details, rcn: { status: "error" } });
					});
			}

			function loadDetails() {
				requestController?.abort();
				const rawId = properties.id;
				const id = typeof rawId === "number" ? rawId : Number(rawId);
				if (!Number.isSafeInteger(id) || id <= 0) {
					render({ status: "error" });
					return;
				}

				const cached = detailsCacheRef.current.get(id);
				if (
					cached &&
					Date.now() - cached.cachedAt < LISTING_DETAILS_CACHE_TTL_MS
				) {
					loadRcn(id, cached.details);
					return;
				}
				detailsCacheRef.current.delete(id);

				render({ status: "loading" });
				const controller = new AbortController();
				requestController = controller;
				detailsRequestRef.current = controller;
				fetch(`/api/listings/${id}`, {
					signal: controller.signal,
					headers: { Accept: "application/json" },
				})
					.then(async (response) => {
						if (!response.ok) throw new Error(`HTTP ${response.status}`);
						return (await response.json()) as ListingDetails;
					})
					.then((details) => {
						if (controller.signal.aborted || popupRef.current !== popup) {
							return;
						}
						detailsCacheRef.current.set(id, {
							details,
							cachedAt: Date.now(),
						});
						requestController = null;
						detailsRequestRef.current = null;
						loadRcn(id, details);
					})
					.catch(() => {
						if (controller.signal.aborted || popupRef.current !== popup) {
							return;
						}
						requestController = null;
						detailsRequestRef.current = null;
						render({ status: "error" });
					});
			}

			popup.on("close", () => {
				const controller = requestController;
				controller?.abort();
				requestController = null;
				if (detailsRequestRef.current === controller) {
					detailsRequestRef.current = null;
				}
				rcnController?.abort();
				rcnController = null;
				rcnRequestRef.current = null;
				if (popupRef.current === popup) popupRef.current = null;
			});
			popupRef.current = popup;
			render({ status: "loading" });
			popup.setLngLat(center).addTo(map);
			loadDetails();
		};

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
					? [LAYER_IDS.listingPoint, LAYER_IDS.listingScoreLabel].filter((id) =>
							map.getLayer(id),
						)
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
				const rcnLayers = togglesRef.current.rcn
					? [LAYER_IDS.rcnCluster, LAYER_IDS.rcnPoint].filter((id) =>
							map.getLayer(id),
						)
					: [];
				const hoveringRcn =
					point && rcnLayers.length > 0
						? map.queryRenderedFeatures(point, { layers: rcnLayers }).length > 0
						: false;
				map.getCanvas().style.cursor =
					hoveringPlanned ||
					hoveringListing ||
					hoveringAggregate ||
					hoveringCluster ||
					hoveringRcn
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
				closePopup();
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
				? [LAYER_IDS.listingPoint, LAYER_IDS.listingScoreLabel].filter((id) =>
						map.getLayer(id),
					)
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
				showListingPopup(listingProperties, center);
				return;
			}

			const rcnLayers = togglesRef.current.rcn
				? [LAYER_IDS.rcnCluster, LAYER_IDS.rcnPoint].filter((id) =>
						map.getLayer(id),
					)
				: [];
			const rcnFeature =
				rcnLayers.length > 0
					? map.queryRenderedFeatures(event.point, { layers: rcnLayers })[0]
					: undefined;
			const rcnProperties = featureProperties(rcnFeature);
			if (rcnProperties) {
				const geometry = (
					rcnFeature as unknown as {
						geometry?: { type: string; coordinates: [number, number] };
					}
				)?.geometry;
				const center: [number, number] =
					geometry?.type === "Point"
						? geometry.coordinates
						: [event.lngLat.lng, event.lngLat.lat];
				closePopup();
				const popup = new mapboxgl.Popup({
					closeButton: true,
					offset: 12,
					maxWidth: "300px",
				})
					.setLngLat(center)
					.setHTML(rcnPopupHtml(rcnProperties))
					.addTo(map);
				popupRef.current = popup;
				popup
					.getElement()
					?.querySelector<HTMLButtonElement>("[data-rcn-zoom]")
					?.addEventListener("click", () => {
						popup.remove();
						map.easeTo({
							center,
							zoom: Math.max(
								map.getZoom() + 2,
								LISTINGS_AGGREGATE_MIN_ZOOM + 1,
							),
						});
					});
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
				closePopup();
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
				closePopup();
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
			closePopup();
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
			closePopup();
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

import { createFileRoute } from "@tanstack/react-router";
import { count, max, min, sql } from "drizzle-orm";
import { db } from "#/db";
import { listing } from "#/db/schema";
import { aggregateHexagons } from "#/lib/hex-aggregation";
import { parseBbox, parseListingFilters } from "#/lib/listing-filter-types";
import {
	buildListingConditions,
	combineConditions,
} from "#/lib/listing-filters";

const DEFAULT_PRECISION = 0.25;
const MIN_PRECISION = 0.002;
const MAX_PRECISION = 1.0;

function numberParam(value: string | null): number | undefined {
	if (!value) return undefined;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : undefined;
}

export const Route = createFileRoute("/api/listings/clusters")({
	server: {
		handlers: {
			GET: async ({ request }) => {
				const search = new URL(request.url).searchParams;
				const bbox = parseBbox(search);
				if (!bbox) {
					return Response.json(
						{ error: "minLat, maxLat, minLng and maxLng are required" },
						{ status: 400 },
					);
				}

				const filters = parseListingFilters(search);
				const conditions = buildListingConditions(filters, bbox);
				const where = combineConditions(conditions);

				const requestedPrecision =
					numberParam(search.get("precision")) ?? DEFAULT_PRECISION;
				const precision = Math.min(
					MAX_PRECISION,
					Math.max(MIN_PRECISION, requestedPrecision),
				);

				const requestedLimit = numberParam(search.get("limit")) ?? 2000;
				const limit = Math.min(Math.max(Math.trunc(requestedLimit), 1), 5000);

				// Reduce raw rows in SQLite before assigning hexagons in JS.
				// Fine bins at one quarter of the requested precision keep
				// centroids accurate without returning every raw point.
				const finePrecision = precision / 4;
				const latBin = sql<number>`floor(${listing.lat} / ${finePrecision}) * ${finePrecision}`;
				const lngBin = sql<number>`floor(${listing.lng} / ${finePrecision}) * ${finePrecision}`;

				const [totalRow] = await db
					.select({ total: count() })
					.from(listing)
					.where(where);

				const rows = await db
					.select({
						lat: sql<number>`avg(${listing.lat})`.mapWith(Number),
						lng: sql<number>`avg(${listing.lng})`.mapWith(Number),
						cellCount: count(),
						minPrice: min(listing.pricePln),
						maxPrice: max(listing.pricePln),
					})
					.from(listing)
					.where(where)
					.groupBy(latBin, lngBin);

				const aggregates = aggregateHexagons(
					rows.flatMap((row) =>
						row.lat === null || row.lng === null
							? []
							: [
									{
										lat: row.lat,
										lng: row.lng,
										count: row.cellCount,
										minPrice: row.minPrice,
										maxPrice: row.maxPrice,
									},
								],
					),
					precision,
				).slice(0, limit);

				const features = aggregates.map((row) => ({
					type: "Feature" as const,
					id: row.id,
					geometry: {
						type: "Point" as const,
						coordinates: [row.lng, row.lat],
					},
					properties: {
						cluster: true,
						count: row.count,
						countLabel: row.countLabel,
						minPrice: row.minPrice,
						maxPrice: row.maxPrice,
					},
				}));

				return Response.json(
					{
						type: "FeatureCollection",
						features,
						meta: {
							mode: "clusters",
							total: totalRow?.total ?? 0,
							returned: features.length,
							limit,
							precision,
							aggregation: "hex",
						},
					},
					{ headers: { "Cache-Control": "public, max-age=30" } },
				);
			},
		},
	},
});

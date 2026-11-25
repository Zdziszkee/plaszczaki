import { createFileRoute } from "@tanstack/react-router";
import { count, max, min, sql } from "drizzle-orm";
import { db } from "#/db";
import { listing } from "#/db/schema";
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

				const latExpr = sql<number>`(floor(${listing.lat} / ${precision}) * ${precision}) + ${precision / 2}`;
				const lngExpr = sql<number>`(floor(${listing.lng} / ${precision}) * ${precision}) + ${precision / 2}`;

				const [totalRow] = await db
					.select({ total: count() })
					.from(listing)
					.where(where);

				const rows = await db
					.select({
						lat: latExpr,
						lng: lngExpr,
						cellCount: count(),
						minPrice: min(listing.pricePln),
						maxPrice: max(listing.pricePln),
					})
					.from(listing)
					.where(where)
					.groupBy(latExpr, lngExpr)
					.limit(limit);

				const features = rows.map((row) => ({
					type: "Feature" as const,
					geometry: {
						type: "Point" as const,
						coordinates: [row.lng, row.lat],
					},
					properties: {
						cluster: true,
						count: row.cellCount,
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
						},
					},
					{ headers: { "Cache-Control": "public, max-age=30" } },
				);
			},
		},
	},
});

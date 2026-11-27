import { createFileRoute } from "@tanstack/react-router";
import { and, between, count, desc, eq, gte, max, min, sql } from "drizzle-orm";
import { db } from "#/db";
import { rcnImport, rcnTransaction, rcnTransactionObject } from "#/db/schema";
import { parseBbox } from "#/lib/listing-filter-types";
import { RCN_ATTRIBUTION, RCN_GRID_DEGREES } from "#/lib/rcn-comparables";
import { monthsCutoff, parseRcnFilters } from "#/lib/rcn-filters";
import type { RcnFeatureCollection } from "#/lib/rcn-types";

function numberParam(value: string | null): number | undefined {
	if (!value) return undefined;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : undefined;
}

export const Route = createFileRoute("/api/rcn")({
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
				const filters = parseRcnFilters(search);
				const requestedPrecision = numberParam(search.get("precision"));
				const precision = Math.min(
					0.5,
					Math.max(0.002, requestedPrecision ?? 0.05),
				);
				const mode = requestedPrecision === undefined ? "points" : "clusters";
				const requestedLimit = numberParam(search.get("limit")) ?? 2000;
				const limit = Math.min(5000, Math.max(1, Math.trunc(requestedLimit)));
				const conditions = [
					between(
						rcnTransactionObject.geoCellLat,
						Math.floor(bbox.minLat / RCN_GRID_DEGREES + 1e-9) *
							RCN_GRID_DEGREES,
						Math.floor(bbox.maxLat / RCN_GRID_DEGREES + 1e-9) *
							RCN_GRID_DEGREES,
					),
					between(
						rcnTransactionObject.geoCellLng,
						Math.floor(bbox.minLng / RCN_GRID_DEGREES + 1e-9) *
							RCN_GRID_DEGREES,
						Math.floor(bbox.maxLng / RCN_GRID_DEGREES + 1e-9) *
							RCN_GRID_DEGREES,
					),
					gte(rcnTransaction.documentDate, monthsCutoff(filters.months)),
				];
				if (filters.kind !== "all") {
					conditions.push(eq(rcnTransactionObject.objectKind, filters.kind));
				}
				if (filters.market !== "all") {
					conditions.push(eq(rcnTransaction.market, filters.market));
				}
				const where = and(...conditions);
				const [freshness] = await db
					.select({ importedAt: max(rcnImport.finishedAt) })
					.from(rcnImport)
					.where(eq(rcnImport.status, "ok"));
				const [totalRow] = await db
					.select({ total: count() })
					.from(rcnTransactionObject)
					.innerJoin(
						rcnTransaction,
						eq(rcnTransaction.id, rcnTransactionObject.transactionId),
					)
					.where(where);

				if (mode === "clusters") {
					const latExpr = sql<number>`(floor(${rcnTransactionObject.lat} / ${precision}) * ${precision}) + ${precision / 2}`;
					const lngExpr = sql<number>`(floor(${rcnTransactionObject.lng} / ${precision}) * ${precision}) + ${precision / 2}`;
					const rows = await db
						.select({
							lat: latExpr,
							lng: lngExpr,
							cellCount: count(),
							minPrice: min(rcnTransactionObject.comparablePricePln),
							maxPrice: max(rcnTransactionObject.comparablePricePln),
						})
						.from(rcnTransactionObject)
						.innerJoin(
							rcnTransaction,
							eq(rcnTransaction.id, rcnTransactionObject.transactionId),
						)
						.where(where)
						.groupBy(latExpr, lngExpr)
						.orderBy(desc(count()))
						.limit(limit);
					const payload: RcnFeatureCollection = {
						type: "FeatureCollection",
						features: rows.map((row) => ({
							type: "Feature",
							geometry: {
								type: "Point",
								coordinates: [row.lng, row.lat],
							},
							properties: {
								cluster: true,
								count: row.cellCount,
								minPrice: row.minPrice,
								maxPrice: row.maxPrice,
							},
						})),
						meta: {
							mode: "clusters",
							total: totalRow?.total ?? 0,
							returned: rows.length,
							limit,
							precision,
							importedAt: freshness?.importedAt?.toISOString() ?? null,
							attribution: RCN_ATTRIBUTION,
						},
					};
					return Response.json(payload, {
						headers: { "Cache-Control": "public, max-age=60" },
					});
				}

				const rows = await db
					.select({
						id: rcnTransactionObject.id,
						transactionId: rcnTransaction.id,
						objectKind: rcnTransactionObject.objectKind,
						lat: rcnTransactionObject.lat,
						lng: rcnTransactionObject.lng,
						pricePln: rcnTransactionObject.comparablePricePln,
						areaM2: rcnTransactionObject.comparableAreaM2,
						pricePerM2Pln: rcnTransactionObject.pricePerM2Pln,
						documentDate: rcnTransaction.documentDate,
						market: rcnTransaction.market,
						transactionType: rcnTransaction.transactionType,
						addressText: rcnTransactionObject.addressText,
						city: rcnTransactionObject.city,
						rooms: rcnTransactionObject.rooms,
						floor: rcnTransactionObject.floor,
					})
					.from(rcnTransactionObject)
					.innerJoin(
						rcnTransaction,
						eq(rcnTransaction.id, rcnTransactionObject.transactionId),
					)
					.where(where)
					.orderBy(
						desc(rcnTransaction.documentDate),
						desc(rcnTransactionObject.id),
					)
					.limit(limit);
				const payload: RcnFeatureCollection = {
					type: "FeatureCollection",
					features: rows.flatMap((row) =>
						row.lat === null || row.lng === null
							? []
							: [
									{
										type: "Feature" as const,
										geometry: {
											type: "Point" as const,
											coordinates: [row.lng, row.lat] as [number, number],
										},
										properties: {
											id: row.id,
											transactionId: row.transactionId,
											objectKind: row.objectKind,
											pricePln: row.pricePln,
											areaM2: row.areaM2,
											pricePerM2Pln: row.pricePerM2Pln,
											documentDate: row.documentDate,
											market: row.market,
											transactionType: row.transactionType,
											addressText: row.addressText,
											city: row.city,
											rooms: row.rooms,
											floor: row.floor,
										},
									},
								],
					),
					meta: {
						mode: "points",
						total: totalRow?.total ?? 0,
						returned: rows.length,
						limit,
						importedAt: freshness?.importedAt?.toISOString() ?? null,
						attribution: RCN_ATTRIBUTION,
					},
				};
				return Response.json(payload, {
					headers: { "Cache-Control": "public, max-age=60" },
				});
			},
		},
	},
});

import { createFileRoute } from "@tanstack/react-router";
import {
	and,
	between,
	desc,
	eq,
	gte,
	isNotNull,
	isNull,
	max,
} from "drizzle-orm";
import { db } from "#/db";
import {
	listing,
	rcnImport,
	rcnTransaction,
	rcnTransactionObject,
} from "#/db/schema";
import {
	comparableMarket,
	gridBounds,
	haversineKm,
	RCN_ATTRIBUTION,
	RCN_COMPARABLE_DEFAULTS,
	summarizeComparables,
} from "#/lib/rcn-comparables";
import { monthsCutoff } from "#/lib/rcn-filters";
import type {
	RcnCandidate,
	RcnComparables,
	RcnObjectKind,
} from "#/lib/rcn-types";

function parseListingId(value: string): number | null {
	const id = Number(value);
	return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function targetKind(propertyType: string): RcnObjectKind | null {
	if (propertyType === "flat") return "flat";
	if (propertyType === "house") return "building";
	if (propertyType === "plot") return "plot";
	return null;
}

export const Route = createFileRoute("/api/listings/$id/rcn")({
	server: {
		handlers: {
			GET: async ({ params }) => {
				const id = parseListingId(params.id);
				if (id === null) {
					return Response.json(
						{ error: "Nieprawidłowy identyfikator oferty." },
						{ status: 400 },
					);
				}
				const [row] = await db
					.select({
						offerType: listing.offerType,
						propertyType: listing.propertyType,
						market: listing.market,
						lat: listing.lat,
						lng: listing.lng,
						areaM2: listing.areaM2,
						landAreaM2: listing.landAreaM2,
						rooms: listing.rooms,
						street: listing.street,
						city: listing.city,
					})
					.from(listing)
					.where(eq(listing.id, id))
					.limit(1);
				if (!row) {
					return Response.json(
						{ error: "Nie znaleziono oferty." },
						{ status: 404 },
					);
				}
				const [freshness] = await db
					.select({ importedAt: max(rcnImport.finishedAt) })
					.from(rcnImport)
					.where(eq(rcnImport.status, "ok"));
				const importedAt = freshness?.importedAt?.toISOString() ?? null;
				const kind = targetKind(row.propertyType);
				const areaM2 =
					row.propertyType === "plot"
						? (row.landAreaM2 ?? row.areaM2)
						: row.areaM2;
				if (
					(row.offerType !== "sale" && row.offerType !== "auction") ||
					!kind ||
					row.lat === null ||
					row.lng === null ||
					areaM2 === null ||
					areaM2 <= 0
				) {
					const unavailable: RcnComparables = {
						status: "unavailable",
						reason:
							row.offerType === "sale" || row.offerType === "auction"
								? "missing_listing_location_or_area"
								: "unsupported_offer_type",
						count: 0,
						medianPricePerM2Pln: null,
						p25PricePerM2Pln: null,
						p75PricePerM2Pln: null,
						weightedPricePerM2Pln: null,
						radiusKm: 0,
						months: RCN_COMPARABLE_DEFAULTS.months,
						confidence: null,
						samples: [],
						sameAddress: [],
						importedAt,
						attribution: RCN_ATTRIBUTION,
					};
					return Response.json(unavailable, {
						headers: { "Cache-Control": "public, max-age=60" },
					});
				}

				const lat = row.lat;
				const lng = row.lng;
				const maxRadius = Math.max(...RCN_COMPARABLE_DEFAULTS.radiiKm[kind]);
				const bounds = gridBounds(lat, lng, maxRadius);
				const conditions = [
					eq(rcnTransactionObject.objectKind, kind),
					isNull(rcnTransactionObject.exclusionReason),
					isNotNull(rcnTransactionObject.pricePerM2Pln),
					gte(
						rcnTransaction.documentDate,
						monthsCutoff(RCN_COMPARABLE_DEFAULTS.months),
					),
					between(
						rcnTransactionObject.geoCellLat,
						bounds.minLat,
						bounds.maxLat,
					),
					between(
						rcnTransactionObject.geoCellLng,
						bounds.minLng,
						bounds.maxLng,
					),
				];
				if (row.market === "primary" || row.market === "secondary") {
					conditions.push(eq(rcnTransaction.market, row.market));
				}
				const candidates = await db
					.select({
						id: rcnTransactionObject.id,
						transactionId: rcnTransaction.id,
						objectKind: rcnTransactionObject.objectKind,
						pricePln: rcnTransactionObject.comparablePricePln,
						areaM2: rcnTransactionObject.comparableAreaM2,
						pricePerM2Pln: rcnTransactionObject.pricePerM2Pln,
						lat: rcnTransactionObject.lat,
						lng: rcnTransactionObject.lng,
						documentDate: rcnTransaction.documentDate,
						market: rcnTransaction.market,
						addressText: rcnTransactionObject.addressText,
						city: rcnTransactionObject.city,
						street: rcnTransactionObject.street,
						rooms: rcnTransactionObject.rooms,
						floor: rcnTransactionObject.floor,
					})
					.from(rcnTransactionObject)
					.innerJoin(
						rcnTransaction,
						eq(rcnTransaction.id, rcnTransactionObject.transactionId),
					)
					.where(and(...conditions))
					.orderBy(
						desc(rcnTransaction.documentDate),
						desc(rcnTransactionObject.id),
					)
					.limit(5000);
				const normalized: RcnCandidate[] = candidates.flatMap((candidate) => {
					if (
						candidate.pricePln === null ||
						candidate.areaM2 === null ||
						candidate.pricePerM2Pln === null ||
						candidate.lat === null ||
						candidate.lng === null ||
						candidate.documentDate === null
					) {
						return [];
					}
					return [
						{
							id: candidate.id,
							transactionId: candidate.transactionId,
							objectKind: candidate.objectKind as RcnObjectKind,
							pricePln: candidate.pricePln,
							areaM2: candidate.areaM2,
							pricePerM2Pln: candidate.pricePerM2Pln,
							lat: candidate.lat,
							lng: candidate.lng,
							distanceKm: haversineKm(lat, lng, candidate.lat, candidate.lng),
							documentDate: candidate.documentDate,
							market: comparableMarket(candidate.market),
							addressText: candidate.addressText,
							city: candidate.city,
							street: candidate.street,
							rooms: candidate.rooms,
							floor: candidate.floor,
						},
					];
				});
				const summary = summarizeComparables(
					{
						propertyType: row.propertyType,
						market: row.market,
						lat,
						lng,
						areaM2,
						rooms: row.rooms,
						street: row.street,
						city: row.city,
					},
					normalized,
					{ importedAt },
				);
				return Response.json(summary, {
					headers: { "Cache-Control": "public, max-age=60" },
				});
			},
		},
	},
});

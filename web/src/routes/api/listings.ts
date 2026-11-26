import { createFileRoute } from "@tanstack/react-router";
import { count, desc, eq } from "drizzle-orm";
import { db } from "#/db";
import { listing, source } from "#/db/schema";
import { parseBbox, parseListingFilters } from "#/lib/listing-filter-types";
import {
	buildListingConditions,
	combineConditions,
	listingScoreExpression,
} from "#/lib/listing-filters";
import { sourceColor } from "#/lib/source-colors";

function numberParam(value: string | null): number | undefined {
	if (!value) return undefined;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : undefined;
}

export const Route = createFileRoute("/api/listings")({
	server: {
		handlers: {
			GET: async ({ request }) => {
				const search = new URL(request.url).searchParams;
				const filters = parseListingFilters(search);
				const bbox = parseBbox(search);
				const conditions = buildListingConditions(filters, bbox);
				const where = combineConditions(conditions);

				const requestedLimit = numberParam(search.get("limit")) ?? 2000;
				const limit = Math.min(Math.max(Math.trunc(requestedLimit), 1), 5000);

				const [totalRow] = await db
					.select({ total: count() })
					.from(listing)
					.where(where);

				const rows = await db
					.select({
						id: listing.id,
						sourceId: listing.sourceId,
						sourceLabel: source.label,
						externalId: listing.externalId,
						url: listing.url,
						title: listing.title,
						offerType: listing.offerType,
						propertyType: listing.propertyType,
						pricePln: listing.pricePln,
						priceKind: listing.priceKind,
						pricePerM2Pln: listing.pricePerM2Pln,
						areaM2: listing.areaM2,
						rooms: listing.rooms,
						city: listing.city,
						voivodeship: listing.voivodeship,
						market: listing.market,
						sellerType: listing.sellerType,
						lat: listing.lat,
						lng: listing.lng,
						geoSource: listing.geoSource,
						images: listing.images,
						publishedAt: listing.publishedAt,
						firstSeenAt: listing.firstSeenAt,
						score: listing.score,
						scoreProfile: listing.scoreProfile,
						scores: listing.scores,
						scoreReasons: listing.scoreReasons,
						scoreBreakdown: listing.scoreBreakdown,
						scoreVersion: listing.scoreVersion,
						buildingType: listing.buildingType,
						buildYear: listing.buildYear,
						heating: listing.heating,
						windowsType: listing.windowsType,
					})
					.from(listing)
					.innerJoin(source, eq(source.id, listing.sourceId))
					.where(where)
					.orderBy(
						filters.sort === "score"
							? desc(listingScoreExpression(filters.scoreProfile))
							: desc(listing.lastSeenAt),
					)
					.limit(limit);

				const features = rows.flatMap((row) => {
					if (row.lat === null || row.lng === null) return [];
					const profile = filters.scoreProfile ?? row.scoreProfile;
					const score = filters.scoreProfile
						? (row.scores?.[filters.scoreProfile] ?? row.score)
						: row.score;
					const breakdown = profile
						? row.scoreBreakdown?.profiles[
								profile as keyof typeof row.scoreBreakdown.profiles
							]
						: undefined;
					const reasons = breakdown
						? [
								...breakdown.contributions
									.filter((reason) => reason.points > 0)
									.sort((a, b) => b.points - a.points)
									.slice(0, 6),
								...breakdown.penalties.slice(0, 2),
							].map(({ code, label, points }) => ({ code, label, points }))
						: (row.scoreReasons ?? []);
					return [
						{
							type: "Feature" as const,
							geometry: {
								type: "Point" as const,
								coordinates: [row.lng, row.lat],
							},
							properties: {
								id: row.id,
								source: row.sourceId,
								sourceLabel: row.sourceLabel,
								color: sourceColor(row.sourceId),
								externalId: row.externalId,
								url: row.url,
								title: row.title,
								offerType: row.offerType,
								propertyType: row.propertyType,
								pricePln: row.pricePln,
								priceKind: row.priceKind,
								pricePerM2Pln: row.pricePerM2Pln,
								areaM2: row.areaM2,
								rooms: row.rooms,
								city: row.city,
								voivodeship: row.voivodeship,
								market: row.market,
								sellerType: row.sellerType,
								geoSource: row.geoSource,
								image: row.images?.[0] ?? null,
								publishedAt:
									row.publishedAt?.getTime() ?? row.firstSeenAt.getTime(),
								score,
								scoreProfile: profile,
								scores: row.scores,
								scoreReasons: reasons,
								scoreBreakdown: row.scoreBreakdown,
								scoreVersion: row.scoreVersion,
								buildingType: row.buildingType,
								buildYear: row.buildYear,
								heating: row.heating,
								windowsType: row.windowsType,
							},
						},
					];
				});

				return Response.json(
					{
						type: "FeatureCollection",
						features,
						meta: {
							mode: "points",
							total: totalRow?.total ?? 0,
							returned: features.length,
							limit,
						},
					},
					{ headers: { "Cache-Control": "public, max-age=60" } },
				);
			},
		},
	},
});

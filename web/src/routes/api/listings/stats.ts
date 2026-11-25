import { createFileRoute } from "@tanstack/react-router";
import { and, count, desc, eq, isNotNull, sql } from "drizzle-orm";
import { db } from "#/db";
import { listing, source } from "#/db/schema";
import {
	OFFER_TYPES,
	type PostedWithin,
	parseListingFilters,
} from "#/lib/listing-filter-types";
import {
	buildListingConditions,
	combineConditions,
	postedAtExpression,
	postedWithinCutoff,
} from "#/lib/listing-filters";
import { sourceColor } from "#/lib/source-colors";

type PeriodCounts = Record<PostedWithin | "all", number>;

function toNumber(value: unknown): number {
	const parsed = typeof value === "number" ? value : Number(value);
	return Number.isFinite(parsed) ? parsed : 0;
}

export const Route = createFileRoute("/api/listings/stats")({
	server: {
		handlers: {
			GET: ({ request }) => {
				const search = new URL(request.url).searchParams;
				const filters = parseListingFilters(search);
				const now = new Date();

				return (async () => {
					const [unfiltered] = await db
						.select({ total: count() })
						.from(listing)
						.where(and(isNotNull(listing.lat), isNotNull(listing.lng)));

					const filteredWhere = combineConditions(
						buildListingConditions(filters, null),
					);
					const [filtered] = await db
						.select({ total: count() })
						.from(listing)
						.where(filteredWhere);

					// Counts per dimension ignore that dimension's own filter, so each
					// button/row shows the number of offers selecting it would reveal.
					const typeWhere = combineConditions(
						buildListingConditions(filters, null, ["offerType"]),
					);
					const typeRows = await db
						.select({ offerType: listing.offerType, total: count() })
						.from(listing)
						.where(typeWhere)
						.groupBy(listing.offerType);
					const byOfferType = Object.fromEntries(
						OFFER_TYPES.map((type) => [type, 0]),
					) as Record<string, number>;
					for (const row of typeRows) {
						byOfferType[row.offerType] = toNumber(row.total);
					}

					const periodWhere = combineConditions(
						buildListingConditions(filters, null, ["postedWithin"]),
					);
					const [periods] = await db
						.select({
							all: count(),
							today: sql<number>`sum(case when ${postedAtExpression()} >= ${postedWithinCutoff("today", now)} then 1 else 0 end)`,
							week: sql<number>`sum(case when ${postedAtExpression()} >= ${postedWithinCutoff("week", now)} then 1 else 0 end)`,
							month: sql<number>`sum(case when ${postedAtExpression()} >= ${postedWithinCutoff("month", now)} then 1 else 0 end)`,
						})
						.from(listing)
						.where(periodWhere);
					const byPeriod: PeriodCounts = {
						all: toNumber(periods?.all),
						today: toNumber(periods?.today),
						week: toNumber(periods?.week),
						month: toNumber(periods?.month),
					};

					const sourceWhere = combineConditions(
						buildListingConditions(filters, null, ["sourceId"]),
					);
					const sourceRows = await db
						.select({
							id: source.id,
							label: source.label,
							enabled: source.enabled,
							total: count(listing.id),
						})
						.from(source)
						.leftJoin(
							listing,
							and(eq(listing.sourceId, source.id), sourceWhere),
						)
						.groupBy(source.id)
						.orderBy(desc(count(listing.id)), source.label);

					return Response.json(
						{
							total: toNumber(unfiltered?.total),
							filteredTotal: toNumber(filtered?.total),
							byOfferType,
							byPeriod,
							bySource: sourceRows.map((row) => ({
								id: row.id,
								label: row.label,
								enabled: row.enabled,
								color: sourceColor(row.id),
								count: toNumber(row.total),
							})),
						},
						{ headers: { "Cache-Control": "public, max-age=30" } },
					);
				})();
			},
		},
	},
});

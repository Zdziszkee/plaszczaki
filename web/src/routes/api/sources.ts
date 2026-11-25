import { createFileRoute } from "@tanstack/react-router";
import { count, eq, max } from "drizzle-orm";
import { db } from "#/db";
import { listing, source } from "#/db/schema";
import { sourceColor } from "#/lib/source-colors";

export const Route = createFileRoute("/api/sources")({
	server: {
		handlers: {
			GET: async () => {
				const rows = await db
					.select({
						id: source.id,
						label: source.label,
						homepageUrl: source.homepageUrl,
						adapter: source.adapter,
						enabled: source.enabled,
						listings: count(listing.id),
						lastSeenAt: max(listing.lastSeenAt),
					})
					.from(source)
					.leftJoin(listing, eq(listing.sourceId, source.id))
					.groupBy(source.id)
					.orderBy(source.label);

				return Response.json(
					{
						sources: rows.map((row) => ({
							...row,
							color: sourceColor(row.id),
						})),
					},
					{ headers: { "Cache-Control": "public, max-age=60" } },
				);
			},
		},
	},
});

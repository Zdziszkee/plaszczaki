import { createFileRoute } from "@tanstack/react-router";
import { sql } from "drizzle-orm";
import { db } from "#/db";
import { RCN_ATTRIBUTION } from "#/lib/rcn-comparables";

export const Route = createFileRoute("/api/rcn/status")({
	server: {
		handlers: {
			GET: async () => {
				const imports = await db.all(
					sql`
						SELECT i.* FROM rcn_import i
						JOIN (
							SELECT region_id, object_kind, max(id) AS id
							FROM rcn_import
							GROUP BY region_id, object_kind
						) latest ON latest.id = i.id
						ORDER BY i.region_id, i.object_kind
					`,
				);
				const totalRows = (await db.all(
					sql`
						SELECT
							(SELECT count(*) FROM rcn_transaction) AS transactions,
							(SELECT count(*) FROM rcn_transaction_object) AS objects,
							(SELECT count(*) FROM rcn_transaction_object
							 WHERE exclusion_reason IS NULL AND price_per_m2_pln IS NOT NULL)
								AS comparable_objects
					`,
				)) as unknown as Array<{
					transactions: number;
					objects: number;
					comparable_objects: number;
				}>;
				const totals = totalRows[0];
				return Response.json(
					{
						imports,
						transactions: Number(totals?.transactions ?? 0),
						objects: Number(totals?.objects ?? 0),
						comparableObjects: Number(totals?.comparable_objects ?? 0),
						attribution: RCN_ATTRIBUTION,
					},
					{ headers: { "Cache-Control": "public, max-age=30" } },
				);
			},
		},
	},
});

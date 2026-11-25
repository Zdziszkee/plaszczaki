import { relations, sql } from "drizzle-orm";
import {
	index,
	integer,
	primaryKey,
	real,
	sqliteTable,
	text,
	uniqueIndex,
} from "drizzle-orm/sqlite-core";

export const todos = sqliteTable("todos", {
	id: integer({ mode: "number" }).primaryKey({
		autoIncrement: true,
	}),
	title: text().notNull(),
	createdAt: integer("created_at", { mode: "timestamp" }).default(
		sql`(unixepoch())`,
	),
});

export const user = sqliteTable("user", {
	id: text("id").primaryKey(),
	name: text("name").notNull(),
	email: text("email").notNull().unique(),
	emailVerified: integer("email_verified", { mode: "boolean" })
		.default(false)
		.notNull(),
	image: text("image"),
	createdAt: integer("created_at", { mode: "timestamp_ms" })
		.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
		.notNull(),
	updatedAt: integer("updated_at", { mode: "timestamp_ms" })
		.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
		.$onUpdate(() => new Date())
		.notNull(),
});

export const session = sqliteTable(
	"session",
	{
		id: text("id").primaryKey(),
		expiresAt: integer("expires_at", { mode: "timestamp_ms" }).notNull(),
		token: text("token").notNull().unique(),
		createdAt: integer("created_at", { mode: "timestamp_ms" })
			.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
			.notNull(),
		updatedAt: integer("updated_at", { mode: "timestamp_ms" })
			.$onUpdate(() => new Date())
			.notNull(),
		ipAddress: text("ip_address"),
		userAgent: text("user_agent"),
		userId: text("user_id")
			.notNull()
			.references(() => user.id, { onDelete: "cascade" }),
	},
	(table) => [index("session_userId_idx").on(table.userId)],
);

export const account = sqliteTable(
	"account",
	{
		id: text("id").primaryKey(),
		accountId: text("account_id").notNull(),
		providerId: text("provider_id").notNull(),
		userId: text("user_id")
			.notNull()
			.references(() => user.id, { onDelete: "cascade" }),
		accessToken: text("access_token"),
		refreshToken: text("refresh_token"),
		idToken: text("id_token"),
		accessTokenExpiresAt: integer("access_token_expires_at", {
			mode: "timestamp_ms",
		}),
		refreshTokenExpiresAt: integer("refresh_token_expires_at", {
			mode: "timestamp_ms",
		}),
		scope: text("scope"),
		password: text("password"),
		createdAt: integer("created_at", { mode: "timestamp_ms" })
			.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
			.notNull(),
		updatedAt: integer("updated_at", { mode: "timestamp_ms" })
			.$onUpdate(() => new Date())
			.notNull(),
	},
	(table) => [index("account_userId_idx").on(table.userId)],
);

export const verification = sqliteTable(
	"verification",
	{
		id: text("id").primaryKey(),
		identifier: text("identifier").notNull(),
		value: text("value").notNull(),
		expiresAt: integer("expires_at", { mode: "timestamp_ms" }).notNull(),
		createdAt: integer("created_at", { mode: "timestamp_ms" })
			.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
			.notNull(),
		updatedAt: integer("updated_at", { mode: "timestamp_ms" })
			.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
			.$onUpdate(() => new Date())
			.notNull(),
	},
	(table) => [index("verification_identifier_idx").on(table.identifier)],
);

export const source = sqliteTable("source", {
	id: text("id").primaryKey(),
	label: text("label").notNull(),
	homepageUrl: text("homepage_url").notNull(),
	adapter: text("adapter").notNull(),
	enabled: integer("enabled", { mode: "boolean" }).default(true).notNull(),
	createdAt: integer("created_at", { mode: "timestamp_ms" }).notNull(),
});

export const listing = sqliteTable(
	"listing",
	{
		id: integer("id").primaryKey({ autoIncrement: true }),
		sourceId: text("source_id")
			.notNull()
			.references(() => source.id, { onDelete: "cascade" }),
		externalId: text("external_id").notNull(),
		url: text("url").notNull(),
		title: text("title"),
		description: text("description"),

		offerType: text("offer_type").notNull(),
		propertyType: text("property_type").notNull(),
		market: text("market"),
		priceKind: text("price_kind").default("asking").notNull(),
		priceUnit: text("price_unit").default("total").notNull(),

		pricePln: integer("price_pln"),
		rentPln: integer("rent_pln"),
		depositPln: integer("deposit_pln"),
		pricePerM2Pln: integer("price_per_m2_pln"),

		areaM2: real("area_m2"),
		landAreaM2: real("land_area_m2"),
		rooms: integer("rooms"),
		floor: integer("floor"),
		floorLabel: text("floor_label"),
		buildYear: integer("build_year"),

		voivodeship: text("voivodeship"),
		county: text("county"),
		commune: text("commune"),
		city: text("city"),
		district: text("district"),
		street: text("street"),
		addressText: text("address_text"),
		lat: real("lat"),
		lng: real("lng"),
		geoSource: text("geo_source"),

		images: text("images", { mode: "json" }).$type<string[]>().notNull(),
		sellerType: text("seller_type"),
		sellerName: text("seller_name"),

		publishedAt: integer("published_at", { mode: "timestamp_ms" }),
		modifiedAt: integer("modified_at", { mode: "timestamp_ms" }),

		firstSeenAt: integer("first_seen_at", { mode: "timestamp_ms" }).notNull(),
		lastSeenAt: integer("last_seen_at", { mode: "timestamp_ms" }).notNull(),

		extractionMethod: text("extraction_method").default("adapter").notNull(),
		extractionModel: text("extraction_model"),

		auctionDate: integer("auction_date", { mode: "timestamp_ms" }),
		auctionRound: integer("auction_round"),
		caseNumber: text("case_number"),

		extra: text("extra", { mode: "json" })
			.$type<Record<string, unknown>>()
			.notNull(),

		canonicalKey: text("canonical_key"),
	},
	(table) => [
		uniqueIndex("listing_source_external_idx").on(
			table.sourceId,
			table.externalId,
		),
		index("listing_canonical_idx").on(table.canonicalKey),
		index("listing_region_idx").on(
			table.voivodeship,
			table.propertyType,
			table.offerType,
		),
		index("listing_geo_idx").on(table.lat, table.lng),
	],
);

export const listingSnapshot = sqliteTable("listing_snapshot", {
	id: integer("id").primaryKey({ autoIncrement: true }),
	listingId: integer("listing_id")
		.notNull()
		.references(() => listing.id, { onDelete: "cascade" }),
	observedAt: integer("observed_at", { mode: "timestamp_ms" }).notNull(),
	pricePln: integer("price_pln"),
	changed: text("changed", { mode: "json" }).$type<string[]>().notNull(),
});

export const scraperRun = sqliteTable("scraper_run", {
	id: integer("id").primaryKey({ autoIncrement: true }),
	sourceId: text("source_id").notNull(),
	mode: text("mode").notNull(),
	startedAt: integer("started_at", { mode: "timestamp_ms" }).notNull(),
	finishedAt: integer("finished_at", { mode: "timestamp_ms" }),
	status: text("status").default("running").notNull(),
	stats: text("stats", { mode: "json" })
		.$type<Record<string, unknown>>()
		.notNull(),
	error: text("error"),
});

export const scraperCheckpoint = sqliteTable(
	"scraper_checkpoint",
	{
		sourceId: text("source_id").notNull(),
		queryKey: text("query_key").notNull(),
		cursor: text("cursor"),
		etag: text("etag"),
		updatedAt: integer("updated_at", { mode: "timestamp_ms" }).notNull(),
	},
	(table) => [primaryKey({ columns: [table.sourceId, table.queryKey] })],
);

export const llmJob = sqliteTable(
	"llm_job",
	{
		id: integer("id").primaryKey({ autoIncrement: true }),
		sourceId: text("source_id").notNull(),
		url: text("url").notNull(),
		kind: text("kind").notNull(),
		inputHash: text("input_hash").notNull(),
		model: text("model"),
		promptVersion: text("prompt_version").notNull(),
		status: text("status").default("pending").notNull(),
		attempts: integer("attempts").default(0).notNull(),
		costUsd: real("cost_usd"),
		inputTokens: integer("input_tokens"),
		outputTokens: integer("output_tokens"),
		responseJson: text("response_json", { mode: "json" }).$type<
			Record<string, unknown>
		>(),
		error: text("error"),
		createdAt: integer("created_at", { mode: "timestamp_ms" }).notNull(),
		updatedAt: integer("updated_at", { mode: "timestamp_ms" }).notNull(),
	},
	(table) => [
		uniqueIndex("llm_job_input_idx").on(
			table.inputHash,
			table.promptVersion,
			table.model,
		),
		index("llm_job_status_idx").on(table.status, table.createdAt),
	],
);

export const userRelations = relations(user, ({ many }) => ({
	sessions: many(session),
	accounts: many(account),
}));

export const sessionRelations = relations(session, ({ one }) => ({
	user: one(user, {
		fields: [session.userId],
		references: [user.id],
	}),
}));

export const accountRelations = relations(account, ({ one }) => ({
	user: one(user, {
		fields: [account.userId],
		references: [user.id],
	}),
}));

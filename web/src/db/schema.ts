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
import type { RankingConfig, RankingSignals } from "#/lib/ranking-types";

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

		condition: text("condition"),
		buildingType: text("building_type"),
		buildingMaterial: text("building_material"),
		heating: text("heating"),
		windowsType: text("windows_type"),
		ownership: text("ownership"),
		energyCertificate: text("energy_certificate"),
		ceilingHeightM: real("ceiling_height_m"),
		buildingFloors: integer("building_floors"),
		elevator: integer("elevator", { mode: "boolean" }),
		parking: text("parking"),
		parkingSpaces: integer("parking_spaces"),
		balcony: integer("balcony", { mode: "boolean" }),
		terrace: integer("terrace", { mode: "boolean" }),
		garden: integer("garden", { mode: "boolean" }),
		furnished: integer("furnished", { mode: "boolean" }),
		petsAllowed: integer("pets_allowed", { mode: "boolean" }),
		airConditioning: integer("air_conditioning", { mode: "boolean" }),
		security: text("security"),
		internet: integer("internet", { mode: "boolean" }),
		fenced: integer("fenced", { mode: "boolean" }),
		administrativeRentPln: integer("administrative_rent_pln"),
		estimatePln: integer("estimate_pln"),
		availableFrom: integer("available_from", { mode: "timestamp_ms" }),

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

		// Signal extraction is owned by the scraper; ranking is computed by the web app.
		scoreSignals: text("score_signals", {
			mode: "json",
		}).$type<RankingSignals>(),
		scoreSignalsVersion: text("score_signals_version"),
		scoreSignalsAt: integer("score_signals_at", { mode: "timestamp_ms" }),

		// Deprecated legacy columns retained only for a zero-downtime migration.
		score: integer("score"),
		scoreProfile: text("score_profile"),
		scores: text("scores", { mode: "json" }).$type<Record<string, number>>(),
		scoreReasons: text("score_reasons", { mode: "json" }).$type<
			Array<{ code: string; label: string; points: number }>
		>(),
		scoreBreakdown: text("score_breakdown", {
			mode: "json",
		}).$type<unknown>(),
		scoredAt: integer("scored_at", { mode: "timestamp_ms" }),
		scoreVersion: text("score_version"),
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
		index("listing_signals_idx").on(table.propertyType, table.scoreSignalsAt),
		index("listing_first_seen_idx").on(table.firstSeenAt),
	],
);

export const rankingPreset = sqliteTable(
	"ranking_preset",
	{
		id: integer("id").primaryKey({ autoIncrement: true }),
		name: text("name").notNull(),
		profile: text("profile").notNull(),
		config: text("config", { mode: "json" }).$type<RankingConfig>().notNull(),
		isActive: integer("is_active", { mode: "boolean" })
			.default(false)
			.notNull(),
		createdAt: integer("created_at", { mode: "timestamp_ms" })
			.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
			.notNull(),
		updatedAt: integer("updated_at", { mode: "timestamp_ms" })
			.default(sql`(cast(unixepoch('subsecond') * 1000 as integer))`)
			.$onUpdate(() => new Date())
			.notNull(),
	},
	(table) => [index("ranking_preset_profile_idx").on(table.profile)],
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

export const rcnImport = sqliteTable(
	"rcn_import",
	{
		id: integer("id").primaryKey({ autoIncrement: true }),
		regionId: text("region_id").notNull(),
		objectKind: text("object_kind").notNull(),
		sourceUrl: text("source_url").notNull(),
		etag: text("etag"),
		lastModified: text("last_modified"),
		fileSha256: text("file_sha256"),
		status: text("status").default("running").notNull(),
		startedAt: integer("started_at", { mode: "timestamp_ms" }).notNull(),
		finishedAt: integer("finished_at", { mode: "timestamp_ms" }),
		sourceRows: integer("source_rows"),
		importedRows: integer("imported_rows"),
		skippedRows: integer("skipped_rows"),
		error: text("error"),
	},
	(table) => [
		index("rcn_import_region_kind_idx").on(
			table.regionId,
			table.objectKind,
			table.startedAt,
		),
	],
);

export const rcnTransaction = sqliteTable(
	"rcn_transaction",
	{
		id: integer("id").primaryKey({ autoIncrement: true }),
		transactionKey: text("transaction_key").notNull(),
		countyTeryt: text("county_teryt").notNull(),
		regionId: text("region_id").notNull(),
		iipNamespace: text("iip_namespace").notNull(),
		localId: text("local_id").notNull(),
		versionAt: integer("version_at", { mode: "timestamp_ms" }),
		designation: text("designation"),
		transactionType: text("transaction_type"),
		market: text("market"),
		sellerType: text("seller_type"),
		buyerType: text("buyer_type"),
		grossPricePln: integer("gross_price_pln"),
		vatPln: integer("vat_pln"),
		documentRef: text("document_ref"),
		documentDate: text("document_date"),
		documentCreator: text("document_creator"),
		propertyKind: text("property_kind"),
		rightKind: text("right_kind"),
		shareRaw: text("share_raw"),
		shareFraction: real("share_fraction"),
		landAreaM2: real("land_area_m2"),
		propertyPricePln: integer("property_price_pln"),
		propertyVatPln: integer("property_vat_pln"),
		description: text("description"),
		sourceUrl: text("source_url"),
		recordHash: text("record_hash"),
		firstSeenAt: integer("first_seen_at", { mode: "timestamp_ms" }).notNull(),
		lastSeenAt: integer("last_seen_at", { mode: "timestamp_ms" }).notNull(),
	},
	(table) => [
		uniqueIndex("rcn_transaction_key_idx").on(table.transactionKey),
		index("rcn_transaction_date_market_idx").on(
			table.documentDate,
			table.market,
		),
		index("rcn_transaction_region_idx").on(table.regionId),
	],
);

export const rcnTransactionObject = sqliteTable(
	"rcn_transaction_object",
	{
		id: integer("id").primaryKey({ autoIncrement: true }),
		transactionId: integer("transaction_id")
			.notNull()
			.references(() => rcnTransaction.id, { onDelete: "cascade" }),
		objectKey: text("object_key").notNull(),
		regionId: text("region_id").notNull(),
		objectKind: text("object_kind").notNull(),
		objectId: text("object_id"),
		objectNumber: text("object_number"),
		addressText: text("address_text"),
		city: text("city"),
		street: text("street"),
		functionRaw: text("function_raw"),
		rooms: integer("rooms"),
		floor: integer("floor"),
		usableAreaM2: real("usable_area_m2"),
		ancillaryAreaM2: real("ancillary_area_m2"),
		plotAreaM2: real("plot_area_m2"),
		mpzpPurpose: text("mpzp_purpose"),
		landUse: text("land_use"),
		buildingTypeRaw: text("building_type_raw"),
		objectPricePln: integer("object_price_pln"),
		objectVatPln: integer("object_vat_pln"),
		info: text("info"),
		lat: real("lat"),
		lng: real("lng"),
		geoCellLat: real("geo_cell_lat"),
		geoCellLng: real("geo_cell_lng"),
		sourceLayer: text("source_layer").notNull(),
		sourceGid: integer("source_gid"),
		comparablePricePln: integer("comparable_price_pln"),
		comparableAreaM2: real("comparable_area_m2"),
		pricePerM2Pln: real("price_per_m2_pln"),
		exclusionReason: text("exclusion_reason"),
		recordHash: text("record_hash"),
		lastSeenImportId: integer("last_seen_import_id"),
		firstSeenAt: integer("first_seen_at", { mode: "timestamp_ms" }).notNull(),
		lastSeenAt: integer("last_seen_at", { mode: "timestamp_ms" }).notNull(),
	},
	(table) => [
		uniqueIndex("rcn_transaction_object_key_idx").on(table.objectKey),
		index("rcn_object_transaction_idx").on(table.transactionId),
		index("rcn_object_region_layer_idx").on(
			table.regionId,
			table.sourceLayer,
			table.lastSeenImportId,
		),
		index("rcn_object_geo_idx").on(
			table.geoCellLat,
			table.geoCellLng,
			table.objectKind,
		),
		index("rcn_object_comparable_geo_idx").on(
			table.objectKind,
			table.geoCellLat,
			table.geoCellLng,
			table.exclusionReason,
		),
		index("rcn_object_comparable_idx").on(
			table.objectKind,
			table.exclusionReason,
			table.pricePerM2Pln,
		),
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

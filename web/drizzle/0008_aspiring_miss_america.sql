CREATE TABLE `rcn_import` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`region_id` text NOT NULL,
	`object_kind` text NOT NULL,
	`source_url` text NOT NULL,
	`etag` text,
	`last_modified` text,
	`file_sha256` text,
	`status` text DEFAULT 'running' NOT NULL,
	`started_at` integer NOT NULL,
	`finished_at` integer,
	`source_rows` integer,
	`imported_rows` integer,
	`skipped_rows` integer,
	`error` text
);
--> statement-breakpoint
CREATE INDEX `rcn_import_region_kind_idx` ON `rcn_import` (`region_id`,`object_kind`,`started_at`);--> statement-breakpoint
CREATE TABLE `rcn_transaction` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`transaction_key` text NOT NULL,
	`county_teryt` text NOT NULL,
	`region_id` text NOT NULL,
	`iip_namespace` text NOT NULL,
	`local_id` text NOT NULL,
	`version_at` integer,
	`designation` text,
	`transaction_type` text,
	`market` text,
	`seller_type` text,
	`buyer_type` text,
	`gross_price_pln` integer,
	`vat_pln` integer,
	`document_ref` text,
	`document_date` text,
	`document_creator` text,
	`property_kind` text,
	`right_kind` text,
	`share_raw` text,
	`share_fraction` real,
	`land_area_m2` real,
	`property_price_pln` integer,
	`property_vat_pln` integer,
	`description` text,
	`source_url` text,
	`record_hash` text,
	`first_seen_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `rcn_transaction_key_idx` ON `rcn_transaction` (`transaction_key`);--> statement-breakpoint
CREATE INDEX `rcn_transaction_date_market_idx` ON `rcn_transaction` (`document_date`,`market`);--> statement-breakpoint
CREATE INDEX `rcn_transaction_region_idx` ON `rcn_transaction` (`region_id`);--> statement-breakpoint
CREATE TABLE `rcn_transaction_object` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`transaction_id` integer NOT NULL,
	`object_key` text NOT NULL,
	`region_id` text NOT NULL,
	`object_kind` text NOT NULL,
	`object_id` text,
	`object_number` text,
	`address_text` text,
	`city` text,
	`street` text,
	`function_raw` text,
	`rooms` integer,
	`floor` integer,
	`usable_area_m2` real,
	`ancillary_area_m2` real,
	`plot_area_m2` real,
	`mpzp_purpose` text,
	`land_use` text,
	`building_type_raw` text,
	`object_price_pln` integer,
	`object_vat_pln` integer,
	`info` text,
	`lat` real,
	`lng` real,
	`geo_cell_lat` real,
	`geo_cell_lng` real,
	`source_layer` text NOT NULL,
	`source_gid` integer,
	`comparable_price_pln` integer,
	`comparable_area_m2` real,
	`price_per_m2_pln` real,
	`exclusion_reason` text,
	`record_hash` text,
	`last_seen_import_id` integer,
	`first_seen_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL,
	FOREIGN KEY (`transaction_id`) REFERENCES `rcn_transaction`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `rcn_transaction_object_key_idx` ON `rcn_transaction_object` (`object_key`);--> statement-breakpoint
CREATE INDEX `rcn_object_transaction_idx` ON `rcn_transaction_object` (`transaction_id`);--> statement-breakpoint
CREATE INDEX `rcn_object_region_kind_idx` ON `rcn_transaction_object` (`region_id`,`object_kind`,`last_seen_import_id`);--> statement-breakpoint
CREATE INDEX `rcn_object_geo_idx` ON `rcn_transaction_object` (`geo_cell_lat`,`geo_cell_lng`,`object_kind`);--> statement-breakpoint
CREATE INDEX `rcn_object_comparable_idx` ON `rcn_transaction_object` (`object_kind`,`exclusion_reason`,`price_per_m2_pln`);
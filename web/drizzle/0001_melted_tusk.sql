CREATE TABLE `listing` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`source_id` text NOT NULL,
	`external_id` text NOT NULL,
	`url` text NOT NULL,
	`title` text,
	`description` text,
	`offer_type` text NOT NULL,
	`property_type` text NOT NULL,
	`market` text,
	`price_kind` text DEFAULT 'asking' NOT NULL,
	`price_unit` text DEFAULT 'total' NOT NULL,
	`price_pln` integer,
	`rent_pln` integer,
	`deposit_pln` integer,
	`price_per_m2_pln` integer,
	`area_m2` real,
	`land_area_m2` real,
	`rooms` integer,
	`floor` integer,
	`floor_label` text,
	`build_year` integer,
	`voivodeship` text,
	`county` text,
	`commune` text,
	`city` text,
	`district` text,
	`street` text,
	`address_text` text,
	`lat` real,
	`lng` real,
	`geo_source` text,
	`images` text NOT NULL,
	`seller_type` text,
	`seller_name` text,
	`published_at` integer,
	`modified_at` integer,
	`first_seen_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL,
	`detail_fetched_at` integer,
	`detail_attempts` integer DEFAULT 0 NOT NULL,
	`detail_error` text,
	`status` text DEFAULT 'discovered' NOT NULL,
	`extraction_method` text DEFAULT 'adapter' NOT NULL,
	`extraction_model` text,
	`auction_date` integer,
	`auction_round` integer,
	`case_number` text,
	`extra` text NOT NULL,
	FOREIGN KEY (`source_id`) REFERENCES `source`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `listing_source_external_idx` ON `listing` (`source_id`,`external_id`);--> statement-breakpoint
CREATE INDEX `listing_region_idx` ON `listing` (`voivodeship`,`property_type`,`offer_type`);--> statement-breakpoint
CREATE INDEX `listing_geo_idx` ON `listing` (`lat`,`lng`);--> statement-breakpoint
CREATE INDEX `listing_status_idx` ON `listing` (`status`,`last_seen_at`);--> statement-breakpoint
CREATE TABLE `listing_snapshot` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`listing_id` integer NOT NULL,
	`observed_at` integer NOT NULL,
	`price_pln` integer,
	`status` text NOT NULL,
	`changed` text NOT NULL,
	FOREIGN KEY (`listing_id`) REFERENCES `listing`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `llm_job` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`source_id` text NOT NULL,
	`url` text NOT NULL,
	`kind` text NOT NULL,
	`input_hash` text NOT NULL,
	`model` text,
	`prompt_version` text NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`attempts` integer DEFAULT 0 NOT NULL,
	`cost_usd` real,
	`input_tokens` integer,
	`output_tokens` integer,
	`response_json` text,
	`error` text,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `llm_job_input_idx` ON `llm_job` (`input_hash`,`prompt_version`,`model`);--> statement-breakpoint
CREATE INDEX `llm_job_status_idx` ON `llm_job` (`status`,`created_at`);--> statement-breakpoint
CREATE TABLE `scraper_checkpoint` (
	`source_id` text NOT NULL,
	`query_key` text NOT NULL,
	`cursor` text,
	`etag` text,
	`updated_at` integer NOT NULL,
	PRIMARY KEY(`source_id`, `query_key`)
);
--> statement-breakpoint
CREATE TABLE `scraper_run` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`source_id` text NOT NULL,
	`mode` text NOT NULL,
	`started_at` integer NOT NULL,
	`finished_at` integer,
	`status` text DEFAULT 'running' NOT NULL,
	`stats` text NOT NULL,
	`error` text
);
--> statement-breakpoint
CREATE TABLE `source` (
	`id` text PRIMARY KEY NOT NULL,
	`label` text NOT NULL,
	`homepage_url` text NOT NULL,
	`adapter` text NOT NULL,
	`enabled` integer DEFAULT true NOT NULL,
	`created_at` integer NOT NULL
);

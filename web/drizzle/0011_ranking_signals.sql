CREATE TABLE `ranking_preset` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`profile` text NOT NULL,
	`config` text NOT NULL,
	`is_active` integer DEFAULT false NOT NULL,
	`created_at` integer DEFAULT (cast(unixepoch('subsecond') * 1000 as integer)) NOT NULL,
	`updated_at` integer DEFAULT (cast(unixepoch('subsecond') * 1000 as integer)) NOT NULL
);
--> statement-breakpoint
CREATE INDEX `ranking_preset_profile_idx` ON `ranking_preset` (`profile`);--> statement-breakpoint
DROP INDEX `listing_score_idx`;--> statement-breakpoint
ALTER TABLE `listing` ADD `score_signals` text;--> statement-breakpoint
ALTER TABLE `listing` ADD `score_signals_version` text;--> statement-breakpoint
ALTER TABLE `listing` ADD `score_signals_at` integer;--> statement-breakpoint
CREATE INDEX `listing_signals_idx` ON `listing` (`property_type`,`score_signals_at`);
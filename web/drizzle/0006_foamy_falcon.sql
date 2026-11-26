ALTER TABLE `listing` ADD `score` integer;--> statement-breakpoint
ALTER TABLE `listing` ADD `score_profile` text;--> statement-breakpoint
ALTER TABLE `listing` ADD `scores` text;--> statement-breakpoint
ALTER TABLE `listing` ADD `score_reasons` text;--> statement-breakpoint
ALTER TABLE `listing` ADD `scored_at` integer;--> statement-breakpoint
ALTER TABLE `listing` ADD `score_version` text;--> statement-breakpoint
CREATE INDEX `listing_score_idx` ON `listing` (`property_type`,`score`);--> statement-breakpoint
CREATE INDEX `listing_first_seen_idx` ON `listing` (`first_seen_at`);
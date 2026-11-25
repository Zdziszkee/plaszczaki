ALTER TABLE `listing` ADD `canonical_key` text;--> statement-breakpoint
CREATE INDEX `listing_canonical_idx` ON `listing` (`canonical_key`);
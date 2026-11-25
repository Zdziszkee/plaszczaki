DROP INDEX `listing_bbox_idx`;--> statement-breakpoint
DROP INDEX `listing_status_idx`;--> statement-breakpoint
ALTER TABLE `listing` DROP COLUMN `detail_fetched_at`;--> statement-breakpoint
ALTER TABLE `listing` DROP COLUMN `detail_attempts`;--> statement-breakpoint
ALTER TABLE `listing` DROP COLUMN `detail_error`;--> statement-breakpoint
ALTER TABLE `listing` DROP COLUMN `status`;--> statement-breakpoint
ALTER TABLE `listing_snapshot` DROP COLUMN `status`;
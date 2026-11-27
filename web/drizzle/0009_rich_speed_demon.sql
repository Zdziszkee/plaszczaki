DROP INDEX `rcn_object_region_kind_idx`;--> statement-breakpoint
CREATE INDEX `rcn_object_region_layer_idx` ON `rcn_transaction_object` (`region_id`,`source_layer`,`last_seen_import_id`);
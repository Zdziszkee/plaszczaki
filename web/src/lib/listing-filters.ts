/** Server-only Drizzle conditions shared by the listing endpoints. */

import {
	and,
	between,
	eq,
	gte,
	isNotNull,
	lte,
	type SQL,
	sql,
} from "drizzle-orm";
import { listing } from "#/db/schema";
import type {
	BBox,
	ListingFilters,
	PostedWithin,
} from "./listing-filter-types";

type FilterKey = keyof ListingFilters;

const DAY_MS = 24 * 60 * 60 * 1000;

/** Newest-publication cutoff: start of the local day, or a rolling window. */
export function postedWithinCutoff(
	window: PostedWithin,
	now: Date = new Date(),
): number {
	if (window === "today") {
		const start = new Date(now);
		start.setHours(0, 0, 0, 0);
		return start.getTime();
	}
	if (window === "week") return now.getTime() - 7 * DAY_MS;
	return now.getTime() - 30 * DAY_MS;
}

/** Publication date with the scraper's discovery date as a fallback. */
export function postedAtExpression(): SQL<number> {
	return sql<number>`coalesce(${listing.publishedAt}, ${listing.firstSeenAt})`;
}

export function postedWithinCondition(window: PostedWithin, now?: Date): SQL {
	return gte(postedAtExpression(), postedWithinCutoff(window, now));
}

/**
 * Keep one representative row per canonical identity.
 *
 * Duplicates are preserved in the database (per-source provenance) and only
 * collapsed for the map. The representative is the row with the most complete
 * data, then the most recently seen.
 */
export function primaryListingCondition(): SQL {
	return sql`${listing.id} = (
		SELECT l2.id FROM listing l2
		WHERE coalesce(l2.canonical_key, 'id:' || l2.id)
			= coalesce(${listing.canonicalKey}, 'id:' || ${listing.id})
		ORDER BY
			(l2.lat IS NOT NULL) DESC,
			(l2.street IS NOT NULL) DESC,
			(l2.price_pln IS NOT NULL) DESC,
			(l2.area_m2 IS NOT NULL) DESC,
			(l2.description IS NOT NULL) DESC,
			l2.last_seen_at DESC,
			l2.id DESC
		LIMIT 1
	)`;
}

export function buildListingConditions(
	filters: ListingFilters,
	bbox: BBox | null,
	omit: readonly FilterKey[] = [],
): SQL[] {
	const skip = new Set<FilterKey>(omit);
	const conditions: SQL[] = [isNotNull(listing.lat), isNotNull(listing.lng)];

	// A source filter needs every row of that source, not only the rows that
	// won the cross-source representative selection.
	if (!filters.sourceId) {
		conditions.push(primaryListingCondition());
	}

	if (bbox) {
		conditions.push(
			between(listing.lat, bbox.minLat, bbox.maxLat),
			between(listing.lng, bbox.minLng, bbox.maxLng),
		);
	}

	if (filters.offerType && !skip.has("offerType")) {
		conditions.push(eq(listing.offerType, filters.offerType));
	}
	if (filters.propertyType && !skip.has("propertyType")) {
		conditions.push(eq(listing.propertyType, filters.propertyType));
	}
	if (filters.voivodeship && !skip.has("voivodeship")) {
		conditions.push(eq(listing.voivodeship, filters.voivodeship));
	}
	if (filters.sourceId && !skip.has("sourceId")) {
		conditions.push(eq(listing.sourceId, filters.sourceId));
	}
	if (filters.postedWithin && !skip.has("postedWithin")) {
		conditions.push(postedWithinCondition(filters.postedWithin));
	}
	if (filters.minPrice !== undefined && !skip.has("minPrice")) {
		conditions.push(gte(listing.pricePln, filters.minPrice));
	}
	if (filters.maxPrice !== undefined && !skip.has("maxPrice")) {
		conditions.push(lte(listing.pricePln, filters.maxPrice));
	}

	return conditions;
}

export function combineConditions(conditions: SQL[]): SQL | undefined {
	return and(...conditions);
}

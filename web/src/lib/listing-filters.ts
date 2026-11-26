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

export function buildListingConditions(
	filters: ListingFilters,
	bbox: BBox | null,
	omit: readonly FilterKey[] = [],
): SQL[] {
	const skip = new Set<FilterKey>(omit);
	const conditions: SQL[] = [isNotNull(listing.lat), isNotNull(listing.lng)];

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
	if (filters.minScore !== undefined && !skip.has("minScore")) {
		conditions.push(gte(listing.score, filters.minScore));
	}
	if (filters.scoreProfile && !skip.has("scoreProfile")) {
		conditions.push(
			sql`json_extract(${listing.scores}, '$."${filters.scoreProfile}"') IS NOT NULL`,
		);
	}

	return conditions;
}

export function combineConditions(conditions: SQL[]): SQL | undefined {
	return and(...conditions);
}

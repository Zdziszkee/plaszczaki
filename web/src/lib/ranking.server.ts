import { and, desc, eq, gte, inArray, isNotNull, lte } from "drizzle-orm";
import { db } from "#/db";
import { listing, rankingPreset, source } from "#/db/schema";
import {
	type RankingListing,
	type RankingResult,
	rankListings,
	sanitizeRankingConfig,
} from "#/lib/ranking-engine";
import {
	createDefaultRankingConfig,
	type ScoreProfile,
} from "#/lib/ranking-spec";
import type { RankingConfig } from "#/lib/ranking-types";

export type RankingPresetDto = {
	id: number;
	name: string;
	profile: ScoreProfile;
	config: RankingConfig;
	isActive: boolean;
	updatedAt: string;
};

export type RankingPageInput = { config: unknown };

function presetRow(row: typeof rankingPreset.$inferSelect): RankingPresetDto {
	return {
		id: row.id,
		name: row.name,
		profile: "universal",
		config: sanitizeRankingConfig(row.config),
		isActive: row.isActive,
		updatedAt: row.updatedAt.toISOString(),
	};
}

export async function listRankingPresetsData(): Promise<RankingPresetDto[]> {
	const rows = await db
		.select()
		.from(rankingPreset)
		.orderBy(desc(rankingPreset.isActive), desc(rankingPreset.updatedAt));
	return rows.map(presetRow);
}

export async function activeRankingConfigData(): Promise<RankingConfig> {
	const [preset] = await db
		.select()
		.from(rankingPreset)
		.where(eq(rankingPreset.isActive, true))
		.orderBy(desc(rankingPreset.updatedAt))
		.limit(1);
	return preset
		? sanitizeRankingConfig(preset.config)
		: createDefaultRankingConfig();
}

export async function rankingPageData(
	input: RankingPageInput,
): Promise<RankingResult> {
	const config = sanitizeRankingConfig(input.config);
	const filters = config.filters;
	const conditions = [isNotNull(listing.scoreSignals)];

	if (filters.propertyTypes.length > 0) {
		conditions.push(inArray(listing.propertyType, filters.propertyTypes));
	}
	if (filters.offerTypes.length > 0) {
		conditions.push(inArray(listing.offerType, filters.offerTypes));
	}
	if (filters.priceMin !== null)
		conditions.push(gte(listing.pricePln, filters.priceMin));
	if (filters.priceMax !== null)
		conditions.push(lte(listing.pricePln, filters.priceMax));
	if (filters.areaMin !== null)
		conditions.push(gte(listing.areaM2, filters.areaMin));
	if (filters.areaMax !== null)
		conditions.push(lte(listing.areaM2, filters.areaMax));
	if (filters.landAreaMin !== null) {
		conditions.push(gte(listing.landAreaM2, filters.landAreaMin));
	}
	if (filters.landAreaMax !== null) {
		conditions.push(lte(listing.landAreaM2, filters.landAreaMax));
	}
	if (filters.roomsMin !== null)
		conditions.push(gte(listing.rooms, filters.roomsMin));
	if (filters.roomsMax !== null)
		conditions.push(lte(listing.rooms, filters.roomsMax));
	if (filters.buildYearMin !== null) {
		conditions.push(gte(listing.buildYear, filters.buildYearMin));
	}
	if (filters.buildYearMax !== null) {
		conditions.push(lte(listing.buildYear, filters.buildYearMax));
	}
	if (filters.markets.length > 0) {
		conditions.push(inArray(listing.market, filters.markets));
	}
	if (filters.conditions.length > 0) {
		conditions.push(inArray(listing.condition, filters.conditions));
	}
	if (filters.heatingTypes.length > 0) {
		conditions.push(inArray(listing.heating, filters.heatingTypes));
	}
	if (filters.windowsTypes.length > 0) {
		conditions.push(inArray(listing.windowsType, filters.windowsTypes));
	}
	if (filters.postedWithin !== "all") {
		const days =
			filters.postedWithin === "today"
				? 1
				: filters.postedWithin === "week"
					? 7
					: 30;
		conditions.push(
			gte(listing.firstSeenAt, new Date(Date.now() - days * 86_400_000)),
		);
	}

	const rows = await db
		.select({
			id: listing.id,
			title: listing.title,
			url: listing.url,
			sourceId: listing.sourceId,
			sourceLabel: source.label,
			propertyType: listing.propertyType,
			offerType: listing.offerType,
			pricePln: listing.pricePln,
			pricePerM2Pln: listing.pricePerM2Pln,
			areaM2: listing.areaM2,
			landAreaM2: listing.landAreaM2,
			rooms: listing.rooms,
			floor: listing.floor,
			buildYear: listing.buildYear,
			buildingType: listing.buildingType,
			buildingMaterial: listing.buildingMaterial,
			condition: listing.condition,
			heating: listing.heating,
			windowsType: listing.windowsType,
			ceilingHeightM: listing.ceilingHeightM,
			elevator: listing.elevator,
			parking: listing.parking,
			balcony: listing.balcony,
			terrace: listing.terrace,
			garden: listing.garden,
			internet: listing.internet,
			airConditioning: listing.airConditioning,
			city: listing.city,
			district: listing.district,
			lat: listing.lat,
			lng: listing.lng,
			images: listing.images,
			publishedAt: listing.publishedAt,
			firstSeenAt: listing.firstSeenAt,
			scoreSignals: listing.scoreSignals,
		})
		.from(listing)
		.innerJoin(source, eq(source.id, listing.sourceId))
		.where(and(...conditions));

	const listings: RankingListing[] = rows.flatMap((row) => {
		if (!row.scoreSignals) return [];
		return [
			{
				...row,
				publishedAt: row.publishedAt?.getTime() ?? null,
				firstSeenAt: row.firstSeenAt.getTime(),
			},
		];
	});
	return rankListings(listings, config);
}

export type RankingInitialData = {
	profile: ScoreProfile;
	presets: RankingPresetDto[];
	selectedPresetId: number | null;
	config: RankingConfig;
	result: RankingResult;
};

export async function rankingInitialData(input: {
	profile?: ScoreProfile;
	presetId?: number;
}): Promise<RankingInitialData> {
	const profile = "universal" as const;
	const presets = await listRankingPresetsData();
	const selected =
		presets.find((preset) => preset.id === input.presetId) ??
		presets.find((preset) => preset.isActive) ??
		null;
	const config = selected?.config ?? createDefaultRankingConfig();
	const result = await rankingPageData({ config });
	return {
		profile,
		presets,
		selectedPresetId: selected?.id ?? null,
		config,
		result,
	};
}

export async function saveRankingPresetData(input: {
	name: string;
	config: RankingConfig;
}): Promise<RankingPresetDto> {
	const [row] = await db
		.insert(rankingPreset)
		.values({
			name: input.name,
			profile: "universal",
			config: input.config,
		})
		.returning();
	return presetRow(row);
}

export async function activateRankingPresetData(
	id: number,
): Promise<RankingPresetDto> {
	const [preset] = await db
		.select()
		.from(rankingPreset)
		.where(eq(rankingPreset.id, id))
		.limit(1);
	if (!preset) throw new Error("Preset nie istnieje");
	await db.update(rankingPreset).set({ isActive: false });
	await db
		.update(rankingPreset)
		.set({ isActive: true })
		.where(eq(rankingPreset.id, preset.id));
	return presetRow({ ...preset, isActive: true });
}

export async function deleteRankingPresetData(
	id: number,
): Promise<{ id: number }> {
	await db.delete(rankingPreset).where(eq(rankingPreset.id, id));
	return { id };
}

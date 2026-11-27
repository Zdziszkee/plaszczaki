import { createServerFn } from "@tanstack/react-start";
import {
	activateRankingPresetData,
	deleteRankingPresetData,
	listRankingPresetsData,
	rankingInitialData,
	rankingPageData,
	saveRankingPresetData,
} from "#/lib/ranking.server";
import { sanitizeRankingConfig } from "#/lib/ranking-engine";
import type { ScoreProfile } from "#/lib/ranking-types";

export type { RankingPresetDto } from "#/lib/ranking.server";

function objectValue(value: unknown): Record<string, unknown> {
	return value && typeof value === "object"
		? (value as Record<string, unknown>)
		: {};
}

function pageInput(value: unknown): { config: unknown } {
	const input = objectValue(value);
	if (!("config" in input)) throw new ValueError("Brak konfiguracji rankingu");
	return { config: input.config };
}

class ValueError extends Error {}

export const getRankingInitialData = createServerFn({ method: "GET" })
	.validator((value: unknown) => {
		const input = objectValue(value);
		const profile = input.profile;
		const presetId = input.presetId;
		return {
			profile:
				typeof profile === "string" &&
				[
					"kamienica",
					"nowe_mieszkanie",
					"dzialka_kolej",
					"dom_airbnb",
					"dom_kolej",
					"dzialka_airbnb",
				].includes(profile)
					? (profile as ScoreProfile)
					: undefined,
			presetId:
				typeof presetId === "number" &&
				Number.isInteger(presetId) &&
				presetId > 0
					? presetId
					: undefined,
		};
	})
	.handler(async ({ data }) => rankingInitialData(data));

export const getRankingPage = createServerFn({ method: "POST" })
	.validator((value: unknown) => pageInput(value))
	.handler(async ({ data }) => rankingPageData(data));

export const listRankingPresets = createServerFn({ method: "GET" }).handler(
	async () => listRankingPresetsData(),
);

export const saveRankingPreset = createServerFn({ method: "POST" })
	.validator((value: unknown) => {
		const input = objectValue(value);
		const name =
			typeof input.name === "string" ? input.name.trim().slice(0, 80) : "";
		if (!name) throw new ValueError("Podaj nazwę presetu");
		if (!("config" in input))
			throw new ValueError("Brak konfiguracji rankingu");
		return { name, config: sanitizeRankingConfig(input.config) };
	})
	.handler(async ({ data }) => saveRankingPresetData(data));

export const activateRankingPreset = createServerFn({ method: "POST" })
	.validator((value: unknown) => {
		const id = objectValue(value).id;
		if (typeof id !== "number" || !Number.isInteger(id) || id <= 0) {
			throw new ValueError("Nieprawidłowy preset");
		}
		return { id };
	})
	.handler(async ({ data }) => activateRankingPresetData(data.id));

export const deleteRankingPreset = createServerFn({ method: "POST" })
	.validator((value: unknown) => {
		const id = objectValue(value).id;
		if (typeof id !== "number" || !Number.isInteger(id) || id <= 0) {
			throw new ValueError("Nieprawidłowy preset");
		}
		return { id };
	})
	.handler(async ({ data }) => deleteRankingPresetData(data.id));

import { describe, expect, it } from "bun:test";
import {
	derivePricePerM2Pln,
	haversineKm,
	matchesRankingFilters,
	normalizeCategoryWeights,
	rankListings,
	sanitizeRankingConfig,
	scoreListing,
	summarizeResults,
} from "./ranking-engine";
import {
	BUILDING_TYPE_OPTIONS,
	CITY_CENTERS,
	createDefaultRankingConfig,
	DEFAULT_RANKING_CONFIG,
	RANKING_PROFILE_SPECS,
} from "./ranking-spec";
import type {
	LegacyScoreProfile,
	PriceEvidence,
	RankingBudgetCategory,
	RankingConfig,
	RankingListing,
	RankingProfileSignals,
	RankingSignals,
	ScoreProfile,
} from "./ranking-types";

function profileSignals(
	signals: Record<string, number | null> = {},
	penalties: Record<string, number | null> = {},
): RankingProfileSignals {
	return { signals, penalties };
}

function signals(
	profile: ScoreProfile | LegacyScoreProfile,
	values: RankingProfileSignals = profileSignals(),
	metrics: RankingSignals["metrics"] = {
		ceiling_height_m: null,
		large_windows: null,
	},
	priceEvidence: PriceEvidence | null = null,
	geo?: RankingSignals["geo"],
): RankingSignals {
	return {
		version: geo ? "signals-v2" : "signals-v1",
		profiles: { [profile]: values },
		geo,
		metrics,
		priceEvidence,
	};
}

function listing(overrides: Partial<RankingListing> = {}): RankingListing {
	return {
		id: 1,
		sourceId: "test",
		sourceLabel: "Test",
		externalId: "1",
		url: "https://example.com/1",
		offerType: "sale",
		propertyType: "flat",
		city: "Kraków",
		pricePln: 800_000,
		pricePerM2Pln: null,
		areaM2: 40,
		landAreaM2: null,
		rooms: 2,
		buildYear: 1925,
		buildingType: "tenement",
		publishedAt: Date.UTC(2026, 10, 20),
		firstSeenAt: Date.UTC(2026, 10, 20),
		lat: CITY_CENTERS.krakow.lat,
		lng: CITY_CENTERS.krakow.lng,
		scoreSignals: signals("kamienica"),
		...overrides,
	};
}

function isolatedConfig(
	profile: ScoreProfile | LegacyScoreProfile,
	category: RankingBudgetCategory,
	signalWeights: Record<string, number>,
	penaltyWeights: Record<string, number> = {},
): RankingConfig {
	const config = createDefaultRankingConfig("universal");
	void profile;
	for (const key of [
		"price",
		"location",
		"attributes",
		"nature",
		"opportunity",
	] as const) {
		config.categoryWeights[key] = key === category ? 100 : 0;
	}
	for (const code of Object.keys(config.signalWeights))
		config.signalWeights[code] = 0;
	for (const code of Object.keys(config.penaltyWeights))
		config.penaltyWeights[code] = 0;
	config.distanceWeights = {
		rail_station: 0,
		tram_stop: 0,
		lake: 0,
		river: 0,
		forest: 0,
		city_center: 1,
	};
	Object.assign(config.signalWeights, signalWeights);
	Object.assign(config.penaltyWeights, penaltyWeights);
	config.filters.resultLimit = 5000;
	return config;
}

function priceEvidence(confidence: PriceEvidence["confidence"]): PriceEvidence {
	return {
		listing: null,
		rcn: null,
		baseline: null,
		benchmark: 20_000,
		benchmarkSource: "baseline",
		discount: 0.1,
		effectiveDiscount: 0.1,
		confidence,
		overpriced: false,
		topEligible: true,
	};
}

describe("ranking configuration", () => {
	it("exports one universal profile spec and the integration constants", () => {
		expect(Object.keys(RANKING_PROFILE_SPECS)).toEqual(["universal"]);
		expect(DEFAULT_RANKING_CONFIG.profile).toBe("universal");
		expect(DEFAULT_RANKING_CONFIG.filters.resultLimit).toBe(25);
		expect(DEFAULT_RANKING_CONFIG.filters.elevator).toBe("any");
		expect(DEFAULT_RANKING_CONFIG.filters.parking).toBe("any");
		expect(DEFAULT_RANKING_CONFIG.filters.districtHeating).toBe("any");
		expect(BUILDING_TYPE_OPTIONS.length).toBeGreaterThan(0);
		expect(CITY_CENTERS.katowice.lat).toBe(50.2599);
	});

	it("normalizes category weights to 100", () => {
		const normalized = normalizeCategoryWeights({
			price: 1,
			location: 3,
			attributes: 0,
			nature: -5,
			opportunity: 1,
		});
		expect(normalized.price).toBe(20);
		expect(normalized.location).toBe(60);
		expect(normalized.opportunity).toBe(20);
		expect(normalized.nature).toBe(0);
		expect(
			Object.values(normalized).reduce((sum, value) => sum + value, 0),
		).toBe(100);
	});

	it("scores v2 geo distances and migrates legacy profiles", () => {
		const config = isolatedConfig("kamienica", "location", {});
		config.distanceWeights = {
			rail_station: 50,
			tram_stop: 10,
			lake: 10,
			river: 10,
			forest: 10,
			city_center: 10,
		};
		const result = scoreListing(
			listing({
				scoreSignals: {
					version: "signals-v2",
					profiles: {},
					geo: {
						rail_station: 1,
						tram_stop: 3,
						lake: 3,
						river: 3,
						forest: 3,
					},
					metrics: { ceiling_height_m: null, large_windows: null },
					priceEvidence: null,
				},
			}),
			config,
		);
		expect(result.score).toBe(60);

		const legacy = scoreListing(
			listing({
				propertyType: "house",
				scoreSignals: signals("dom_kolej", profileSignals({ ceiling: 1 })),
			}),
			isolatedConfig("dom_kolej", "attributes", { ceiling: 1 }),
		);
		expect(legacy.score).toBe(100);

		const mixedVersion = scoreListing(
			listing({
				scoreSignals: {
					...signals("universal", profileSignals()),
					profiles: {
						universal: profileSignals(),
						kamienica: profileSignals({ ceiling: 1 }),
					},
				},
			}),
			isolatedConfig("kamienica", "attributes", { ceiling: 1 }),
		);
		expect(mixedVersion.score).toBe(100);
	});

	it("normalizes signal weights within the selected category budget", () => {
		const config = isolatedConfig("kamienica", "attributes", {
			ceiling: 1,
			large_windows: 2,
		});
		const result = scoreListing(
			listing({
				scoreSignals: signals(
					"kamienica",
					profileSignals({ ceiling: 0.5, large_windows: 1 }),
				),
			}),
			config,
		);
		const ceiling = result.contributions.find(
			(item) => item.code === "ceiling",
		);
		const windows = result.contributions.find(
			(item) => item.code === "large_windows",
		);
		expect(ceiling?.effectiveMaxPoints).toBeCloseTo(100 / 3);
		expect(windows?.effectiveMaxPoints).toBeCloseTo(200 / 3);
		expect(result.rawScore).toBeCloseTo(250 / 3);
		expect(result.score).toBe(83);
	});

	it("sanitizes malformed input without mutating it", () => {
		const input = {
			profile: "nowe_mieszkanie",
			version: 42,
			categoryWeights: { price: -1, location: "bad" },
			signalWeights: { ceiling: 0, unknown: 10 },
			filters: {
				propertyTypes: ["flat", "bogus"],
				priceMin: "10",
				priceMax: 5,
				resultLimit: -2,
				center: "elsewhere",
				sourceIds: ["must-not-be-used"],
				elevator: "yes",
				parking: "no",
				districtHeating: "invalid",
			},
			options: { missingPolicy: "invalid", requirePriceEvidence: "yes" },
		};
		const snapshot = structuredClone(input);
		const config = sanitizeRankingConfig(input);
		expect(input).toEqual(snapshot);
		expect(config.profile).toBe("universal");
		expect(config.version).toBe("ranking-v2");
		expect(config.filters.propertyTypes).toEqual(["flat"]);
		expect(config.filters.offerTypes).toEqual(["sale", "auction"]);
		expect(config.filters.priceMin).toBe(5);
		expect(config.filters.priceMax).toBe(10);
		expect(config.filters.resultLimit).toBe(25);
		expect(config.filters.center).toBe("nearest");
		expect(config.filters.elevator).toBe("yes");
		expect(config.filters.parking).toBe("no");
		expect(config.filters.districtHeating).toBe("any");
		expect(config.signalWeights.ceiling).toBe(0);
		expect(config.signalWeights.unknown).toBeUndefined();
		expect(config.options).toEqual({
			requirePriceEvidence: false,
			minPriceConfidence: "any",
			missingPolicy: "zero",
		});
		expect(config.filters).not.toHaveProperty("sourceIds");
	});

	it("falls back to defaults for non-object input", () => {
		expect(sanitizeRankingConfig(null)).toEqual(DEFAULT_RANKING_CONFIG);
	});
});

describe("ranking score", () => {
	it("counts missing values as zero and can exclude them", () => {
		const config = isolatedConfig("kamienica", "attributes", {
			ceiling: 1,
			large_windows: 1,
		});
		const target = listing({
			scoreSignals: signals("kamienica", profileSignals({ ceiling: 1 })),
		});
		const zeroPolicy = scoreListing(target, config);
		expect(zeroPolicy.score).toBe(50);
		expect(zeroPolicy.missing).toContain("Duże okna");

		const excludeConfig = {
			...config,
			options: { ...config.options, missingPolicy: "exclude" as const },
		};
		const excluded = rankListings([target], excludeConfig, 1000);
		expect(excluded.totalMatched).toBe(1);
		expect(excluded.totalScored).toBe(1);
		expect(excluded.excludedMissing).toBe(1);
		expect(excluded.items).toHaveLength(0);
	});

	it("scores large windows as a first-class residential signal", () => {
		const config = isolatedConfig("kamienica", "attributes", {
			large_windows: 1,
		});
		const result = scoreListing(
			listing({
				scoreSignals: signals(
					"kamienica",
					profileSignals({ large_windows: 1 }),
					{ ceiling_height_m: null, large_windows: true },
					null,
				),
			}),
			config,
		);
		expect(result.score).toBe(100);
		expect(result.contributions[0]?.code).toBe("large_windows");
		expect(result.contributions[0]?.rawValue).toBe(1);
	});

	it("applies penalties before caps and reports both", () => {
		const config = isolatedConfig(
			"kamienica",
			"price",
			{ price_quantile: 1 },
			{ overpriced: 5 },
		);
		const result = scoreListing(
			listing({
				scoreSignals: signals(
					"kamienica",
					profileSignals({ price_quantile: 1 }, { overpriced: 1 }),
					undefined,
					priceEvidence("high"),
				),
			}),
			config,
		);
		expect(result.rawScore).toBe(95);
		expect(result.penalties[0]?.points).toBe(-5);
		expect(result.cap).toBe(65);
		expect(result.capLabel).toBe("Limit dla ceny powyżej rynku");
		expect(result.score).toBe(65);
	});

	it("uses half-up rounding and clamps the final score", () => {
		const config = isolatedConfig("kamienica", "price", {
			price_quantile: 1,
		});
		const target = listing({
			scoreSignals: signals(
				"kamienica",
				profileSignals({ price_quantile: 0.645 }),
			),
		});
		expect(scoreListing(target, config).score).toBe(65);
		expect(
			scoreListing(
				listing({
					scoreSignals: signals(
						"kamienica",
						profileSignals({ price_quantile: 1.5 }),
					),
				}),
				config,
			).score,
		).toBe(100);
	});

	it("scores flats, houses and plots with the same universal configuration", () => {
		for (const propertyType of ["flat", "house", "plot"]) {
			const result = scoreListing(
				listing({ propertyType }),
				createDefaultRankingConfig(),
			);
			expect(result.eligible).not.toBe(false);
		}
	});
});

describe("ranking filters", () => {
	it("filters large windows only from first-class metrics", () => {
		const target = listing({
			scoreSignals: signals("kamienica", profileSignals(), {
				ceiling_height_m: null,
				large_windows: true,
			}),
		});
		const yes = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: { ...DEFAULT_RANKING_CONFIG.filters, largeWindows: "yes" },
		});
		const no = {
			...yes,
			filters: { ...yes.filters, largeWindows: "no" as const },
		};
		expect(matchesRankingFilters(target, yes)).toBe(true);
		expect(matchesRankingFilters(target, no)).toBe(false);
		expect(
			matchesRankingFilters(
				{ ...target, scoreSignals: signals("kamienica") },
				no,
			),
		).toBe(false);
	});

	it("filters amenities and parking with yes/no semantics", () => {
		const yesConfig = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: {
				...DEFAULT_RANKING_CONFIG.filters,
				elevator: "yes",
				parking: "yes",
				balcony: "yes",
				terrace: "yes",
				garden: "yes",
				internet: "yes",
				airConditioning: "yes",
			},
		});
		const present = listing({
			elevator: true,
			parking: "garage",
			balcony: true,
			terrace: true,
			garden: true,
			internet: true,
			airConditioning: true,
		});
		expect(matchesRankingFilters(present, yesConfig)).toBe(true);
		expect(
			matchesRankingFilters({ ...present, parking: "none" }, yesConfig),
		).toBe(false);

		const noConfig = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: {
				...DEFAULT_RANKING_CONFIG.filters,
				elevator: "no",
				parking: "no",
				balcony: "no",
				terrace: "no",
				garden: "no",
				internet: "no",
				airConditioning: "no",
			},
		});
		const absent = listing({
			elevator: false,
			parking: "none",
			balcony: false,
			terrace: false,
			garden: false,
			internet: false,
			airConditioning: false,
		});
		expect(matchesRankingFilters(absent, noConfig)).toBe(true);
		expect(
			matchesRankingFilters(
				listing({
					elevator: null,
					parking: null,
					balcony: null,
					terrace: null,
					garden: null,
					internet: null,
					airConditioning: null,
				}),
				noConfig,
			),
		).toBe(false);
	});

	it("recognizes normalized district-heating values", () => {
		const config = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: { ...DEFAULT_RANKING_CONFIG.filters, districtHeating: "yes" },
		});
		for (const heating of ["district", "Miejskie", "co miejskie"]) {
			expect(matchesRankingFilters(listing({ heating }), config)).toBe(true);
		}
		expect(matchesRankingFilters(listing({ heating: "gas" }), config)).toBe(
			false,
		);
		expect(matchesRankingFilters(listing({ heating: null }), config)).toBe(
			false,
		);
	});

	it("uses signal ceiling metrics with the canonical field as fallback", () => {
		const config = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: { ...DEFAULT_RANKING_CONFIG.filters, minCeilingHeight: 3 },
		});
		const fromMetrics = listing({
			ceilingHeightM: 2.4,
			scoreSignals: signals("kamienica", profileSignals(), {
				ceiling_height_m: 3.2,
				large_windows: null,
			}),
		});
		const fromCanonical = listing({
			ceilingHeightM: 3.1,
			scoreSignals: signals("kamienica"),
		});
		expect(matchesRankingFilters(fromMetrics, config)).toBe(true);
		expect(matchesRankingFilters(fromCanonical, config)).toBe(true);
		expect(
			matchesRankingFilters({ ...fromMetrics, ceilingHeightM: 2.4 }, config),
		).toBe(true);
	});

	it("normalizes building type aliases and expands groups", () => {
		const config = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: { ...DEFAULT_RANKING_CONFIG.filters, buildingTypes: ["block"] },
		});
		expect(
			matchesRankingFilters(listing({ buildingType: "blok" }), config),
		).toBe(true);
		expect(
			matchesRankingFilters(
				listing({ buildingType: "prefabricated_concrete" }),
				config,
			),
		).toBe(true);
		expect(
			matchesRankingFilters(listing({ buildingType: "detached_house" }), {
				...config,
				filters: { ...config.filters, buildingTypes: ["house"] },
			}),
		).toBe(true);
		expect(
			matchesRankingFilters(listing({ buildingType: "tenement" }), config),
		).toBe(false);
	});

	it("computes haversine distance from the selected center", () => {
		expect(haversineKm(50.0614, 19.9366, 50.0614, 19.9366)).toBe(0);
		const target = listing({ lat: 50.0714, lng: 19.9366 });
		const config = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: {
				...DEFAULT_RANKING_CONFIG.filters,
				center: "krakow",
				maxCenterDistanceKm: 2,
			},
		});
		expect(matchesRankingFilters(target, config)).toBe(true);
		const result = rankListings([target], config, 1000);
		expect(result.items[0]?.distanceKm).toBeCloseTo(1.11, 1);
		expect(
			matchesRankingFilters(target, {
				...config,
				filters: { ...config.filters, maxCenterDistanceKm: 1 },
			}),
		).toBe(false);
	});

	it("uses the nearest major city centre by default", () => {
		const config = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: {
				...DEFAULT_RANKING_CONFIG.filters,
				center: "nearest",
			},
		});
		const krakow = listing({
			id: 1,
			city: "Kraków",
			lat: CITY_CENTERS.krakow.lat,
			lng: CITY_CENTERS.krakow.lng,
		});
		const katowice = listing({
			id: 2,
			city: "Katowice",
			lat: CITY_CENTERS.katowice.lat,
			lng: CITY_CENTERS.katowice.lng,
		});
		const result = rankListings([krakow, katowice], config, 1000);
		const byId = new Map(result.items.map((item) => [item.listing.id, item]));

		expect(byId.get(1)?.centerCity).toBe("krakow");
		expect(byId.get(1)?.distanceKm).toBeCloseTo(0, 5);
		expect(byId.get(2)?.centerCity).toBe("katowice");
		expect(byId.get(2)?.distanceKm).toBeCloseTo(0, 5);

		const nearbyKatowice = {
			...config,
			filters: { ...config.filters, maxCenterDistanceKm: 1 },
		};
		expect(matchesRankingFilters(katowice, nearbyKatowice)).toBe(true);
	});

	it("derives price per square metre with canonical and plot fallbacks", () => {
		expect(
			derivePricePerM2Pln(
				listing({ pricePln: 800_000, areaM2: 40, pricePerM2Pln: 21_000 }),
			),
		).toBe(21_000);
		expect(
			derivePricePerM2Pln(
				listing({ pricePln: 800_000, areaM2: 40, pricePerM2Pln: null }),
			),
		).toBe(20_000);
		expect(
			derivePricePerM2Pln(
				listing({
					propertyType: "plot",
					pricePln: 100_000,
					areaM2: null,
					landAreaM2: 500,
					pricePerM2Pln: null,
				}),
			),
		).toBe(200);
	});

	it("requires price evidence and the requested confidence", () => {
		const target = listing({
			scoreSignals: signals(
				"kamienica",
				profileSignals(),
				undefined,
				priceEvidence("low"),
			),
		});
		const required = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			options: {
				...DEFAULT_RANKING_CONFIG.options,
				requirePriceEvidence: true,
				minPriceConfidence: "medium",
			},
		});
		expect(matchesRankingFilters(target, required)).toBe(false);
		expect(
			matchesRankingFilters(
				{
					...target,
					scoreSignals: signals(
						"kamienica",
						profileSignals(),
						undefined,
						priceEvidence("high"),
					),
				},
				required,
			),
		).toBe(true);
		expect(
			matchesRankingFilters(
				{ ...target, scoreSignals: signals("kamienica") },
				required,
			),
		).toBe(false);
	});

	it("filters epoch-millisecond publication dates", () => {
		const now = Date.UTC(2026, 10, 27, 12);
		const config = sanitizeRankingConfig({
			...DEFAULT_RANKING_CONFIG,
			filters: { ...DEFAULT_RANKING_CONFIG.filters, postedWithin: "week" },
		});
		expect(
			matchesRankingFilters(
				listing({ publishedAt: now - 2 * 86_400_000 }),
				config,
				now,
			),
		).toBe(true);
		expect(
			matchesRankingFilters(
				listing({ publishedAt: now - 8 * 86_400_000 }),
				config,
				now,
			),
		).toBe(false);
	});
});

describe("ranking results", () => {
	it("sorts ties deterministically regardless of input order", () => {
		const config = isolatedConfig("kamienica", "attributes", { ceiling: 1 });
		const targets = [3, 1, 2].map((id) =>
			listing({
				id,
				scoreSignals: signals("kamienica", profileSignals({ ceiling: 1 })),
			}),
		);
		const forward = rankListings(targets, config, 1000);
		const reverse = rankListings([...targets].reverse(), config, 1000);
		expect(forward.items.map((item) => item.listing.id)).toEqual([1, 2, 3]);
		expect(reverse.items.map((item) => item.listing.id)).toEqual([1, 2, 3]);
		expect(forward.items.map((item) => item.rank)).toEqual([1, 2, 3]);
		expect(forward.items.every((item) => item.eligible === true)).toBe(true);
	});

	it("supports newest and price sorting plus result limits", () => {
		const config = isolatedConfig("kamienica", "attributes", { ceiling: 1 });
		const olderCheap = listing({
			id: 1,
			pricePln: 100,
			publishedAt: 100,
			scoreSignals: signals("kamienica", profileSignals({ ceiling: 1 })),
		});
		const newerExpensive = listing({
			id: 2,
			pricePln: 200,
			publishedAt: 200,
			scoreSignals: signals("kamienica", profileSignals({ ceiling: 1 })),
		});
		const newest = rankListings(
			[olderCheap, newerExpensive],
			{
				...config,
				filters: { ...config.filters, sort: "newest", resultLimit: 1 },
			},
			1000,
		);
		expect(newest.items[0]?.listing.id).toBe(2);
		expect(newest.totalScored).toBe(2);
		const cheapest = rankListings(
			[newerExpensive, olderCheap],
			{
				...config,
				filters: { ...config.filters, sort: "price", resultLimit: 5000 },
			},
			1000,
		);
		expect(cheapest.items.map((item) => item.listing.id)).toEqual([1, 2]);
	});

	it("reports aggregate counts and a deterministic generatedAt", () => {
		const config = isolatedConfig("kamienica", "attributes", { ceiling: 1 });
		config.options.missingPolicy = "exclude";
		config.filters.cities = ["Kraków"];
		const matched = listing({
			id: 1,
			scoreSignals: signals("kamienica", profileSignals({ ceiling: 1 })),
		});
		const missing = listing({ id: 2 });
		const filtered = listing({ id: 3, city: "Warszawa" });
		const result = rankListings([matched, missing, filtered], config, 1234);
		expect(result.totalMatched).toBe(2);
		expect(result.totalScored).toBe(2);
		expect(result.excludedMissing).toBe(1);
		expect(result.items).toHaveLength(1);
		expect(result.generatedAt).toBe(1234);
		expect(summarizeResults(result)).toMatchObject({
			count: 1,
			averageScore: 100,
			minScore: 100,
			maxScore: 100,
		});
	});
});

import { describe, expect, it } from "bun:test";
import {
	DISTANCE_SIGNAL_CODES,
	normalizeDistanceWeights,
	redistributeDistanceWeights,
	redistributePercentageWeights,
} from "./ranking-weights";

describe("live ranking weight redistribution", () => {
	it("moves one slider and proportionally updates all the others", () => {
		const weights = redistributePercentageWeights(
			["price", "location", "nature"],
			{ price: 50, location: 30, nature: 20 },
			"price",
			60,
		);
		expect(weights).toEqual({ price: 60, location: 24, nature: 16 });
		expect(Object.values(weights).reduce((sum, value) => sum + value, 0)).toBe(
			100,
		);
	});

	it("moves weight back into the other sliders when one decreases", () => {
		const weights = redistributePercentageWeights(
			["price", "location", "nature"],
			{ price: 60, location: 24, nature: 16 },
			"price",
			20,
		);
		expect(weights.price).toBe(20);
		expect(weights.location).toBe(48);
		expect(weights.nature).toBe(32);
	});

	it("handles zero, 100 and equal zero-weight shares without rounding drift", () => {
		const keys = ["a", "b", "c"] as const;
		expect(
			redistributePercentageWeights(keys, { a: 20, b: 30, c: 50 }, "a", 100),
		).toEqual({ a: 100, b: 0, c: 0 });
		expect(
			redistributePercentageWeights(keys, { a: 100, b: 0, c: 0 }, "a", 0),
		).toEqual({ a: 0, b: 50, c: 50 });
		expect(
			redistributePercentageWeights(keys, { a: 1, b: 1, c: 1 }, "a", 67),
		).toEqual({ a: 67, b: 17, c: 16 });
	});

	it("normalizes lopsided inputs and corrects rounding drift", () => {
		const weights = normalizeDistanceWeights({
			rail_station: 98,
			tram_stop: 1,
			lake: 1,
			river: 0,
			forest: 0,
			city_center: 0,
		});
		expect(weights.rail_station).toBe(98);
		expect(weights.tram_stop).toBe(1);
		expect(weights.lake).toBe(1);
		expect(
			Object.values(weights).reduce((sum, weight) => sum + weight, 0),
		).toBe(100);
		const thirds = normalizeDistanceWeights({
			rail_station: 1,
			tram_stop: 1,
			lake: 1,
			river: 1,
			forest: 0,
			city_center: 0,
		});
		expect(thirds).toEqual({
			rail_station: 25,
			tram_stop: 25,
			lake: 25,
			river: 25,
			forest: 0,
			city_center: 0,
		});
	});

	it("keeps every distance slider combination at exactly 100", () => {
		let weights = normalizeDistanceWeights({
			rail_station: 25,
			tram_stop: 15,
			lake: 10,
			river: 10,
			forest: 15,
			city_center: 25,
		});
		for (const code of DISTANCE_SIGNAL_CODES) {
			for (const value of [0, 1, 17, 33, 66, 99, 100]) {
				weights = redistributeDistanceWeights(weights, code, value);
				expect(weights[code]).toBe(value);
				expect(
					Object.values(weights).reduce((sum, weight) => sum + weight, 0),
				).toBe(100);
			}
		}
	});
});

import { describe, expect, it } from "bun:test";
import {
	aggregateHexagons,
	formatCompactCount,
	type HexAggregateInput,
} from "./hex-aggregation";

function row(overrides: Partial<HexAggregateInput> = {}): HexAggregateInput {
	return {
		lat: 50.06,
		lng: 19.94,
		count: 1,
		minPrice: 400_000,
		maxPrice: 400_000,
		...overrides,
	};
}

describe("hexagonal aggregation", () => {
	it("combines counts, weighted positions, and price ranges", () => {
		const [group] = aggregateHexagons(
			[
				row({ count: 2, minPrice: 300_000, maxPrice: 500_000 }),
				row({ lat: 50.061, lng: 19.941, count: 1, minPrice: 200_000 }),
			],
			0.1,
		);

		expect(group.count).toBe(3);
		expect(group.lat).toBeCloseTo((50.06 * 2 + 50.061) / 3, 10);
		expect(group.lng).toBeCloseTo((19.94 * 2 + 19.941) / 3, 10);
		expect(group.minPrice).toBe(200_000);
		expect(group.maxPrice).toBe(500_000);
		expect(group.id).toStartWith("hex:0.1:");
	});

	it("keeps null prices null and ignores invalid rows", () => {
		const groups = aggregateHexagons(
			[
				row({ minPrice: null, maxPrice: null }),
				row({ lat: Number.NaN, count: 100 }),
				row({ count: 0 }),
			],
			0.1,
		);

		expect(groups).toHaveLength(1);
		expect(groups[0]?.count).toBe(1);
		expect(groups[0]?.minPrice).toBeNull();
		expect(groups[0]?.maxPrice).toBeNull();
	});

	it("does not preserve a square grid distribution", () => {
		const precision = 0.1;
		const rows = Array.from({ length: 25 }, (_, index) =>
			row({
				lat: 49.8 + Math.floor(index / 5) * precision,
				lng: 19.8 + (index % 5) * precision,
			}),
		);
		const groups = aggregateHexagons(rows, precision);

		expect(groups.length).toBeLessThan(rows.length);
		expect(groups.reduce((sum, group) => sum + group.count, 0)).toBe(25);
		expect(new Set(groups.map((group) => group.id)).size).toBe(groups.length);
	});

	it("is deterministic regardless of input order", () => {
		const rows = [
			row({ lat: 50.06, lng: 19.94, count: 5 }),
			row({ lat: 50.2, lng: 20.1, count: 2 }),
			row({ lat: 50.21, lng: 20.11, count: 3 }),
		];

		expect(aggregateHexagons(rows, 0.25)).toEqual(
			aggregateHexagons([...rows].reverse(), 0.25),
		);
	});

	it("sorts by descending count and rejects invalid precision", () => {
		const groups = aggregateHexagons(
			[
				row({ lat: 50.06, lng: 19.94, count: 2 }),
				row({ lat: 50.2, lng: 20.1, count: 8 }),
			],
			0.1,
		);

		expect(groups.map((group) => group.count)).toEqual([8, 2]);
		expect(() => aggregateHexagons([], 0)).toThrow(RangeError);
	});

	it("formats compact Polish counts", () => {
		expect(formatCompactCount(999)).toBe("999");
		expect(formatCompactCount(1_200)).toBe("1,2 tys.");
		expect(formatCompactCount(12_886)).toBe("13 tys.");
		expect(formatCompactCount(2_000_000)).toBe("2 mln");
	});
});

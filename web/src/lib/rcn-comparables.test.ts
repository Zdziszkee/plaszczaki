import { describe, expect, it } from "bun:test";
import {
	type ComparableListing,
	haversineKm,
	quantile,
	summarizeComparables,
} from "./rcn-comparables";
import type { RcnCandidate } from "./rcn-types";

const listing: ComparableListing = {
	propertyType: "flat",
	market: "secondary",
	lat: 50.06,
	lng: 19.94,
	areaM2: 50,
	rooms: 2,
	street: "Plac Kossaka 5",
	city: "Kraków",
};

function candidate(
	index: number,
	overrides: Partial<RcnCandidate> = {},
): RcnCandidate {
	return {
		id: index,
		transactionId: index,
		objectKind: "flat",
		pricePln: 400_000 + index * 10_000,
		areaM2: 50,
		pricePerM2Pln: 8000 + index * 200,
		lat: 50.0605,
		lng: 19.9405,
		distanceKm: 0.08 + index * 0.05,
		documentDate: "2026-01-01",
		market: "secondary",
		addressText: "MSC:KRAKÓW;UL:INNA;NR_PORZ:1",
		city: "KRAKÓW",
		street: "INNA 1",
		rooms: 2,
		floor: 2,
		...overrides,
	};
}

describe("RCN comparables", () => {
	it("computes distance and quantiles", () => {
		expect(haversineKm(50.06, 19.94, 50.0605, 19.9405)).toBeLessThan(0.1);
		expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
		expect(quantile([], 0.5)).toBeNull();
	});

	it("summarizes nearby same-market sales", () => {
		const result = summarizeComparables(
			listing,
			Array.from({ length: 6 }, (_, index) => candidate(index)),
		);

		expect(result.status).toBe("ok");
		expect(result.count).toBe(6);
		expect(result.radiusKm).toBe(1);
		expect(result.confidence).toBe("medium");
		expect(result.medianPricePerM2Pln).toBe(8500);
		expect(result.weightedPricePerM2Pln).not.toBeNull();
		expect(result.samples).toHaveLength(5);
	});

	it("marks residential-building comparisons as low confidence", () => {
		const result = summarizeComparables(
			{ ...listing, propertyType: "house", areaM2: 140, rooms: null },
			Array.from({ length: 6 }, (_, index) =>
				candidate(index, { objectKind: "building", areaM2: 140, rooms: null }),
			),
		);

		expect(result.status).toBe("ok");
		expect(result.count).toBe(6);
		expect(result.confidence).toBe("low");
	});

	it("does not mix primary-market sales into secondary listings", () => {
		const result = summarizeComparables(
			listing,
			Array.from({ length: 6 }, (_, index) =>
				candidate(index, { market: "primary" }),
			),
		);

		expect(result.status).toBe("unavailable");
		expect(result.reason).toBe("insufficient_comparables");
	});

	it("expands the radius when the immediate area is sparse", () => {
		const result = summarizeComparables(listing, [
			candidate(1, { distanceKm: 0.5 }),
			candidate(2, { distanceKm: 0.6 }),
			candidate(3, { distanceKm: 1.5 }),
			candidate(4, { distanceKm: 1.6 }),
			candidate(5, { distanceKm: 1.7 }),
		]);

		expect(result.status).toBe("ok");
		expect(result.radiusKm).toBe(2.5);
		expect(result.count).toBe(5);
	});

	it("identifies previous sales at the same normalized address", () => {
		const result = summarizeComparables(listing, [
			candidate(1, { street: "Plac Kossaka 5" }),
			candidate(2, { street: "Inna 2" }),
			candidate(3, { street: "Inna 3" }),
			candidate(4, { street: "Inna 4" }),
			candidate(5, { street: "Inna 5" }),
		]);

		expect(result.status).toBe("ok");
		expect(result.sameAddress).toHaveLength(1);
		expect(result.sameAddress[0]?.id).toBe(1);
	});

	it("reports unavailable for unsupported property types", () => {
		const result = summarizeComparables(
			{ ...listing, propertyType: "commercial" },
			[candidate(1)],
		);

		expect(result.status).toBe("unavailable");
		expect(result.reason).toBe("unsupported_property_type");
	});
});

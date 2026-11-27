/** Reference latitude used to keep hex area comparable to the old degree bins. */
const REFERENCE_LATITUDE_RADIANS = (52 * Math.PI) / 180;
const HEX_AREA = (3 * Math.sqrt(3)) / 2;
const HEX_RADIUS_PER_PRECISION = Math.sqrt(
	2 / (HEX_AREA * Math.cos(REFERENCE_LATITUDE_RADIANS)),
);
const MAX_MERCATOR_LATITUDE = 85.0511287798066;

export type HexAggregateInput = {
	lat: number;
	lng: number;
	count: number;
	minPrice: number | null;
	maxPrice: number | null;
};

export type HexAggregate = {
	id: string;
	lat: number;
	lng: number;
	count: number;
	minPrice: number | null;
	maxPrice: number | null;
	countLabel: string;
};

const compactNumber = new Intl.NumberFormat("pl-PL", {
	notation: "compact",
	maximumFractionDigits: 1,
});
const compactLargeNumber = new Intl.NumberFormat("pl-PL", {
	notation: "compact",
	maximumFractionDigits: 0,
});

export function formatCompactCount(count: number): string {
	return (count >= 10_000 ? compactLargeNumber : compactNumber)
		.format(count)
		.replace(/\u00a0/g, " ");
}

function mercatorY(latitude: number): number {
	const clamped = Math.max(
		-MAX_MERCATOR_LATITUDE,
		Math.min(MAX_MERCATOR_LATITUDE, latitude),
	);
	const radians = (clamped * Math.PI) / 180;
	return (Math.log(Math.tan(Math.PI / 4 + radians / 2)) * 180) / Math.PI;
}

function hexKey(latitude: number, longitude: number, size: number): string {
	const x = longitude;
	const y = mercatorY(latitude);
	const q = (Math.sqrt(3) / 3) * x - y / 3;
	const r = (2 * y) / 3;

	const cubeX = q / size;
	const cubeZ = r / size;
	const cubeY = -cubeX - cubeZ;
	let roundedX = Math.round(cubeX);
	let roundedY = Math.round(cubeY);
	const roundedZ = Math.round(cubeZ);

	const xDifference = Math.abs(roundedX - cubeX);
	const yDifference = Math.abs(roundedY - cubeY);
	const zDifference = Math.abs(roundedZ - cubeZ);
	if (xDifference > yDifference && xDifference > zDifference) {
		roundedX = -roundedY - roundedZ;
	} else if (yDifference > zDifference) {
		roundedY = -roundedX - roundedZ;
	}

	return `${roundedX}:${roundedZ}`;
}

/**
 * Group pre-aggregated points into a Web Mercator hex lattice. Counts and price
 * ranges stay exact; positions are count-weighted centroids of the fine bins.
 */
export function aggregateHexagons(
	rows: readonly HexAggregateInput[],
	precision: number,
): HexAggregate[] {
	if (!Number.isFinite(precision) || precision <= 0) {
		throw new RangeError("precision must be a positive finite number");
	}

	const size = precision * HEX_RADIUS_PER_PRECISION;
	const groups = new Map<
		string,
		{
			count: number;
			latSum: number;
			lngSum: number;
			minPrice: number | null;
			maxPrice: number | null;
		}
	>();

	for (const row of rows) {
		if (
			!Number.isFinite(row.lat) ||
			!Number.isFinite(row.lng) ||
			!Number.isSafeInteger(row.count) ||
			row.count <= 0
		) {
			continue;
		}
		const key = hexKey(row.lat, row.lng, size);
		const group = groups.get(key) ?? {
			count: 0,
			latSum: 0,
			lngSum: 0,
			minPrice: null,
			maxPrice: null,
		};
		group.count += row.count;
		group.latSum += row.lat * row.count;
		group.lngSum += row.lng * row.count;
		if (row.minPrice !== null && Number.isFinite(row.minPrice)) {
			group.minPrice =
				group.minPrice === null
					? row.minPrice
					: Math.min(group.minPrice, row.minPrice);
		}
		if (row.maxPrice !== null && Number.isFinite(row.maxPrice)) {
			group.maxPrice =
				group.maxPrice === null
					? row.maxPrice
					: Math.max(group.maxPrice, row.maxPrice);
		}
		groups.set(key, group);
	}

	return Array.from(groups, ([key, group]) => ({
		id: `hex:${precision}:${key}`,
		lat: group.latSum / group.count,
		lng: group.lngSum / group.count,
		count: group.count,
		minPrice: group.minPrice,
		maxPrice: group.maxPrice,
		countLabel: formatCompactCount(group.count),
	})).sort(
		(a, b) => b.count - a.count || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
	);
}

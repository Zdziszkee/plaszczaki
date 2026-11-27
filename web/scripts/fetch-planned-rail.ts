#!/usr/bin/env bun
/**
 * Regenerate `web/public/data/planned-rail.geojson` from OpenStreetMap.
 *
 * The map loads the committed snapshot at runtime; this script only has to run
 * when the data should be refreshed. It keeps heavy-rail lines tagged
 * `railway=construction` or `railway=proposed` inside the Śląskie (PL-24) and
 * Małopolskie (PL-12) voivodeship areas.
 *
 * Usage: bun run data:planned-rail
 */

import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";

import { matchKolejPlusProject } from "./kolej-plus-projects";
import { isRail } from "./rail";

const OUTPUT_FILE = join(
	import.meta.dir,
	"..",
	"public",
	"data",
	"planned-rail.geojson",
);
const VERSION_FILE = join(
	import.meta.dir,
	"..",
	"src",
	"components",
	"map",
	"planned-rail-snapshot.ts",
);

/** Tried in order; public Overpass instances are often busy. */
const OVERPASS_ENDPOINTS = [
	"https://overpass.openstreetmap.fr/api/interpreter",
	"https://overpass-api.de/api/interpreter",
	"https://overpass.kumi.systems/api/interpreter",
	"https://overpass.private.coffee/api/interpreter",
];

const OVERPASS_QUERY = `[out:json][timeout:240];
area["ISO3166-2"="PL-24"]->.slaskie;
area["ISO3166-2"="PL-12"]->.malopolskie;
(
  way["railway"~"^(construction|proposed)$"](area.slaskie);
  way["railway"~"^(construction|proposed)$"](area.malopolskie);
);
out geom tags;`;

type OsmGeometryPoint = { lat: number; lon: number };

type OsmWay = {
	type: "way";
	id: number;
	tags?: Record<string, string>;
	geometry?: OsmGeometryPoint[];
};

type OverpassResponse = {
	elements?: OsmWay[];
};

type PlannedRailFeature = {
	type: "Feature";
	id: number;
	properties: {
		status: "construction" | "proposed";
		name: string | null;
		ref: string | null;
		usage: string | null;
		maxspeed: string | null;
		operator: string | null;
		/** Strategic programme marker, e.g. `port-polska` or `kolej-plus`. */
		program: string | null;
		/** Curated public-facing project title when OSM wording is unclear. */
		project: string | null;
		target: string | null;
		stage: string | null;
	};
	geometry: {
		type: "LineString";
		coordinates: Array<[number, number]>;
	};
};

/**
 * Port Polska (formerly CPK) rail projects in the two voivodeships: the
 * Katowice–Ostrawa KDP and its Jastrzębie/Wodzisław connectors.
 *
 * The investment is joint CPK + PKP PLK: CPK owns the southern section, while
 * PLK owns the Katowice–Mikołów approach. OSM maps that approach as unnamed
 * extra tracks on LK 139/140/656, so it is matched by the small corridor box
 * below; otherwise the teal line would stop at Katowice Piotrowice.
 *
 * There is no Port Polska rail project in Małopolska in the current ZSK plan:
 * the former Małopolsko-Śląski hub (including a new Katowice–Kraków line) was
 * dropped in 2026. Podłęże–Piekiełko is a PKP PLK project, not Port Polska.
 */
const PORT_POLSKA_NAME =
	/Katowice\s*-\s*Ostrawa|Wodzisław Śląski - Jastrzębie Zdrój|Bohumin-Wodzisław Śląski/i;

/** PKP PLK KPO project: Podłęże–Piekiełko, lines 622/623/627 and upgrades on 104. */
const PODLEZE_PIEKIELKO_REFS = new Set(["622", "623", "627", "104"]);

const PORT_POLSKA_APPROACH_REFS = new Set(["139", "140", "656"]);
const PORT_POLSKA_APPROACH_BOX = {
	minLon: 18.93,
	maxLon: 18.99,
	minLat: 50.18,
	maxLat: 50.22,
};

function isPortPolska(
	tags: Record<string, string>,
	geometry?: OsmGeometryPoint[],
): boolean {
	const operator = `${tags["operator:short"] ?? ""} ${tags.operator ?? ""}`;
	if (/\bCPK\b|Port Polska/i.test(operator)) return true;
	if (PORT_POLSKA_NAME.test(tags.name ?? "")) return true;

	if (!tags.ref || !PORT_POLSKA_APPROACH_REFS.has(tags.ref) || !geometry) {
		return false;
	}
	return geometry.some(
		(point) =>
			point.lon >= PORT_POLSKA_APPROACH_BOX.minLon &&
			point.lon <= PORT_POLSKA_APPROACH_BOX.maxLon &&
			point.lat >= PORT_POLSKA_APPROACH_BOX.minLat &&
			point.lat <= PORT_POLSKA_APPROACH_BOX.maxLat,
	);
}

function curatedProject(
	tags: Record<string, string>,
	geometry: OsmGeometryPoint[],
) {
	const project = matchKolejPlusProject(tags, geometry);
	if (project) {
		return {
			program: "kolej-plus",
			project: project.name,
			target: project.target,
			stage: project.stage,
		};
	}
	if (tags.ref && PODLEZE_PIEKIELKO_REFS.has(tags.ref)) {
		return {
			program: "pkp-plk",
			project: "Podłęże–Piekiełko",
			target: null,
			stage: "w budowie",
		};
	}
	return null;
}

/** Round to ~1 m and drop consecutive duplicate nodes. */
function cleanCoordinates(points: OsmGeometryPoint[]): Array<[number, number]> {
	const coordinates: Array<[number, number]> = [];
	for (const point of points) {
		const lng = Number(point.lon.toFixed(5));
		const lat = Number(point.lat.toFixed(5));
		const previous = coordinates[coordinates.length - 1];
		if (previous && previous[0] === lng && previous[1] === lat) continue;
		coordinates.push([lng, lat]);
	}
	return coordinates;
}

async function fetchOverpass(): Promise<OverpassResponse> {
	let lastError: unknown;
	for (const endpoint of OVERPASS_ENDPOINTS) {
		try {
			console.log(`Pobieranie OSM: ${endpoint}`);
			const response = await fetch(endpoint, {
				method: "POST",
				headers: {
					"content-type": "application/x-www-form-urlencoded",
					"user-agent": "plaszczaki-map-planned-rail/1.0",
				},
				body: new URLSearchParams({ data: OVERPASS_QUERY }),
				signal: AbortSignal.timeout(240_000),
			});
			if (!response.ok) {
				throw new Error(`HTTP ${response.status} ${response.statusText}`);
			}
			return (await response.json()) as OverpassResponse;
		} catch (error) {
			lastError = error;
			console.warn(
				`Nie udało się pobrać z ${endpoint}: ${
					error instanceof Error ? error.message : String(error)
				}`,
			);
		}
	}
	throw new Error(
		`Nie udało się pobrać danych OSM: ${
			lastError instanceof Error ? lastError.message : String(lastError)
		}`,
	);
}

const payload = await fetchOverpass();
const elements = payload.elements ?? [];
console.log(`Otrzymano ${elements.length} dróg OSM.`);

const features: PlannedRailFeature[] = [];
let skipped = 0;

for (const way of elements) {
	const tags = way.tags ?? {};
	const status = tags.railway;
	if (
		!isRail(tags) ||
		(status !== "construction" && status !== "proposed") ||
		tags.usage === "industrial" ||
		!way.geometry
	) {
		skipped += 1;
		continue;
	}

	const coordinates = cleanCoordinates(way.geometry);
	if (coordinates.length < 2) {
		skipped += 1;
		continue;
	}

	const curated = curatedProject(tags, way.geometry);
	features.push({
		type: "Feature",
		id: way.id,
		properties: {
			status,
			name: tags.name ?? null,
			ref: tags.ref ?? null,
			usage: tags.usage ?? null,
			maxspeed: tags.maxspeed ?? null,
			operator: tags["operator:short"] ?? tags.operator ?? null,
			program: isPortPolska(tags, way.geometry)
				? "port-polska"
				: (curated?.program ?? null),
			project: curated?.project ?? null,
			target: curated?.target ?? null,
			stage: curated?.stage ?? null,
		},
		geometry: { type: "LineString", coordinates },
	});
}

features.sort((a, b) => a.id - b.id);

const statusCounts = features.reduce<Record<string, number>>((counts, feature) => {
	counts[feature.properties.status] =
		(counts[feature.properties.status] ?? 0) + 1;
	return counts;
}, {});
const portPolskaCount = features.filter(
	(feature) => feature.properties.program === "port-polska",
).length;

const generatedAt = new Date().toISOString();
const collection = {
	type: "FeatureCollection",
	metadata: {
		source: "OpenStreetMap contributors",
		sourceUrl: "https://www.openstreetmap.org",
		license: "ODbL-1.0",
		licenseUrl: "https://www.openstreetmap.org/copyright",
		generatedAt,
		voivodeships: ["PL-24", "PL-12"],
		query: "railway=construction|proposed",
		note: "Migawka danych OSM; nie jest to oficjalny harmonogram inwestycji. program=port-polska oznacza inwestycje Port Polska (CPK).",
	},
	features,
};

await mkdir(dirname(OUTPUT_FILE), { recursive: true });
await mkdir(dirname(VERSION_FILE), { recursive: true });
await Bun.write(OUTPUT_FILE, JSON.stringify(collection));
// Changing the URL invalidates browser caches that still hold an older snapshot.
await Bun.write(
	VERSION_FILE,
	`/** Generated by \`bun run data:planned-rail\`. Do not edit. */\nexport const PLANNED_RAIL_SNAPSHOT_VERSION = ${JSON.stringify(generatedAt)};\n`,
);

console.log(
	`Zapisano ${features.length} odcinków (${statusCounts.construction ?? 0} w budowie, ${
		statusCounts.proposed ?? 0
	} planowanych, ${portPolskaCount} Port Polska), pominięto ${skipped}.`,
);
console.log(`Plik: ${OUTPUT_FILE}`);

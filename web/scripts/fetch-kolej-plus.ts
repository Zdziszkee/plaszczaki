#!/usr/bin/env bun
/**
 * Regenerate `web/public/data/kolej-plus.geojson`.
 *
 * Kolej Plus projects mix new lines with modernisations of existing tracks, so
 * this snapshot includes both existing rail and the planned/under-construction
 * sections. Only projects from the current PKP PLK Kolej Plus list that are in
 * Śląskie or Małopolskie are included. Port Polska (Katowice–Ostrawa, LK 140)
 * is drawn by the separate planned-rail layer and is intentionally skipped.
 *
 * Usage: bun run data:kolej-plus
 */

import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";

import {
	KOLEJ_PLUS_PROJECTS,
	matchKolejPlusProject,
} from "./kolej-plus-projects";
import { isRail } from "./rail";

const OUTPUT_FILE = join(
	import.meta.dir,
	"..",
	"public",
	"data",
	"kolej-plus.geojson",
);

const OVERPASS_ENDPOINTS = [
	"https://overpass.openstreetmap.fr/api/interpreter",
	"https://overpass-api.de/api/interpreter",
	"https://overpass.kumi.systems/api/interpreter",
	"https://overpass.private.coffee/api/interpreter",
];

const OVERPASS_QUERY = `[out:json][timeout:180];
area["ISO3166-2"="PL-24"]->.slaskie;
area["ISO3166-2"="PL-12"]->.malopolskie;
(
  way["railway"~"^(rail|disused|construction|proposed)$"]["ref"~"^(132|141|142|162|164|177|189|190|198|294)$"](area.slaskie);
  way["railway"~"^(rail|disused|construction|proposed)$"]["ref"~"^(132|141|142|162|164|177|189|190|198|294)$"](area.malopolskie);
  way["railway"~"^(rail|disused|construction|proposed)$"]["name"~"Kraków-Myślenice|Kuźnica - Bytom|Borowa Górka",i](area.slaskie);
  way["railway"~"^(rail|disused|construction|proposed)$"]["name"~"Kraków-Myślenice|Kuźnica - Bytom|Borowa Górka",i](area.malopolskie);
);
out geom tags;`;

type OsmGeometryPoint = { lat: number; lon: number };

type OsmWay = {
	type: "way";
	id: number;
	tags?: Record<string, string>;
	geometry?: OsmGeometryPoint[];
};

type OverpassResponse = { elements?: OsmWay[] };

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
					"user-agent": "plaszczaki-map-kolej-plus/1.0",
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

const features: Array<{
	type: "Feature";
	id: string;
	properties: {
		program: "kolej-plus";
		project: string;
		target: string | null;
		stage: string | null;
		status: "existing" | "construction" | "proposed" | "schematic";
		ref: string | null;
		name: string | null;
		operator: string | null;
	};
	geometry:
		| { type: "LineString"; coordinates: Array<[number, number]> }
		| { type: "Point"; coordinates: [number, number] };
}> = [];

let skipped = 0;
for (const way of elements) {
	const tags = way.tags ?? {};
	if (!isRail(tags) || !way.geometry) {
		skipped += 1;
		continue;
	}
	const project = matchKolejPlusProject(tags, way.geometry);
	if (!project) {
		skipped += 1;
		continue;
	}
	const coordinates = cleanCoordinates(way.geometry);
	if (coordinates.length < 2) {
		skipped += 1;
		continue;
	}

	const status =
		tags.railway === "construction"
			? "construction"
			: tags.railway === "proposed"
				? "proposed"
				: "existing";

	features.push({
		type: "Feature",
		id: `${project.id}-${way.id}`,
		properties: {
			program: "kolej-plus",
			project: project.name,
			target: project.target,
			stage: project.stage,
			status,
			ref: tags.ref ?? null,
			name: tags.name ?? null,
			operator: tags["operator:short"] ?? tags.operator ?? null,
		},
		geometry: { type: "LineString", coordinates },
	});
}

// Projects that have no mapped public alignment yet stay visible as one
// clearly-marked schematic point instead of an invented line.
const representedProjects = new Set(
	features.map((feature) => feature.properties.project),
);
for (const project of KOLEJ_PLUS_PROJECTS) {
	if (!project.schematicPoint || representedProjects.has(project.name)) continue;
	features.push({
		type: "Feature",
		id: `${project.id}-schematic`,
		properties: {
			program: "kolej-plus",
			project: project.name,
			target: project.target,
			stage: project.stage,
			status: "schematic",
			ref: null,
			name: null,
			operator: null,
		},
		geometry: { type: "Point", coordinates: project.schematicPoint },
	});
}

features.sort((a, b) => a.id.localeCompare(b.id));

const projectCounts = features.reduce<Record<string, number>>((counts, feature) => {
	counts[feature.properties.project] =
		(counts[feature.properties.project] ?? 0) + 1;
	return counts;
}, {});

const collection = {
	type: "FeatureCollection",
	metadata: {
		source: "OpenStreetMap contributors",
		sourceUrl: "https://www.openstreetmap.org",
		license: "ODbL-1.0",
		licenseUrl: "https://www.openstreetmap.org/copyright",
		generatedAt: new Date().toISOString(),
		program: "Kolej Plus",
		note: "Migawka OSM; projekty Kolej Plus łączą modernizacje istniejących linii z nowymi odcinkami. Nie jest to oficjalny harmonogram.",
	},
	features,
};

await mkdir(dirname(OUTPUT_FILE), { recursive: true });
await Bun.write(OUTPUT_FILE, JSON.stringify(collection));

console.log(
	`Zapisano ${features.length} odcinków Kolej Plus, pominięto ${skipped}.`,
);
for (const [project, count] of Object.entries(projectCounts).sort(
	(a, b) => b[1] - a[1],
)) {
	console.log(`  ${count}× ${project}`);
}
console.log(`Plik: ${OUTPUT_FILE}`);

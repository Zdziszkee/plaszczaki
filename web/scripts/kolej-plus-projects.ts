/**
 * Shared Kolej Plus project definitions for the map data scripts.
 *
 * Boxes are approximate and only used to assign an OSM way to the right
 * current PKP PLK project; they are not published as project boundaries.
 */

export type KolejPlusBounds = {
	minLon: number;
	minLat: number;
	maxLon: number;
	maxLat: number;
};

export type KolejPlusProject = {
	id: string;
	name: string;
	refs: string[];
	bbox: KolejPlusBounds;
	target: string | null;
	stage: string | null;
	namePattern?: RegExp;
	/** Approximate marker used when no public alignment exists yet. */
	schematicPoint?: [number, number];
};

export const KOLEJ_PLUS_PROJECTS: KolejPlusProject[] = [
	{
		id: "krakow-myslenice",
		name: "Kraków–Myślenice",
		refs: [],
		bbox: { minLon: 19.8, minLat: 49.8, maxLon: 20.02, maxLat: 50.08 },
		target: null,
		stage: "projekt",
		namePattern: /Kraków\s*-\s*Myślenice/i,
	},
	{
		id: "krakow-olkusz",
		name: "Łącznica Olkusz–Kraków (Borowa Górka–Pieczyska)",
		refs: [],
		bbox: { minLon: 19.28, minLat: 50.22, maxLon: 19.38, maxLat: 50.27 },
		target: "2028–2030",
		stage: "decyzja środowiskowa",
		namePattern: /Borowa Górka/i,
	},
	{
		id: "krakow-niepolomice",
		name: "Kraków–Niepołomice",
		refs: [],
		bbox: { minLon: 19.94, minLat: 49.94, maxLon: 20.1, maxLat: 50.02 },
		target: null,
		stage: "projekt",
		namePattern: /Niepołomice/i,
		schematicPoint: [20.188, 50.037],
	},
	{
		id: "lk198-pyskowice",
		name: "LK 198 Pyskowice–Pyskowice Miasto",
		refs: ["198"],
		bbox: { minLon: 18.58, minLat: 50.37, maxLon: 18.66, maxLat: 50.41 },
		target: null,
		stage: "projekt",
		schematicPoint: [18.62, 50.394],
	},
	{
		id: "lk189-132-kuznica",
		name: "Kuźnica–Bytom Bobrek Wsch (LK 189/132)",
		refs: ["189", "132"],
		bbox: { minLon: 18.82, minLat: 50.28, maxLon: 18.96, maxLat: 50.38 },
		target: null,
		stage: "projekt",
		namePattern: /Kuźnica/i,
	},
	{
		id: "lk141-164-gliwice",
		name: "Gliwice–Ruda Kochłowice–Katowice (LK 141/164)",
		refs: ["141", "164"],
		bbox: { minLon: 18.62, minLat: 50.24, maxLon: 19.0, maxLat: 50.33 },
		target: null,
		stage: "projekt",
	},
	{
		id: "lk142-tychy",
		name: "Tychy–Katowice Ligota (LK 142)",
		refs: ["142"],
		bbox: { minLon: 18.94, minLat: 50.08, maxLon: 19.06, maxLat: 50.24 },
		target: null,
		stage: "projekt",
	},
	{
		id: "lk162-dg",
		name: "LK 162 DG Strzemieszyce–Huta Katowice",
		refs: ["162"],
		bbox: { minLon: 19.15, minLat: 50.25, maxLon: 19.36, maxLat: 50.36 },
		target: null,
		stage: "projekt",
	},
	{
		id: "lk190-skoczow",
		name: "LK 190 Skoczów–Bielsko-Biała",
		refs: ["190"],
		bbox: { minLon: 18.72, minLat: 49.76, maxLon: 19.08, maxLat: 49.86 },
		target: null,
		stage: "projekt",
	},
	{
		id: "lk177-294-raciborz",
		name: "LK 177/294 Racibórz–Racławice Śląskie",
		refs: ["177", "294"],
		bbox: { minLon: 17.95, minLat: 49.95, maxLon: 18.45, maxLat: 50.16 },
		target: null,
		stage: "projekt",
	},
];

type LatLon = { lat: number; lon: number };

function inBox(points: LatLon[], box: KolejPlusBounds): boolean {
	return points.some(
		(point) =>
			point.lon >= box.minLon &&
			point.lon <= box.maxLon &&
			point.lat >= box.minLat &&
			point.lat <= box.maxLat,
	);
}

export function matchKolejPlusProject(
	tags: Record<string, string>,
	geometry: LatLon[],
): KolejPlusProject | null {
	for (const project of KOLEJ_PLUS_PROJECTS) {
		if (project.namePattern?.test(tags.name ?? "")) return project;
		if (tags.ref && project.refs.includes(tags.ref) && inBox(geometry, project.bbox)) {
			return project;
		}
	}
	return null;
}

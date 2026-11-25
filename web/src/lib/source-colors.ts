/**
 * Client-safe source identity colors.
 *
 * One shared palette keeps the map pins, the filter selects and the legend in
 * sync: the server routes and the React components both resolve colors here.
 * Hues are chosen to stay legible on the night basemap and to avoid colliding
 * with the rail/traffic layer colors.
 */

const SOURCE_COLORS = {
	otodom: "#f87171",
	olx: "#fb923c",
	gratka: "#fbbf24",
	morizon: "#a3e635",
	"nieruchomosci-online": "#4ade80",
	domiporta: "#34d399",
	domy: "#2dd4bf",
	adresowo: "#22d3ee",
	gethome: "#38bdf8",
	homeportal: "#60a5fa",
	szybko: "#818cf8",
	flats: "#a78bfa",
	axiimmo: "#c084fc",
	nportal: "#e879f9",
	luksusowemieszkania: "#f472b6",
	rentola: "#fb7185",
	otoprzetargi: "#fda4af",
	alertyprzetargowe: "#fca5a5",
	syndykaukcje: "#fdba74",
	ogloszeniasyndyka: "#fcd34d",
	noweinwestycje: "#bef264",
	rynekpierwotny: "#6ee7b7",
	wybierzdewelopera: "#5eead4",
	sprzedajemy: "#67e8f9",
	sprzedambiznes: "#7dd3fc",
	tabelaofert: "#93c5fd",
	"oferty-net": "#a5b4fc",
	kupbezposrednio: "#c4b5fd",
	okolica: "#d8b4fe",
	rentumo: "#f0abfc",
	partnersinternational: "#f9a8d4",
	"pkobp-nieruchomosci": "#fecdd3",
	pkp: "#fed7aa",
	"poczta-polska": "#fef08a",
	tauron: "#d9f99d",
	"amw-nieruchomosci": "#bbf7d0",
	terenyprzemyslowe: "#99f6e4",
	"magazyny-online": "#a5f3fc",
	szybkokomercyjne: "#bae6fd",
	txo: "#c7d2fe",
	vilea: "#ddd6fe",
	nieruchomosciowi: "#e9d5ff",
} as const satisfies Record<string, string>;

/** Neutral color for a source outside the curated palette. */
export const SOURCE_FALLBACK_COLOR = "#94a3b8";

function hashString(value: string): number {
	let hash = 0;
	for (let index = 0; index < value.length; index += 1) {
		hash = (hash * 31 + value.charCodeAt(index)) % 360;
	}
	return hash;
}

/**
 * Color for a source id. Curated ids get a hand-picked hue; anything else
 * (a new portal added to `sources.json`) gets a stable, deterministic hue so
 * the same source keeps the same color across reloads and between the map,
 * the select and the legend.
 */
export function sourceColor(sourceId: string): string {
	const known = (SOURCE_COLORS as Record<string, string>)[sourceId];
	if (known) return known;
	return `hsl(${hashString(sourceId)} 72% 66%)`;
}

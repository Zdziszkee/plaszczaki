export const PROPERTY_LABELS: Record<string, string> = {
	flat: "mieszkanie",
	house: "dom",
	plot: "działka",
	commercial: "lokal",
	garage: "garaż",
	room: "pokój",
};

export const OFFER_LABELS: Record<string, string> = {
	sale: "sprzedaż",
	rent: "wynajem",
	auction: "aukcja",
	lease: "dzierżawa",
};

export const MARKET_LABELS: Record<string, string> = {
	primary: "rynek pierwotny",
	secondary: "rynek wtórny",
};

export const CONDITION_LABELS: Record<string, string> = {
	to_finish: "do wykończenia",
	move_in_ready: "do zamieszkania",
	to_renovate: "do remontu",
	to_refresh: "do odświeżenia",
	very_good: "bardzo dobry",
	good: "dobry",
	high_standard: "wysoki standard",
	very_high_standard: "bardzo wysoki standard",
	shell_closed: "surowy zamknięty",
	shell_open: "surowy otwarty",
	developer: "deweloperski",
	standard: "standard",
	idealny: "idealny",
	po_remoncie: "po remoncie",
	wykonczone: "wykonczone",
};

export const BUILDING_TYPE_LABELS: Record<string, string> = {
	block: "blok",
	apartment_building: "apartamentowiec",
	tenement: "kamienica",
	detached_house: "dom wolnostojący",
	semi_detached_house: "dom bliźniaczy",
	row_house: "dom szeregowy",
	office: "biurowiec",
	office_building: "biurowiec",
	warehouse: "magazyn",
	shopping_centre: "centrum handlowe",
	house: "dom",
	private_house: "dom prywatny",
	other: "inny",
};

export const BUILDING_MATERIAL_LABELS: Record<string, string> = {
	brick: "cegła",
	aerated_concrete: "beton komórkowy",
	cellular_concrete: "beton komórkowy",
	prefabricated_concrete: "beton prefabrykowany",
	concrete: "beton",
	reinforced_concrete: "beton zbrojony",
	silicate: "silikat",
	wood: "drewno",
	mixed: "mieszany",
	other: "inny",
};

export const HEATING_LABELS: Record<string, string> = {
	district: "sieciowe",
	gas: "gazowe",
	own_boiler_room: "własna kotłownia",
	electric: "elektryczne",
	heat_pump: "pompa ciepła",
	coal: "węglowe",
	solid_fuel_stove: "piec na paliwo stałe",
	other: "inne",
	brak: "brak",
};

export const WINDOWS_LABELS: Record<string, string> = {
	plastic: "plastikowe",
	wooden: "drewniane",
	wood: "drewniane",
	aluminium: "aluminiowe",
	partially_new: "częściowo nowe",
	new: "nowe",
	brak: "brak",
};

export const OWNERSHIP_LABELS: Record<string, string> = {
	full_ownership: "pełna własność",
	limited_ownership: "ograniczona własność",
	share: "udział",
	usufruct: "użytek wieczysty",
	wieczyste_uzytkowanie: "użytek wieczysty",
	uzytkowanie_wieczyste: "użytek wieczysty",
	spoldzielcze_wlasnosciowe: "spółdzielcze własnościowe",
	spoldzielcze_wlasnosciowe_z_kw: "spółdzielcze własnościowe z KW",
	wlasnosc_z_kw: "własność z KW",
	odrebna_wlasnosc_lokalu: "odrębna własność lokalu",
	odrebna_wlasnosc: "odrębna własność",
	other: "inna",
};

export const PARKING_LABELS: Record<string, string> = {
	none: "brak",
	street: "ulica",
	garage: "garaż",
	underground: "podziemne",
	surface: "naziemne",
	guarded: "strzeżone",
	parking: "parking",
};

export const SECURITY_LABELS: Record<string, string> = {
	alarm: "alarm",
	gated_area: "ogrodzony teren",
	intercom: "domofon",
	monitoring: "monitoring",
	security_doors: "drzwi ochronne",
	security_guard: "ochrona",
	security_shutters: "role ochronne",
	"24_7": "całodobowa ochrona",
};

export function stringValue(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const text = value.trim();
	return text || null;
}

export function formatEnum(
	value: string | null | undefined,
	labels: Record<string, string>,
): string | null {
	const text = stringValue(value);
	if (!text || text === "unknown" || text === "unspecified") return null;
	return labels[text] ?? text.replaceAll("_", " ");
}

export function formatTokenList(
	value: string | null | undefined,
	labels: Record<string, string>,
): string | null {
	const text = stringValue(value);
	if (!text || text === "unknown" || text === "unspecified") return null;
	const uniqueTokens = [
		...new Set(
			text
				.split("+")
				.map((token) => token.trim())
				.filter(Boolean),
		),
	];
	const tokens =
		uniqueTokens.length > 1
			? uniqueTokens.filter((token) => token !== "none")
			: uniqueTokens;
	return tokens
		.map((token) => labels[token] ?? token.replaceAll("_", " "))
		.join(", ");
}

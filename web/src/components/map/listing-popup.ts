import type { ListingDetails, ListingScoreReason } from "#/lib/listing-details";
import { SCORE_PROFILE_LABELS } from "#/lib/ranking-spec";
import type { RcnComparables } from "#/lib/rcn-types";
import { sourceColor } from "#/lib/source-colors";

export type ListingRcnState =
	| { status: "loading" }
	| { status: "error" }
	| { status: "ready"; data: RcnComparables };

export type ListingDetailsState =
	| { status: "loading" }
	| { status: "error" }
	| { status: "ready"; details: ListingDetails; rcn?: ListingRcnState };

type ListingAttribute = {
	label: string;
	value: string;
};

const HTML_ESCAPES: Record<string, string> = {
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	'"': "&quot;",
	"'": "&#39;",
};

export function escapeHtml(value: unknown): string {
	return String(value ?? "").replace(
		/[&<>"']/g,
		(character) => HTML_ESCAPES[character] ?? character,
	);
}

const PROPERTY_LABELS: Record<string, string> = {
	flat: "mieszkanie",
	house: "dom",
	plot: "działka",
	commercial: "lokal",
	garage: "garaż",
	room: "pokój",
};

const OFFER_LABELS: Record<string, string> = {
	sale: "sprzedaż",
	rent: "wynajem",
	auction: "aukcja",
	lease: "dzierżawa",
};

const MARKET_LABELS: Record<string, string> = {
	primary: "rynek pierwotny",
	secondary: "rynek wtórny",
};

const CONDITION_LABELS: Record<string, string> = {
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

const BUILDING_TYPE_LABELS: Record<string, string> = {
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

const BUILDING_MATERIAL_LABELS: Record<string, string> = {
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

const HEATING_LABELS: Record<string, string> = {
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

const WINDOWS_LABELS: Record<string, string> = {
	plastic: "plastikowe",
	wooden: "drewniane",
	wood: "drewniane",
	aluminium: "aluminiowe",
	partially_new: "częściowo nowe",
	new: "nowe",
	brak: "brak",
};

const OWNERSHIP_LABELS: Record<string, string> = {
	full_ownership: "pełna własność",
	limited_ownership: "ograniczona własność",
	share: "udział",
	usufruct: "użytek wieczysty",
	wieczyste_uzytkowanie: "użytek wieczysty",
	uzytkowanie_wieczyste: "użytek wieczysty",
	spoldzielcze_wlasnosciowe: "spółdzielcze własnościowe",
	spoldzielcze_wlasnosciowe_z_kw: "spółdzielcze z KW",
	wlasnosc_z_kw: "własność z KW",
	odrebna_wlasnosc_lokalu: "odrębna własność lokalu",
	odrebna_wlasnosc: "odrębna własność",
	other: "inna",
};

const PARKING_LABELS: Record<string, string> = {
	none: "brak",
	street: "ulica",
	garage: "garaż",
	underground: "podziemne",
	surface: "naziemne",
	guarded: "strzeżone",
	parking: "parking",
};

const SECURITY_LABELS: Record<string, string> = {
	alarm: "alarm",
	gated_area: "ogrodzony teren",
	intercom: "domofon",
	monitoring: "monitoring",
	security_doors: "drzwi ochronne",
	security_guard: "ochrona",
	security_shutters: "role ochronne",
	"24_7": "całodobowa ochrona",
};

const currencyFormatter = new Intl.NumberFormat("pl-PL", {
	style: "currency",
	currency: "PLN",
	maximumFractionDigits: 0,
});

const numberFormatter = new Intl.NumberFormat("pl-PL", {
	maximumFractionDigits: 2,
});

export function formatPln(value: number): string {
	return currencyFormatter.format(value);
}

function stringValue(value: unknown): string | null {
	if (typeof value !== "string") return null;
	const text = value.trim();
	return text || null;
}

function numberValue(value: unknown): number | null {
	return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function safeHttpUrl(value: unknown): string | null {
	if (typeof value !== "string" || !value.trim()) return null;
	try {
		const url = new URL(value);
		return url.protocol === "http:" || url.protocol === "https:"
			? url.href
			: null;
	} catch {
		return null;
	}
}

function safeColor(value: unknown, fallback: string): string {
	return typeof value === "string" && /^#[\da-f]{6}$/i.test(value)
		? value
		: fallback;
}

function formatArea(value: number | null): string | null {
	return value === null ? null : `${numberFormatter.format(value)} m²`;
}

function formatBoolean(value: boolean | null): string | null {
	return value === null ? null : value ? "Tak" : "Nie";
}

function formatFloor(listing: ListingDetails): string | null {
	const label = stringValue(listing.floorLabel);
	if (label) {
		const numbered = /^(?:floor_)?(\d+)$/i.exec(label);
		if (numbered) {
			const floor = Number(numbered[1]);
			return floor === 0 ? "parter" : `${floor}. piętro`;
		}
		if (["ground_floor", "parter"].includes(label.toLowerCase())) {
			return "parter";
		}
		return label;
	}
	if (listing.floor === null) return null;
	if (listing.floor === 0) return "parter";
	if (listing.floor < 0) return `${Math.abs(listing.floor)}. piętro podziemne`;
	return `${listing.floor}. piętro`;
}

function formatDate(value: string | null, withTime = false): string | null {
	if (!value) return null;
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return null;
	return new Intl.DateTimeFormat("pl-PL", {
		dateStyle: "medium",
		...(withTime ? { timeStyle: "short" as const } : {}),
	}).format(date);
}

function formatEnum(
	value: string | null,
	labels: Record<string, string>,
): string | null {
	const text = stringValue(value);
	if (!text || text === "unknown" || text === "unspecified") return null;
	return labels[text] ?? text.replaceAll("_", " ");
}

function formatTokenList(
	value: string | null,
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

function formatOutdoor(listing: ListingDetails): string | null {
	const features = [
		listing.balcony === true ? "balkon" : null,
		listing.terrace === true ? "taras" : null,
	].filter((feature): feature is string => feature !== null);
	if (features.length > 0) return features.join(" i ");
	return listing.balcony === false && listing.terrace === false ? "brak" : null;
}

function withListingDetails(
	properties: Record<string, unknown>,
	details: ListingDetails,
): Record<string, unknown> {
	const merged = { ...properties };
	const scoringKeys = new Set([
		"score",
		"scoreProfile",
		"scores",
		"scoreReasons",
		"scoreBreakdown",
		"scoreVersion",
	]);
	for (const [key, value] of Object.entries(details)) {
		if (
			!scoringKeys.has(key) &&
			value !== null &&
			value !== undefined &&
			value !== ""
		) {
			merged[key] = value;
		}
	}
	const image = details.images.map(safeHttpUrl).find((url) => url !== null);
	if (image) merged.image = image;
	return merged;
}

function topListingAttributes(listing: ListingDetails): ListingAttribute[] {
	const attributes: ListingAttribute[] = [];
	const seen = new Set<string>();
	const add = (label: string, value: string | null) => {
		const text = value?.trim();
		if (
			attributes.length >= 10 ||
			!text ||
			text === "unknown" ||
			seen.has(label)
		) {
			return;
		}
		seen.add(label);
		attributes.push({ label, value: text });
	};

	const propertyType = listing.propertyType;
	const residential = ["flat", "house", "room"].includes(propertyType);

	if (propertyType === "plot") {
		add(
			"Powierzchnia działki",
			formatArea(listing.landAreaM2 ?? listing.areaM2),
		);
	} else {
		add("Powierzchnia użytkowa", formatArea(listing.areaM2));
		if (propertyType === "house") {
			add("Powierzchnia działki", formatArea(listing.landAreaM2));
		}
	}
	if (residential && listing.rooms !== null) {
		add("Liczba pokoi", `${listing.rooms} pok.`);
	}
	if (["flat", "commercial", "room"].includes(propertyType)) {
		add("Piętro", formatFloor(listing));
	}
	if (["flat", "house", "commercial"].includes(propertyType)) {
		add("Rok budowy", listing.buildYear?.toString() ?? null);
	}
	add("Rynek", formatEnum(listing.market, MARKET_LABELS));

	if (["rent", "lease"].includes(listing.offerType)) {
		add(
			"Kaucja",
			listing.depositPln === null ? null : formatPln(listing.depositPln),
		);
		add(
			"Czynsz administracyjny",
			listing.administrativeRentPln === null
				? null
				: formatPln(listing.administrativeRentPln),
		);
		add("Dostępne od", formatDate(listing.availableFrom));
		add("Wyposażone", formatBoolean(listing.furnished));
		add("Zwierzęta", formatBoolean(listing.petsAllowed));
	}
	if (listing.offerType === "auction") {
		add("Data aukcji", formatDate(listing.auctionDate, true));
		add(
			"Wartość szacunkowa",
			listing.estimatePln === null ? null : formatPln(listing.estimatePln),
		);
		add(
			"Runda aukcji",
			listing.auctionRound === null ? null : `${listing.auctionRound}. runda`,
		);
		add("Numer sprawy", listing.caseNumber);
	}

	if (propertyType === "flat") {
		add("Stan", formatEnum(listing.condition, CONDITION_LABELS));
		add("Typ budynku", formatEnum(listing.buildingType, BUILDING_TYPE_LABELS));
		add("Własność", formatEnum(listing.ownership, OWNERSHIP_LABELS));
		add("Ogrzewanie", formatEnum(listing.heating, HEATING_LABELS));
		add("Winda", formatBoolean(listing.elevator));
		add("Parking", formatTokenList(listing.parking, PARKING_LABELS));
		add("Balkon / taras", formatOutdoor(listing));
	} else if (propertyType === "house") {
		add("Stan", formatEnum(listing.condition, CONDITION_LABELS));
		add(
			"Materiał budynku",
			formatEnum(listing.buildingMaterial, BUILDING_MATERIAL_LABELS),
		);
		add("Ogrzewanie", formatEnum(listing.heating, HEATING_LABELS));
		add("Ogród", formatBoolean(listing.garden));
		add("Ogrodzenie", formatBoolean(listing.fenced));
		add("Parking", formatTokenList(listing.parking, PARKING_LABELS));
		add("Balkon / taras", formatOutdoor(listing));
	} else if (propertyType === "plot") {
		add("Własność", formatEnum(listing.ownership, OWNERSHIP_LABELS));
		add("Ogrodzenie", formatBoolean(listing.fenced));
		add("Internet", formatBoolean(listing.internet));
	} else if (propertyType === "commercial") {
		add("Stan", formatEnum(listing.condition, CONDITION_LABELS));
		add("Typ budynku", formatEnum(listing.buildingType, BUILDING_TYPE_LABELS));
		add("Własność", formatEnum(listing.ownership, OWNERSHIP_LABELS));
		add("Ogrzewanie", formatEnum(listing.heating, HEATING_LABELS));
		add("Parking", formatTokenList(listing.parking, PARKING_LABELS));
		add("Bezpieczeństwo", formatTokenList(listing.security, SECURITY_LABELS));
		add("Internet", formatBoolean(listing.internet));
		add("Klimatyzacja", formatBoolean(listing.airConditioning));
		add("Winda", formatBoolean(listing.elevator));
	} else if (propertyType === "garage") {
		add("Stan", formatEnum(listing.condition, CONDITION_LABELS));
		add("Własność", formatEnum(listing.ownership, OWNERSHIP_LABELS));
		add("Ogrzewanie", formatEnum(listing.heating, HEATING_LABELS));
		add(
			"Miejsca parkingowe",
			listing.parkingSpaces === null ? null : `${listing.parkingSpaces} miejsc`,
		);
	} else if (propertyType === "room") {
		add("Stan", formatEnum(listing.condition, CONDITION_LABELS));
		add("Typ budynku", formatEnum(listing.buildingType, BUILDING_TYPE_LABELS));
		add("Wyposażone", formatBoolean(listing.furnished));
		add("Internet", formatBoolean(listing.internet));
		add("Ogrzewanie", formatEnum(listing.heating, HEATING_LABELS));
	}

	add(
		"Liczba pięter",
		listing.buildingFloors === null ? null : `${listing.buildingFloors} pięter`,
	);
	add("Stan", formatEnum(listing.condition, CONDITION_LABELS));
	add("Typ budynku", formatEnum(listing.buildingType, BUILDING_TYPE_LABELS));
	add(
		"Materiał budynku",
		formatEnum(listing.buildingMaterial, BUILDING_MATERIAL_LABELS),
	);
	add("Ogrzewanie", formatEnum(listing.heating, HEATING_LABELS));
	add("Okna", formatEnum(listing.windowsType, WINDOWS_LABELS));
	add("Własność", formatEnum(listing.ownership, OWNERSHIP_LABELS));
	add(
		"Certyfikat energetyczny",
		stringValue(listing.energyCertificate)?.toUpperCase() === "EXEMPT"
			? "zwolniony"
			: (stringValue(listing.energyCertificate)?.toUpperCase() ?? null),
	);
	add(
		"Wysokość sufitu",
		listing.ceilingHeightM === null
			? null
			: `${numberFormatter.format(listing.ceilingHeightM)} m`,
	);
	add("Winda", formatBoolean(listing.elevator));
	add("Parking", formatTokenList(listing.parking, PARKING_LABELS));
	add(
		"Miejsca parkingowe",
		listing.parkingSpaces === null ? null : `${listing.parkingSpaces} miejsc`,
	);
	add("Balkon / taras", formatOutdoor(listing));
	add("Ogród", formatBoolean(listing.garden));
	add("Klimatyzacja", formatBoolean(listing.airConditioning));
	add("Bezpieczeństwo", formatTokenList(listing.security, SECURITY_LABELS));
	add("Internet", formatBoolean(listing.internet));
	add("Ogrodzenie", formatBoolean(listing.fenced));
	add("Sprzedawca", listing.sellerName);
	add("Opublikowano", formatDate(listing.publishedAt));

	return attributes;
}

function attributesHtml(details: ListingDetails): string {
	const attributes = topListingAttributes(details);
	if (attributes.length === 0) {
		return `<p class="listing-popup-muted">Brak dodatkowych atrybutów.</p>`;
	}
	return `<dl class="listing-attribute-grid">${attributes
		.map(
			(attribute) =>
				`<div class="listing-attribute"><dt>${escapeHtml(attribute.label)}</dt><dd>${escapeHtml(attribute.value)}</dd></div>`,
		)
		.join("")}</dl>`;
}

function quickFloorValue(properties: Record<string, unknown>): string | null {
	const label = stringValue(properties.floorLabel);
	if (label) {
		const numbered = /^(?:floor_)?(\d+)$/i.exec(label);
		if (numbered) {
			const floor = Number(numbered[1]);
			return floor === 0 ? "parter" : `${floor}. piętro`;
		}
		if (["ground_floor", "parter"].includes(label.toLowerCase())) {
			return "parter";
		}
		return label;
	}
	const floor = numberValue(properties.floor);
	if (floor === null) return null;
	if (floor === 0) return "parter";
	if (floor < 0) return `${Math.abs(floor)}. piętro podziemne`;
	return `${floor}. piętro`;
}

/** Facts already present on the pin, shown while full details are loading. */
function quickAttributesHtml(properties: Record<string, unknown>): string {
	const candidates: Array<{ label: string; value: string | null }> = [
		{ label: "Piętro", value: quickFloorValue(properties) },
		{
			label: "Typ budynku",
			value: formatEnum(
				stringValue(properties.buildingType),
				BUILDING_TYPE_LABELS,
			),
		},
		{
			label: "Rok budowy",
			value: numberValue(properties.buildYear)?.toString() ?? null,
		},
		{
			label: "Rynek",
			value: formatEnum(stringValue(properties.market), MARKET_LABELS),
		},
		{
			label: "Stan",
			value: formatEnum(stringValue(properties.condition), CONDITION_LABELS),
		},
		{
			label: "Ogrzewanie",
			value: formatEnum(stringValue(properties.heating), HEATING_LABELS),
		},
		{
			label: "Własność",
			value: formatEnum(stringValue(properties.ownership), OWNERSHIP_LABELS),
		},
		{
			label: "Parking",
			value: formatTokenList(stringValue(properties.parking), PARKING_LABELS),
		},
	].filter(
		(attribute): attribute is { label: string; value: string } =>
			Boolean(attribute.value) && attribute.value !== "unknown",
	);
	if (candidates.length === 0) return "";
	return `<dl class="listing-attribute-grid">${candidates
		.slice(0, 8)
		.map(
			(attribute) =>
				`<div class="listing-attribute"><dt>${escapeHtml(attribute.label)}</dt><dd>${escapeHtml(attribute.value)}</dd></div>`,
		)
		.join("")}</dl>`;
}

function rcnComparablesHtml(state: ListingRcnState | undefined): string {
	if (!state) {
		return `<section class="listing-popup-section" aria-labelledby="listing-rcn-title"><h3 id="listing-rcn-title" class="listing-popup-section-title">Porównanie RCN</h3><button type="button" class="listing-popup-action" data-listing-rcn>Porównaj z RCN</button></section>`;
	}
	if (state.status === "loading") {
		return `<section class="listing-popup-section"><h3 class="listing-popup-section-title">Porównanie RCN</h3><div class="listing-popup-loading" role="status"><span class="listing-popup-spinner" aria-hidden="true"></span>Szukam porównywalnych transakcji…</div></section>`;
	}
	if (state.status === "error") {
		return `<section class="listing-popup-section"><h3 class="listing-popup-section-title">Porównanie RCN</h3><p class="listing-popup-muted">Nie udało się pobrać transakcji RCN.</p></section>`;
	}
	const data = state.data;
	if (data.status === "unavailable") {
		return `<section class="listing-popup-section"><h3 class="listing-popup-section-title">Porównanie RCN</h3><p class="listing-popup-muted">Brak wystarczającej liczby porównywalnych transakcji w okolicy.</p></section>`;
	}
	const median = data.weightedPricePerM2Pln ?? data.medianPricePerM2Pln;
	const low = data.p25PricePerM2Pln;
	const high = data.p75PricePerM2Pln;
	const range =
		low !== null && high !== null
			? `${formatPln(low)} – ${formatPln(high)}/m²`
			: "brak przedziału";
	const samples = data.samples
		.map(
			(sample) =>
				`<li><span>${formatPln(sample.pricePerM2Pln)}/m²</span> · ${numberFormatter.format(sample.areaM2)} m² · ${formatDate(sample.documentDate)} · ${sample.distanceKm.toFixed(1)} km</li>`,
		)
		.join("");
	const imported = data.importedAt ? new Date(data.importedAt) : null;
	const importedLabel =
		imported && !Number.isNaN(imported.getTime())
			? new Intl.DateTimeFormat("pl-PL", { dateStyle: "short" }).format(
					imported,
				)
			: null;
	const sameAddress =
		data.sameAddress.length > 0
			? `<div class="listing-popup-muted">Ten sam adres: ${data.sameAddress.length} wcześniejszych transakcji.</div>`
			: "";
	const confidenceLabels = { high: "wysoka", medium: "średnia", low: "niska" };
	const confidence = data.confidence
		? ` · pewność ${confidenceLabels[data.confidence]}`
		: "";
	return `<section class="listing-popup-section" aria-labelledby="listing-rcn-title">
		<h3 id="listing-rcn-title" class="listing-popup-section-title">Porównanie RCN</h3>
		<div class="listing-popup-rcn-summary"><strong>${median === null ? "—" : `${formatPln(median)}/m²`}</strong><span>mediana ważona · ${data.count} transakcji</span></div>
		<div class="listing-popup-muted">${escapeHtml(range)} · promień ${data.radiusKm} km · ${data.months} mies.${confidence}</div>
		${sameAddress}
		${samples ? `<ul class="listing-popup-rcn-samples">${samples}</ul>` : ""}
		${importedLabel ? `<div class="listing-popup-muted">Import RCN: ${escapeHtml(importedLabel)}</div>` : ""}
		<div class="listing-popup-rcn-source">RCN/GUGiK — ${escapeHtml(data.attribution)}</div>
		<div class="listing-popup-muted">Dane poglądowe, nie wycena. Cena transakcji może obejmować więcej nieruchomości.</div>
	</section>`;
}

function descriptionHtml(details: ListingDetails): string {
	const description = details.descriptionPreview;
	if (!description) {
		return `<section class="listing-popup-section" aria-labelledby="listing-description-title"><h3 id="listing-description-title" class="listing-popup-section-title">Opis</h3><p class="listing-popup-muted">Brak opisu.</p></section>`;
	}
	return `<section class="listing-popup-section" aria-labelledby="listing-description-title"><h3 id="listing-description-title" class="listing-popup-section-title">Opis</h3><p id="listing-description-text" class="listing-description-preview" data-listing-description>${escapeHtml(description)}</p><button type="button" class="listing-description-toggle" data-listing-description-toggle aria-expanded="false" aria-controls="listing-description-text">Pokaż więcej</button></section>`;
}

/** Popup content for a scraped property listing. */
export function listingPopupHtml(
	properties: Record<string, unknown>,
	state: ListingDetailsState = { status: "loading" },
): string {
	const summary =
		state.status === "ready"
			? withListingDetails(properties, state.details)
			: properties;
	const title = typeof summary.title === "string" ? summary.title : "Oferta";
	const url = safeHttpUrl(summary.url);
	const source = typeof summary.source === "string" ? summary.source : "";
	const sourceLabel =
		typeof summary.sourceLabel === "string" ? summary.sourceLabel : source;
	const color = safeColor(summary.color, sourceColor(source || "unknown"));
	const priceValue =
		numberValue(summary.pricePln) ?? numberValue(summary.rentPln);
	const price =
		priceValue !== null
			? formatPln(priceValue)
			: summary.priceKind === "call"
				? "Cena do uzgodnienia"
				: "Cena nieznana";
	const pricePerM2 =
		numberValue(summary.pricePerM2Pln) === null
			? ""
			: `${formatPln(numberValue(summary.pricePerM2Pln) ?? 0)}/m²`;

	const details: string[] = [];
	const offerType =
		typeof summary.offerType === "string"
			? (OFFER_LABELS[summary.offerType] ?? summary.offerType)
			: "";
	if (offerType) details.push(offerType);
	if (typeof summary.propertyType === "string") {
		details.push(PROPERTY_LABELS[summary.propertyType] ?? summary.propertyType);
	}
	if (numberValue(summary.rooms) !== null) {
		details.push(`${numberValue(summary.rooms)} pok.`);
	}
	if (numberValue(summary.areaM2) !== null) {
		details.push(
			`${numberFormatter.format(numberValue(summary.areaM2) ?? 0)} m²`,
		);
	}
	const address = stringValue(summary.addressText);
	const location =
		address ??
		[
			stringValue(summary.street),
			stringValue(summary.district),
			stringValue(summary.city),
		]
			.filter((part): part is string => Boolean(part))
			.join(" · ");
	if (location) details.push(location);

	const imageUrl = safeHttpUrl(summary.image);
	const image = imageUrl
		? `<img src="${escapeHtml(imageUrl)}" alt="" class="listing-popup-image" loading="lazy" />`
		: "";
	const link = url
		? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="listing-popup-link">${sourceLabel ? `Otwórz na ${escapeHtml(sourceLabel)}` : "Otwórz ofertę"}</a>`
		: "";
	const sourceLine = sourceLabel
		? `<div class="listing-popup-source"><span class="listing-popup-source-dot" style="background:${escapeHtml(color)}"></span><span>${escapeHtml(sourceLabel)}</span></div>`
		: "";

	const score = numberValue(summary.score);
	const profile =
		typeof summary.scoreProfile === "string"
			? SCORE_PROFILE_LABELS.universal
			: "";
	const rawReasons = Array.isArray(summary.scoreReasons)
		? summary.scoreReasons
		: [];
	const reasons = rawReasons
		.filter((reason): reason is ListingScoreReason =>
			Boolean(
				reason &&
					typeof reason === "object" &&
					typeof reason.label === "string" &&
					typeof reason.points === "number" &&
					reason.points > 0,
			),
		)
		.slice(0, 3);
	const scoreColor =
		score === null
			? "#94a3b8"
			: score >= 80
				? "#22c55e"
				: score >= 60
					? "#84cc16"
					: score >= 40
						? "#f59e0b"
						: "#ef4444";
	const scoreHtml =
		score === null
			? ""
			: `<div class="listing-popup-score"><div><span class="listing-popup-score-value" style="color:${scoreColor}">${score}/100</span>${profile ? `<div class="listing-popup-score-profile">${escapeHtml(profile)}</div>` : ""}</div>${reasons.length ? `<ul class="listing-popup-reasons">${reasons.map((reason) => `<li>${escapeHtml(reason.label)}</li>`).join("")}</ul>` : ""}</div>`;

	const quickAttributes = quickAttributesHtml(summary);
	const quickContent = quickAttributes
		? `<section class="listing-popup-section" aria-labelledby="listing-quick-attributes-title"><h3 id="listing-quick-attributes-title" class="listing-popup-section-title">Najważniejsze cechy</h3>${quickAttributes}</section>`
		: "";
	const detailContent =
		state.status === "ready"
			? `<section class="listing-popup-section" aria-labelledby="listing-attributes-title"><h3 id="listing-attributes-title" class="listing-popup-section-title">Najważniejsze cechy</h3>${attributesHtml(state.details)}</section>${descriptionHtml(state.details)}`
			: state.status === "error"
				? `${quickContent}<div class="listing-popup-error" role="status"><span>Nie udało się pobrać szczegółów.</span><button type="button" class="listing-popup-retry" data-listing-retry>Spróbuj ponownie</button></div>`
				: `${quickContent}<div class="listing-popup-loading" role="status"><span class="listing-popup-spinner" aria-hidden="true"></span>Ładowanie szczegółów…</div>`;
	const rcnContent =
		state.status === "ready" ? rcnComparablesHtml(state.rcn) : "";

	return `<div class="listing-popup">
		${image}
		${sourceLine}
		${scoreHtml}
		<div class="listing-popup-price">${escapeHtml(price)}</div>
		${pricePerM2 ? `<div class="listing-popup-price-per-m2">${escapeHtml(pricePerM2)}</div>` : ""}
		<div class="listing-popup-title">${escapeHtml(title)}</div>
		<div class="listing-popup-summary">${details.map(escapeHtml).join(" · ")}</div>
		${detailContent}
		${rcnContent}
		${link}
	</div>`;
}

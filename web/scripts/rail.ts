const RAIL_SUBTYPES = new Set(["rail", "light_rail", "narrow_gauge"]);
const TRAM_OPERATOR_SHORTS = new Set(["ZDMK"]);
const TRAM_VOLTAGES = new Set(["600", "750"]);

type RailTags = Record<string, string>;

/**
 * Some OSM ways use `railway=construction`/`proposed` without a subtype.
 * Urban tram works are often tagged that way, so use their electrical system
 * and operator to avoid treating them as heavy rail.
 */
function hasTramSignals(tags: RailTags): boolean {
	const voltage = tags.voltage ?? tags["proposed:voltage"];
	return (
		(voltage !== undefined && TRAM_VOLTAGES.has(voltage)) ||
		(tags["operator:short"] !== undefined &&
			TRAM_OPERATOR_SHORTS.has(tags["operator:short"]))
	);
}

export function railSubtype(tags: RailTags): string | null {
	if (tags.railway === "construction") {
		return (
			tags.construction ??
			tags["construction:railway"] ??
			(hasTramSignals(tags) ? "tram" : "rail")
		);
	}
	if (tags.railway === "proposed") {
		return (
			tags.proposed ??
			tags["proposed:railway"] ??
			(hasTramSignals(tags) ? "tram" : "rail")
		);
	}
	return tags.railway ?? null;
}

export function isRail(tags: RailTags): boolean {
	if (tags.railway === "construction" || tags.railway === "proposed") {
		const subtype = railSubtype(tags);
		return subtype !== null && RAIL_SUBTYPES.has(subtype);
	}
	return (
		tags.railway === "rail" ||
		tags.railway === "disused" ||
		tags.railway === "abandoned"
	);
}

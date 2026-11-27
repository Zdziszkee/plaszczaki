export type AggregateMapTheme = {
	background: string;
	ring: string;
};

const FALLBACK_THEME: AggregateMapTheme = {
	background: "#09090b",
	ring: "#52525b",
};

function resolveCssColor(value: string, fallback: string): string {
	if (!value.trim()) return fallback;
	const canvas = document.createElement("canvas");
	canvas.width = 1;
	canvas.height = 1;
	const context = canvas.getContext("2d");
	if (!context) return fallback;
	context.clearRect(0, 0, 1, 1);
	context.fillStyle = value.trim();
	context.fillRect(0, 0, 1, 1);
	const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
	if (red === undefined || green === undefined || blue === undefined) {
		return fallback;
	}
	return `rgba(${red}, ${green}, ${blue}, ${(alpha ?? 255) / 255})`;
}

/** Resolve shadcn tokens to sRGB, which Mapbox's paint properties can consume. */
export function readAggregateMapTheme(): AggregateMapTheme {
	if (typeof document === "undefined") return FALLBACK_THEME;
	const styles = getComputedStyle(document.documentElement);
	return {
		background: resolveCssColor(
			styles.getPropertyValue("--background"),
			FALLBACK_THEME.background,
		),
		ring: resolveCssColor(
			styles.getPropertyValue("--ring"),
			FALLBACK_THEME.ring,
		),
	};
}

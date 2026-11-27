const HTML_ESCAPES: Record<string, string> = {
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	'"': "&quot;",
	"'": "&#39;",
};

function escapeHtml(value: unknown): string {
	return String(value ?? "").replace(
		/[&<>"']/g,
		(character) => HTML_ESCAPES[character] ?? character,
	);
}

function numberValue(value: unknown): number | null {
	return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatPln(value: number): string {
	return new Intl.NumberFormat("pl-PL", {
		style: "currency",
		currency: "PLN",
		maximumFractionDigits: 0,
	}).format(value);
}

function formatDate(value: unknown): string {
	if (typeof value !== "string") return "brak daty";
	const date = new Date(`${value}T00:00:00Z`);
	if (Number.isNaN(date.getTime())) return value;
	return new Intl.DateTimeFormat("pl-PL", {
		day: "numeric",
		month: "short",
		year: "numeric",
		timeZone: "UTC",
	}).format(date);
}

const KIND_LABELS: Record<string, string> = {
	flat: "mieszkanie",
	building: "budynek",
	plot: "działka",
	premises: "lokal",
};

const MARKET_LABELS: Record<string, string> = {
	primary: "rynek pierwotny",
	secondary: "rynek wtórny",
};

export function rcnPopupHtml(properties: Record<string, unknown>): string {
	const count = numberValue(properties.count);
	if (count !== null) {
		const minPrice = numberValue(properties.minPrice);
		const maxPrice = numberValue(properties.maxPrice);
		const range =
			minPrice === null
				? "Brak cen"
				: minPrice === maxPrice
					? formatPln(minPrice)
					: `${formatPln(minPrice)} – ${formatPln(maxPrice ?? minPrice)}`;
		return `<div style="font:12px/1.45 system-ui,sans-serif;color:var(--popover-foreground);max-width:240px">
			<div style="font-size:16px;font-weight:750">${count.toLocaleString("pl-PL")} transakcji RCN</div>
			<div style="color:var(--muted-foreground);margin:4px 0 8px">${escapeHtml(range)}</div>
			<button type="button" data-rcn-zoom style="width:100%;cursor:pointer;border:1px solid var(--border);border-radius:6px;background:var(--secondary);color:var(--secondary-foreground);padding:5px 8px;font:inherit;font-weight:600">Przybliż</button>
		</div>`;
	}
	const price = numberValue(properties.pricePln);
	const ppm = numberValue(properties.pricePerM2Pln);
	const area = numberValue(properties.areaM2);
	const kind =
		typeof properties.objectKind === "string" ? properties.objectKind : "";
	const market = typeof properties.market === "string" ? properties.market : "";
	const address =
		typeof properties.addressText === "string"
			? properties.addressText
			: typeof properties.city === "string"
				? properties.city
				: "";
	const details = [
		KIND_LABELS[kind] ?? kind,
		MARKET_LABELS[market] ?? market,
		area === null ? null : `${area.toLocaleString("pl-PL")} m²`,
		numberValue(properties.rooms) === null
			? null
			: `${numberValue(properties.rooms)} pok.`,
	]
		.filter(Boolean)
		.join(" · ");
	return `<div style="font:12px/1.45 system-ui,sans-serif;color:var(--popover-foreground);max-width:280px">
		<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#f59e0b">Transakcja RCN</div>
		<div style="font-size:16px;font-weight:750">${price === null ? "Cena nieprzypisana" : formatPln(price)}</div>
		${ppm === null ? "" : `<div style="color:var(--muted-foreground)">${formatPln(ppm)}/m²</div>`}
		<div style="font-weight:600;margin-top:5px">${escapeHtml(address || "Brak adresu")}</div>
		<div style="color:var(--muted-foreground)">${escapeHtml(details)}</div>
		<div style="color:var(--muted-foreground);margin-top:4px">Data aktu: ${escapeHtml(formatDate(properties.documentDate))}</div>
		<div style="font-size:9px;line-height:1.4;color:var(--muted-foreground);margin-top:7px">RCN/GUGiK — Wykorzystano/opracowano na podstawie materiałów państwowego zasobu geodezyjnego i kartograficznego. Cena transakcji może obejmować więcej niż jedną nieruchomość.</div>
	</div>`;
}

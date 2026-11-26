import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
	Card,
	CardAction,
	CardContent,
	CardDescription,
	CardFooter,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";
import { Checkbox } from "#/components/ui/checkbox";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "#/components/ui/collapsible";
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldLegend,
	FieldSet,
} from "#/components/ui/field";
import { Input } from "#/components/ui/input";
import { Separator } from "#/components/ui/separator";
import type {
	ListingFilters,
	OfferType,
	PostedWithin,
	ScoreProfile,
} from "#/lib/listing-filter-types";
import { SCORE_PROFILE_LABELS } from "#/lib/score-types";
import { sourceColor } from "#/lib/source-colors";
import { cn } from "#/lib/utils";
import type { LayerToggles } from "./constants";
import SourceLegend from "./SourceLegend";
import type { ListingsStats } from "./useListingsStats";

const OPTIONS: Array<{ key: keyof LayerToggles; label: string; hint: string }> =
	[
		{ key: "rail", label: "Kolej", hint: "Linie kolejowe" },
		{
			key: "plannedRail",
			label: "Kolej planowana",
			hint: "W budowie i planowana (OSM)",
		},
		{
			key: "portPolska",
			label: "Port Polska (KDP)",
			hint: "Katowice–Ostrawa (CPK/PLK)",
		},
		{
			key: "kolejPlus",
			label: "Kolej Plus",
			hint: "Program Kolej Plus (PKP PLK)",
		},
		{ key: "tram", label: "Tramwaje", hint: "Od zbliżenia 10" },
		{ key: "stops", label: "Przystanki", hint: "Stacje i przystanki" },
		{ key: "boundaries", label: "Granice", hint: "Województwa i powiaty" },
		{ key: "traffic", label: "Ruch drogowy", hint: "Korki (na żywo)" },
		{ key: "heatmap", label: "Gęstość POI", hint: "Mapa cieplna POI" },
		{ key: "listings", label: "Oferty", hint: "Mieszkania, domy, działki" },
		{ key: "models3d", label: "Turbiny 3D", hint: "glTF (demo: Margonin)" },
		{ key: "contours", label: "Warstwice", hint: "Poziomice z etykietami" },
		{ key: "hillshade", label: "Hillshade", hint: "Cieniowanie rzeźby" },
		{ key: "terrain", label: "Rzeźba 3D", hint: "Teren w skali 1:1" },
		{ key: "objects3d", label: "Budynki 3D", hint: "Od zbliżenia 13" },
		{ key: "elevation", label: "Wysokość", hint: "Odczyt n.p.m." },
	];

const POSTED_WITHIN_OPTIONS: Array<{
	value: PostedWithin | undefined;
	countKey: "all" | PostedWithin;
	label: string;
}> = [
	{ value: undefined, countKey: "all", label: "Wszystkie" },
	{ value: "today", countKey: "today", label: "Dziś" },
	{ value: "week", countKey: "week", label: "7 dni" },
	{ value: "month", countKey: "month", label: "30 dni" },
];

const OFFER_TYPE_OPTIONS: Array<{
	value: OfferType | undefined;
	label: string;
}> = [
	{ value: "sale", label: "Sprzedaż" },
	{ value: "rent", label: "Wynajem" },
	{ value: "auction", label: "Aukcje" },
	{ value: undefined, label: "Wszystkie" },
];

const SCORE_PROFILE_OPTIONS: Array<{ value: ScoreProfile; label: string }> = [
	{ value: "kamienica", label: SCORE_PROFILE_LABELS.kamienica },
	{ value: "nowe_mieszkanie", label: SCORE_PROFILE_LABELS.nowe_mieszkanie },
	{ value: "dzialka_kolej", label: SCORE_PROFILE_LABELS.dzialka_kolej },
	{ value: "dom_airbnb", label: SCORE_PROFILE_LABELS.dom_airbnb },
	{ value: "dom_kolej", label: SCORE_PROFILE_LABELS.dom_kolej },
	{ value: "dzialka_airbnb", label: SCORE_PROFILE_LABELS.dzialka_airbnb },
];

export default function MapControls({
	toggles,
	onChange,
	filters,
	onFiltersChange,
	stats,
	statsLoading,
}: {
	toggles: LayerToggles;
	onChange: (key: keyof LayerToggles, value: boolean) => void;
	filters: ListingFilters;
	onFiltersChange: (patch: Partial<ListingFilters> | "reset") => void;
	stats: ListingsStats | null;
	statsLoading: boolean;
}) {
	const [open, setOpen] = useState(true);
	const selectClass =
		"h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input/30";

	const priceValue = (value: string): number | undefined => {
		if (!value) return undefined;
		const parsed = Number(value);
		return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
	};

	const sourceOptions =
		stats?.bySource.filter(
			(row) => row.count > 0 || row.id === filters.sourceId,
		) ?? [];

	return (
		<Collapsible open={open} onOpenChange={setOpen}>
			<Card
				className={cn(
					"pointer-events-auto max-h-[calc(100vh-7rem)] overflow-hidden bg-card/95 backdrop-blur",
					open ? "w-72 gap-4 py-4" : "w-52 gap-0 py-2",
				)}
			>
				<CardHeader className={cn("shrink-0", !open && "px-3")}>
					<CardTitle>Warstwy</CardTitle>
					{open && (
						<CardDescription className="col-span-2">
							Wybierz warstwy mapy
						</CardDescription>
					)}
					<CardAction
						className={cn("flex items-center gap-1", open && "row-span-1")}
					>
						<Badge variant="secondary">Noc</Badge>
						<CollapsibleTrigger asChild>
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label={open ? "Zwiń warstwy" : "Rozwiń warstwy"}
							>
								<ChevronDownIcon
									className={cn(
										"transition-transform duration-200",
										!open && "-rotate-90",
									)}
								/>
							</Button>
						</CollapsibleTrigger>
					</CardAction>
				</CardHeader>
				<CollapsibleContent className="flex min-h-0 flex-col gap-4">
					<CardContent className="min-h-0 overflow-y-auto">
						<FieldSet>
							<FieldLegend variant="label" className="sr-only">
								Warstwy mapy
							</FieldLegend>
							<FieldGroup className="gap-1.5">
								{OPTIONS.map((option) => (
									<Field
										key={option.key}
										orientation="horizontal"
										className="items-start gap-3"
									>
										<Checkbox
											id={`layer-${option.key}`}
											checked={toggles[option.key]}
											onCheckedChange={(checked) =>
												onChange(option.key, checked === true)
											}
										/>
										<FieldContent>
											<FieldLabel
												htmlFor={`layer-${option.key}`}
												className="text-sm font-normal"
											>
												{option.label}
											</FieldLabel>
											<FieldDescription className="text-xs">
												{option.hint}
											</FieldDescription>
										</FieldContent>
									</Field>
								))}
							</FieldGroup>
						</FieldSet>
					</CardContent>
					{toggles.listings && (
						<CardContent className="flex flex-col gap-3 border-t pt-4">
							<div className="flex items-center justify-between">
								<span className="text-xs font-medium text-muted-foreground">
									Filtry ofert
								</span>
								<Button
									variant="ghost"
									size="xs"
									onClick={() => onFiltersChange("reset")}
								>
									Wyczyść
								</Button>
							</div>
							<div className="flex flex-col gap-1.5">
								<div className="flex items-center justify-between">
									<span className="text-xs text-muted-foreground">Dodano</span>
									{statsLoading && (
										<span className="text-[10px] text-muted-foreground">
											Ładowanie…
										</span>
									)}
								</div>
								<div className="grid grid-cols-2 gap-2">
									{POSTED_WITHIN_OPTIONS.map((option) => {
										const count = stats?.byPeriod[option.countKey];
										const active = filters.postedWithin === option.value;
										return (
											<Button
												key={option.countKey}
												variant={active ? "default" : "outline"}
												size="xs"
												className="w-full"
												aria-pressed={active}
												onClick={() =>
													onFiltersChange({
														postedWithin: option.value,
													})
												}
											>
												{option.label}
												{count === undefined
													? null
													: ` · ${count.toLocaleString("pl-PL")}`}
											</Button>
										);
									})}
								</div>
							</div>
							<div className="flex flex-col gap-1.5">
								<span className="text-xs text-muted-foreground">Rodzaj</span>
								<div className="grid grid-cols-2 gap-2">
									{OFFER_TYPE_OPTIONS.map((option) => {
										const count = option.value
											? stats?.byOfferType[option.value]
											: undefined;
										const active = filters.offerType === option.value;
										return (
											<Button
												key={option.label}
												variant={active ? "default" : "outline"}
												size="xs"
												className="w-full"
												aria-pressed={active}
												onClick={() =>
													onFiltersChange({
														offerType: option.value,
													})
												}
											>
												{option.label}
												{count === undefined
													? null
													: ` · ${count.toLocaleString("pl-PL")}`}
											</Button>
										);
									})}
								</div>
							</div>
							<label
								htmlFor="listing-source"
								className="flex flex-col gap-1 text-xs"
							>
								<span className="text-muted-foreground">Źródło</span>
								<div className="flex items-center gap-2">
									<span
										className="size-2 shrink-0 rounded-full"
										style={{
											backgroundColor: filters.sourceId
												? sourceColor(filters.sourceId)
												: "var(--muted-foreground)",
										}}
									/>
									<select
										id="listing-source"
										className={selectClass}
										value={filters.sourceId ?? ""}
										disabled={stats === null && sourceOptions.length === 0}
										onChange={(event) =>
											onFiltersChange({
												sourceId: event.target.value || undefined,
											})
										}
									>
										<option value="">Wszystkie źródła</option>
										{sourceOptions.map((row) => (
											<option key={row.id} value={row.id}>
												{row.label} ({row.count.toLocaleString("pl-PL")})
											</option>
										))}
									</select>
								</div>
							</label>
							<label className="flex flex-col gap-1 text-xs">
								<span className="text-muted-foreground">Nieruchomość</span>
								<select
									id="listing-property-type"
									className={selectClass}
									value={filters.propertyType ?? ""}
									onChange={(event) =>
										onFiltersChange({
											propertyType: event.target.value
												? (event.target.value as ListingFilters["propertyType"])
												: undefined,
										})
									}
								>
									<option value="">Wszystkie</option>
									<option value="flat">Mieszkanie</option>
									<option value="house">Dom</option>
									<option value="plot">Działka</option>
									<option value="commercial">Lokal</option>
									<option value="garage">Garaż</option>
									<option value="room">Pokój</option>
								</select>
							</label>
							<div className="grid grid-cols-2 gap-2">
								<label
									htmlFor="listing-min-score"
									className="flex flex-col gap-1 text-xs"
								>
									<span className="text-muted-foreground">Minimalny score</span>
									<Input
										id="listing-min-score"
										type="number"
										inputMode="numeric"
										min={0}
										max={100}
										placeholder="0–100"
										value={filters.minScore ?? ""}
										onChange={(event) =>
											onFiltersChange({
												minScore: priceValue(event.target.value),
											})
										}
									/>
								</label>
								<label className="flex flex-col gap-1 text-xs">
									<span className="text-muted-foreground">Sortowanie</span>
									<select
										id="listing-sort"
										className={selectClass}
										value={filters.sort ?? "newest"}
										onChange={(event) =>
											onFiltersChange({
												sort: event.target.value as ListingFilters["sort"],
											})
										}
									>
										<option value="newest">Najnowsze</option>
										<option value="score">Najwyżej oceniane</option>
									</select>
								</label>
							</div>
							<label className="flex flex-col gap-1 text-xs">
								<span className="text-muted-foreground">
									Profil atrakcyjności
								</span>
								<select
									id="listing-score-profile"
									className={selectClass}
									value={filters.scoreProfile ?? ""}
									onChange={(event) =>
										onFiltersChange({
											scoreProfile: event.target.value
												? (event.target.value as ScoreProfile)
												: undefined,
										})
									}
								>
									<option value="">Wszystkie profile</option>
									{SCORE_PROFILE_OPTIONS.map((option) => (
										<option key={option.value} value={option.value}>
											{option.label}
										</option>
									))}
								</select>
							</label>
							<div className="grid grid-cols-2 gap-2">
								<label
									htmlFor="listing-min-price"
									className="flex flex-col gap-1 text-xs"
								>
									<span className="text-muted-foreground">Cena od</span>
									<Input
										id="listing-min-price"
										type="number"
										inputMode="numeric"
										min={0}
										placeholder="PLN"
										value={filters.minPrice ?? ""}
										onChange={(event) =>
											onFiltersChange({
												minPrice: priceValue(event.target.value),
											})
										}
									/>
								</label>
								<label
									htmlFor="listing-max-price"
									className="flex flex-col gap-1 text-xs"
								>
									<span className="text-muted-foreground">Cena do</span>
									<Input
										id="listing-max-price"
										type="number"
										inputMode="numeric"
										min={0}
										placeholder="PLN"
										value={filters.maxPrice ?? ""}
										onChange={(event) =>
											onFiltersChange({
												maxPrice: priceValue(event.target.value),
											})
										}
									/>
								</label>
							</div>
							<SourceLegend stats={stats} />
						</CardContent>
					)}
					<CardFooter className="shrink-0 flex-col items-start gap-3">
						<Separator />
						<span className="text-xs font-medium text-muted-foreground">
							Legenda
						</span>
						<div className="grid grid-cols-2 gap-1.5">
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-orange-400" />
								kolej
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-pink-400" />w budowie
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-fuchsia-400" />
								planowana
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-teal-400" />
								Port Polska
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-lime-400" />
								Kolej Plus
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-sky-400" />
								tramwaje
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-amber-400" />
								stacje
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-sky-300" />
								przystanki
							</Badge>
							{toggles.traffic && (
								<>
									<Badge variant="outline">
										<span className="size-1.5 rounded-full bg-green-500" />
										płynnie
									</Badge>
									<Badge variant="outline">
										<span className="size-1.5 rounded-full bg-amber-500" />
										umiarkowanie
									</Badge>
									<Badge variant="outline">
										<span className="size-1.5 rounded-full bg-red-500" />
										korki
									</Badge>
								</>
							)}
						</div>
						{toggles.plannedRail && (
							<a
								href="https://www.openstreetmap.org/copyright"
								target="_blank"
								rel="noreferrer"
								className="text-[10px] text-muted-foreground underline-offset-2 hover:underline"
							>
								Kolej planowana: dane © OpenStreetMap (ODbL)
							</a>
						)}
					</CardFooter>
				</CollapsibleContent>
			</Card>
		</Collapsible>
	);
}

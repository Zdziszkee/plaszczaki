import { ChevronDownIcon, RotateCcwIcon } from "lucide-react";
import { type ReactNode, useId } from "react";
import { Button } from "#/components/ui/button";
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
import { Slider } from "#/components/ui/slider";
import {
	createDefaultRankingConfig,
	RANKING_PROFILE_SPECS,
	type RankingSignalSpec,
} from "#/lib/ranking-spec";
import type {
	RankingCategory,
	RankingConfig,
	RankingFilters,
} from "#/lib/ranking-types";

const CATEGORY_ORDER: RankingCategory[] = [
	"price",
	"location",
	"attributes",
	"nature",
	"opportunity",
];

const CATEGORY_LABELS: Record<RankingCategory, string> = {
	price: "Cena i wartość",
	location: "Lokalizacja",
	attributes: "Atrybuty",
	nature: "Natura",
	opportunity: "Okazja",
	penalty: "Kary",
};

const PROPERTY_OPTIONS = [
	["flat", "Mieszkania"],
	["house", "Domy"],
	["plot", "Działki"],
	["commercial", "Lokale"],
] as const;

const OFFER_OPTIONS = [
	["sale", "Sprzedaż"],
	["auction", "Aukcje"],
	["rent", "Wynajem"],
] as const;

const BUILDING_OPTIONS = [
	["tenement", "Kamienica"],
	["block", "Blok"],
	["apartment_building", "Apartamentowiec"],
	["detached_house", "Dom wolnostojący"],
	["row_house", "Dom szeregowy"],
	["semi_detached_house", "Bliźniak"],
	["house", "Dom"],
	["other", "Pozostałe"],
] as const;

function numberValue(value: string): number | null {
	if (!value.trim()) return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function ControlGroup({
	title,
	description,
	children,
	defaultOpen = false,
}: {
	title: string;
	description?: string;
	children: ReactNode;
	defaultOpen?: boolean;
}) {
	return (
		<Collapsible defaultOpen={defaultOpen} className="group border-b pb-3">
			<CollapsibleTrigger className="flex w-full items-center justify-between gap-3 py-2 text-left text-sm font-semibold">
				<span>{title}</span>
				<ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none" />
			</CollapsibleTrigger>
			<CollapsibleContent className="flex flex-col gap-4 pt-2 pb-1">
				{description && (
					<p className="text-xs leading-5 text-muted-foreground">
						{description}
					</p>
				)}
				{children}
			</CollapsibleContent>
		</Collapsible>
	);
}

function NumberRange({
	label,
	value,
	onChange,
	unit,
	minPlaceholder,
	maxPlaceholder,
}: {
	label: string;
	value: [number | null, number | null];
	onChange: (value: [number | null, number | null]) => void;
	unit?: string;
	minPlaceholder?: string;
	maxPlaceholder?: string;
}) {
	const id = useId();
	return (
		<FieldSet>
			<FieldLegend variant="label">{label}</FieldLegend>
			<FieldGroup className="grid grid-cols-2 gap-2">
				<Input
					id={`${id}-min`}
					type="number"
					inputMode="decimal"
					min={0}
					placeholder={minPlaceholder ?? "od"}
					value={value[0] ?? ""}
					onChange={(event) =>
						onChange([numberValue(event.target.value), value[1]])
					}
				/>
				<Input
					id={`${id}-max`}
					type="number"
					inputMode="decimal"
					min={0}
					placeholder={maxPlaceholder ?? "do"}
					value={value[1] ?? ""}
					onChange={(event) =>
						onChange([value[0], numberValue(event.target.value)])
					}
				/>
			</FieldGroup>
			{unit && <FieldDescription>Wartości w {unit}.</FieldDescription>}
		</FieldSet>
	);
}

function CheckboxGroup({
	legend,
	options,
	values,
	onChange,
}: {
	legend: string;
	options: ReadonlyArray<readonly [string, string]>;
	values: string[];
	onChange: (values: string[]) => void;
}) {
	return (
		<FieldSet>
			<FieldLegend variant="label">{legend}</FieldLegend>
			<FieldGroup className="grid grid-cols-2 gap-x-3 gap-y-2">
				<Field orientation="horizontal" className="gap-2">
					<Checkbox
						id={`${legend.replace(/\s+/g, "-").toLowerCase()}-all`}
						checked={values.length === 0}
						onCheckedChange={(checked) =>
							onChange(checked === true ? [] : [options[0]?.[0] ?? ""])
						}
					/>
					<FieldContent>
						<FieldLabel
							htmlFor={`${legend.replace(/\s+/g, "-").toLowerCase()}-all`}
							className="font-normal"
						>
							Wszystkie
						</FieldLabel>
					</FieldContent>
				</Field>
				{options.map(([value, label]) => {
					const id = `${legend.replace(/\s+/g, "-").toLowerCase()}-${value}`;
					return (
						<Field key={value} orientation="horizontal" className="gap-2">
							<Checkbox
								id={id}
								checked={values.includes(value)}
								onCheckedChange={(checked) =>
									onChange(
										checked === true
											? [...values, value]
											: values.filter((item) => item !== value),
									)
								}
							/>
							<FieldContent>
								<FieldLabel htmlFor={id} className="font-normal">
									{label}
								</FieldLabel>
							</FieldContent>
						</Field>
					);
				})}
			</FieldGroup>
		</FieldSet>
	);
}

function WeightSlider({
	label,
	value,
	onChange,
	max = 40,
}: {
	label: string;
	value: number;
	onChange: (value: number) => void;
	max?: number;
}) {
	const id = useId();
	return (
		<Field className="gap-1.5">
			<div className="flex items-center justify-between gap-3">
				<FieldLabel htmlFor={id} className="text-xs font-normal">
					{label}
				</FieldLabel>
				<span className="font-mono text-[11px] text-muted-foreground">
					{value.toFixed(1)}
				</span>
			</div>
			<Slider
				id={id}
				value={[value]}
				min={0}
				max={max}
				step={0.5}
				onValueChange={([next]) => onChange(next ?? 0)}
			/>
		</Field>
	);
}

export default function RankingControls({
	config,
	onChange,
}: {
	config: RankingConfig;
	onChange: (config: RankingConfig) => void;
}) {
	const spec = RANKING_PROFILE_SPECS[config.profile];
	const filterPatch = (patch: Partial<RankingFilters>) =>
		onChange({ ...config, filters: { ...config.filters, ...patch } });
	const optionPatch = (patch: Partial<RankingConfig["options"]>) =>
		onChange({ ...config, options: { ...config.options, ...patch } });

	const categoryTotal = CATEGORY_ORDER.reduce(
		(total, category) => total + config.categoryWeights[category],
		0,
	);
	const signalMax = (signal: RankingSignalSpec) => {
		const categoryBudget =
			categoryTotal > 0
				? (config.categoryWeights[signal.category] / categoryTotal) * 100
				: 0;
		const weights = spec.signals
			.filter((item) => item.category === signal.category)
			.reduce(
				(total, item) => total + (config.signalWeights[item.code] ?? 0),
				0,
			);
		return weights > 0
			? (categoryBudget * (config.signalWeights[signal.code] ?? 0)) / weights
			: 0;
	};

	return (
		<div className="flex flex-col">
			<div className="flex items-center justify-between gap-3 pb-2">
				<div>
					<h2 className="text-sm font-semibold">Konfiguracja rankingu</h2>
					<p className="text-xs text-muted-foreground">
						Filtry wybierają oferty, wagi ustalają ich punkty.
					</p>
				</div>
				<Button
					variant="ghost"
					size="icon-sm"
					aria-label="Przywróć domyślne ustawienia"
					onClick={() => onChange(createDefaultRankingConfig(config.profile))}
				>
					<RotateCcwIcon />
				</Button>
			</div>

			<ControlGroup title="Zakres ofert" defaultOpen>
				<CheckboxGroup
					legend="Nieruchomości"
					options={PROPERTY_OPTIONS}
					values={config.filters.propertyTypes}
					onChange={(propertyTypes) => filterPatch({ propertyTypes })}
				/>
				<CheckboxGroup
					legend="Rodzaj oferty"
					options={OFFER_OPTIONS}
					values={config.filters.offerTypes}
					onChange={(offerTypes) => filterPatch({ offerTypes })}
				/>
				<Field>
					<FieldLabel htmlFor="ranking-cities">Miasta</FieldLabel>
					<Input
						id="ranking-cities"
						placeholder="Kraków, Katowice"
						value={config.filters.cities.join(", ")}
						onChange={(event) =>
							filterPatch({
								cities: event.target.value
									.split(",")
									.map((city) => city.trim())
									.filter(Boolean),
							})
						}
					/>
				</Field>
				<Field>
					<FieldLabel htmlFor="ranking-posted">Dodano</FieldLabel>
					<select
						id="ranking-posted"
						className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
						value={config.filters.postedWithin}
						onChange={(event) =>
							filterPatch({
								postedWithin: event.target
									.value as RankingFilters["postedWithin"],
							})
						}
					>
						<option value="all">Cała baza</option>
						<option value="today">Ostatnie 24 godziny</option>
						<option value="week">Ostatnie 7 dni</option>
						<option value="month">Ostatnie 30 dni</option>
					</select>
				</Field>
			</ControlGroup>

			<ControlGroup title="Cena i metraż">
				<NumberRange
					label="Cena całkowita"
					unit="PLN"
					value={[config.filters.priceMin, config.filters.priceMax]}
					onChange={([priceMin, priceMax]) =>
						filterPatch({ priceMin, priceMax })
					}
				/>
				<NumberRange
					label="Cena za m²"
					unit="PLN/m²"
					value={[config.filters.pricePerM2Min, config.filters.pricePerM2Max]}
					onChange={([pricePerM2Min, pricePerM2Max]) =>
						filterPatch({ pricePerM2Min, pricePerM2Max })
					}
				/>
				<NumberRange
					label="Metraż użytkowy"
					unit="m²"
					value={[config.filters.areaMin, config.filters.areaMax]}
					onChange={([areaMin, areaMax]) => filterPatch({ areaMin, areaMax })}
				/>
				<NumberRange
					label="Metraż działki"
					unit="m²"
					value={[config.filters.landAreaMin, config.filters.landAreaMax]}
					onChange={([landAreaMin, landAreaMax]) =>
						filterPatch({ landAreaMin, landAreaMax })
					}
				/>
				<NumberRange
					label="Liczba pokoi"
					value={[config.filters.roomsMin, config.filters.roomsMax]}
					onChange={([roomsMin, roomsMax]) =>
						filterPatch({ roomsMin, roomsMax })
					}
				/>
			</ControlGroup>

			<ControlGroup title="Budynek i wnętrze" defaultOpen>
				<CheckboxGroup
					legend="Typ budynku"
					options={BUILDING_OPTIONS}
					values={config.filters.buildingTypes}
					onChange={(buildingTypes) => filterPatch({ buildingTypes })}
				/>
				<NumberRange
					label="Rok budowy"
					value={[config.filters.buildYearMin, config.filters.buildYearMax]}
					onChange={([buildYearMin, buildYearMax]) =>
						filterPatch({ buildYearMin, buildYearMax })
					}
				/>
				<Field>
					<FieldLabel htmlFor="ranking-ceiling">
						Minimalna wysokość sufitów
					</FieldLabel>
					<Input
						id="ranking-ceiling"
						type="number"
						inputMode="decimal"
						min={0}
						step={0.1}
						placeholder="np. 3,0"
						value={config.filters.minCeilingHeight ?? ""}
						onChange={(event) =>
							filterPatch({ minCeilingHeight: numberValue(event.target.value) })
						}
					/>
					<FieldDescription>
						Metr; brak danych nie spełnia filtra.
					</FieldDescription>
				</Field>
				<Field>
					<FieldLabel htmlFor="ranking-large-windows">Duże okna</FieldLabel>
					<select
						id="ranking-large-windows"
						className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
						value={config.filters.largeWindows}
						onChange={(event) =>
							filterPatch({
								largeWindows: event.target
									.value as RankingFilters["largeWindows"],
							})
						}
					>
						<option value="any">Bez znaczenia</option>
						<option value="yes">Wymagane</option>
						<option value="no">Bez dużych okien</option>
					</select>
				</Field>
			</ControlGroup>

			<ControlGroup title="Rynek, stan i instalacje">
				<CheckboxGroup
					legend="Rynek"
					options={[
						["primary", "Pierwotny"],
						["secondary", "Wtórny"],
					]}
					values={config.filters.markets}
					onChange={(markets) => filterPatch({ markets })}
				/>
				<CheckboxGroup
					legend="Stan"
					options={[
						["developer", "Deweloperski"],
						["move_in_ready", "Gotowy do zamieszkania"],
						["very_good", "Bardzo dobry"],
						["good", "Dobry"],
						["to_renovate", "Do remontu"],
					]}
					values={config.filters.conditions}
					onChange={(conditions) => filterPatch({ conditions })}
				/>
				<CheckboxGroup
					legend="Ogrzewanie"
					options={[
						["district", "Miejskie"],
						["gas", "Gazowe"],
						["electric", "Elektryczne"],
						["heat_pump", "Pompa ciepła"],
					]}
					values={config.filters.heatingTypes}
					onChange={(heatingTypes) => filterPatch({ heatingTypes })}
				/>
				<CheckboxGroup
					legend="Okna"
					options={[
						["plastic", "Plastikowe"],
						["new", "Nowe"],
						["partially_new", "Częściowo wymienione"],
					]}
					values={config.filters.windowsTypes}
					onChange={(windowsTypes) => filterPatch({ windowsTypes })}
				/>
			</ControlGroup>

			<ControlGroup title="Udogodnienia">
				{(
					[
						["elevator", "Winda"],
						["parking", "Parking"],
						["balcony", "Balkon"],
						["terrace", "Taras"],
						["garden", "Ogród"],
						["internet", "Internet"],
						["airConditioning", "Klimatyzacja"],
						["districtHeating", "Ogrzewanie miejskie"],
					] as const
				).map(([key, label]) => (
					<Field key={key}>
						<FieldLabel htmlFor={`ranking-amenity-${key}`}>{label}</FieldLabel>
						<select
							id={`ranking-amenity-${key}`}
							className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
							value={config.filters[key]}
							onChange={(event) =>
								filterPatch({
									[key]: event.target.value,
								} as Partial<RankingFilters>)
							}
						>
							<option value="any">Bez znaczenia</option>
							<option value="yes">Wymagane</option>
							<option value="no">Wykluczone</option>
						</select>
					</Field>
				))}
			</ControlGroup>

			<ControlGroup title="Odległość od centrum">
				<Field>
					<FieldLabel htmlFor="ranking-center">Centrum</FieldLabel>
					<select
						id="ranking-center"
						className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
						value={config.filters.center ?? ""}
						onChange={(event) =>
							filterPatch({
								center: (event.target.value ||
									null) as RankingFilters["center"],
							})
						}
					>
						<option value="">Bez filtra</option>
						<option value="krakow">Kraków — Rynek Główny</option>
						<option value="katowice">Katowice — Rynek</option>
					</select>
				</Field>
				<Field>
					<FieldLabel htmlFor="ranking-center-distance">
						Maksymalna odległość
					</FieldLabel>
					<Input
						id="ranking-center-distance"
						type="number"
						inputMode="decimal"
						min={0}
						step={0.5}
						placeholder="km"
						value={config.filters.maxCenterDistanceKm ?? ""}
						onChange={(event) =>
							filterPatch({
								maxCenterDistanceKm: numberValue(event.target.value),
							})
						}
					/>
					<FieldDescription>Odległość w linii prostej.</FieldDescription>
				</Field>
			</ControlGroup>

			<ControlGroup title="Wagi kategorii" defaultOpen>
				{CATEGORY_ORDER.map((category) => (
					<WeightSlider
						key={category}
						label={CATEGORY_LABELS[category]}
						value={config.categoryWeights[category]}
						max={60}
						onChange={(value) =>
							onChange({
								...config,
								categoryWeights: {
									...config.categoryWeights,
									[category]: value,
								},
							})
						}
					/>
				))}
				<p className="text-[11px] leading-4 text-muted-foreground">
					Wagi są normalizowane do 100 punktów. Zmiana jednej kategorii zmienia
					udział pozostałych.
				</p>
			</ControlGroup>

			<ControlGroup title="Wagi atrybutów" defaultOpen>
				{CATEGORY_ORDER.map((category) => {
					const signals = spec.signals.filter(
						(signal) => signal.category === category,
					);
					if (signals.length === 0) return null;
					return (
						<FieldSet key={category}>
							<FieldLegend variant="label">
								{CATEGORY_LABELS[category]}
							</FieldLegend>
							<FieldGroup className="gap-3">
								{signals.map((signal) => (
									<div key={signal.code} className="flex flex-col gap-1">
										<WeightSlider
											label={`${signal.label} · maks. ${signalMax(signal).toFixed(1)} pkt`}
											value={config.signalWeights[signal.code] ?? 0}
											onChange={(value) =>
												onChange({
													...config,
													signalWeights: {
														...config.signalWeights,
														[signal.code]: value,
													},
												})
											}
										/>
									</div>
								))}
							</FieldGroup>
						</FieldSet>
					);
				})}
			</ControlGroup>

			<ControlGroup title="Kary">
				{spec.penalties.map((penalty) => (
					<WeightSlider
						key={penalty.code}
						label={`${penalty.label} · ${penalty.defaultWeight} pkt domyślnie`}
						value={config.penaltyWeights[penalty.code] ?? 0}
						max={30}
						onChange={(value) =>
							onChange({
								...config,
								penaltyWeights: {
									...config.penaltyWeights,
									[penalty.code]: value,
								},
							})
						}
					/>
				))}
			</ControlGroup>

			<ControlGroup title="Zasady danych">
				<Field orientation="horizontal">
					<Checkbox
						id="ranking-require-price"
						checked={config.options.requirePriceEvidence}
						onCheckedChange={(checked) =>
							optionPatch({ requirePriceEvidence: checked === true })
						}
					/>
					<FieldContent>
						<FieldLabel htmlFor="ranking-require-price">
							Wymagaj przewagi cenowej
						</FieldLabel>
						<FieldDescription>
							Oferta musi mieć potwierdzone porównanie ceny.
						</FieldDescription>
					</FieldContent>
				</Field>
				<Field>
					<FieldLabel htmlFor="ranking-confidence">
						Minimalna pewność ceny
					</FieldLabel>
					<select
						id="ranking-confidence"
						className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
						value={config.options.minPriceConfidence}
						onChange={(event) =>
							optionPatch({
								minPriceConfidence: event.target
									.value as RankingConfig["options"]["minPriceConfidence"],
							})
						}
					>
						<option value="any">Dowolna</option>
						<option value="low">Niska</option>
						<option value="medium">Średnia</option>
						<option value="high">Wysoka</option>
					</select>
				</Field>
				<Field>
					<FieldLabel htmlFor="ranking-missing">Brakujące sygnały</FieldLabel>
					<select
						id="ranking-missing"
						className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
						value={config.options.missingPolicy}
						onChange={(event) =>
							optionPatch({
								missingPolicy: event.target
									.value as RankingConfig["options"]["missingPolicy"],
							})
						}
					>
						<option value="zero">Licz jako 0</option>
						<option value="exclude">Wykluczaj oferty</option>
					</select>
				</Field>
			</ControlGroup>

			<Separator className="my-4" />
			<Field>
				<FieldLabel htmlFor="ranking-sort">Sortowanie</FieldLabel>
				<select
					id="ranking-sort"
					className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
					value={config.filters.sort}
					onChange={(event) =>
						filterPatch({ sort: event.target.value as RankingFilters["sort"] })
					}
				>
					<option value="score">Wynik</option>
					<option value="newest">Najnowsze</option>
					<option value="price">Najniższa cena</option>
				</select>
			</Field>
			<Field>
				<FieldLabel htmlFor="ranking-limit">Liczba wyników</FieldLabel>
				<select
					id="ranking-limit"
					className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
					value={config.filters.resultLimit}
					onChange={(event) =>
						filterPatch({ resultLimit: Number(event.target.value) })
					}
				>
					<option value={25}>25</option>
					<option value={50}>50</option>
					<option value={100}>100</option>
					<option value={250}>250</option>
				</select>
			</Field>
		</div>
	);
}

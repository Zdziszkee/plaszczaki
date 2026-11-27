import {
	createFileRoute,
	Link,
	useNavigate,
	useRouter,
} from "@tanstack/react-router";
import {
	ArrowRightIcon,
	AwardIcon,
	BuildingIcon,
	DownloadIcon,
	HouseIcon,
	LandPlotIcon,
	ListOrderedIcon,
	MapIcon,
	SlidersHorizontalIcon,
	Trash2Icon,
	UploadIcon,
} from "lucide-react";
import { type ChangeEvent, useRef, useState } from "react";
import RankingBreakdownView from "#/components/RankingBreakdown";
import RankingControls from "#/components/RankingControls";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
	Card,
	CardContent,
	CardFooter,
	CardHeader,
} from "#/components/ui/card";
import { Field, FieldLabel } from "#/components/ui/field";
import { Input } from "#/components/ui/input";
import { Spinner } from "#/components/ui/spinner";
import {
	activateRankingPreset,
	deleteRankingPreset,
	getRankingInitialData,
	getRankingPage,
	type RankingPresetDto,
	saveRankingPreset,
} from "#/lib/ranking.functions";
import {
	type RankingConfig,
	type RankingResult,
	sanitizeRankingConfig,
} from "#/lib/ranking-engine";
import { SCORE_PROFILE_LABELS } from "#/lib/ranking-spec";
import type { RankedListing } from "#/lib/ranking-types";

type RankingSearch = {
	presetId?: number;
};

function validPresetId(value: unknown): number | undefined {
	const parsed = typeof value === "number" ? value : Number(value);
	return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function formatPln(value: number | null): string {
	if (value === null) return "Cena do uzgodnienia";
	return new Intl.NumberFormat("pl-PL", {
		style: "currency",
		currency: "PLN",
		maximumFractionDigits: 0,
	}).format(value);
}

function formatNumber(value: number | null, suffix = ""): string {
	return value === null ? "—" : `${value.toLocaleString("pl-PL")}${suffix}`;
}

function scoreColor(score: number): string {
	if (score >= 80) return "text-emerald-400";
	if (score >= 60) return "text-lime-300";
	if (score >= 40) return "text-amber-300";
	return "text-orange-400";
}

function PropertyIcon({ type }: { type: string }) {
	if (type === "house") return <HouseIcon className="size-10 opacity-30" />;
	if (type === "plot") return <LandPlotIcon className="size-10 opacity-30" />;
	return <BuildingIcon className="size-10 opacity-30" />;
}

function ResultCard({ item, rank }: { item: RankedListing; rank: number }) {
	const listing = item.listing;
	const area =
		listing.propertyType === "plot" ? listing.landAreaM2 : listing.areaM2;
	const location = [listing.district, listing.city].filter(Boolean).join(" · ");
	const ceiling =
		listing.scoreSignals?.metrics?.ceiling_height_m ?? listing.ceilingHeightM;
	const pricePerM2 = item.pricePerM2Pln ?? listing.pricePerM2Pln;
	return (
		<Card className="group gap-0 overflow-hidden border-border/70 bg-card/75 py-0 backdrop-blur-sm transition-colors hover:border-primary/40">
			<CardContent className="relative h-36 overflow-hidden bg-muted p-0">
				{listing.images?.[0] ? (
					<img
						src={listing.images[0]}
						alt=""
						loading="lazy"
						className="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03] motion-reduce:transition-none"
					/>
				) : (
					<div className="flex size-full items-center justify-center text-muted-foreground">
						<PropertyIcon type={listing.propertyType} />
					</div>
				)}
				<div className="absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-3">
					<Badge className="bg-background/85 font-mono text-[10px] backdrop-blur">
						#{String(rank).padStart(2, "0")}
					</Badge>
					<Badge className="bg-background/85 font-mono text-[10px] backdrop-blur">
						<span className={scoreColor(item.score)}>{item.score}/100</span>
					</Badge>
				</div>
			</CardContent>
			<CardHeader className="gap-2 px-4 pt-4">
				<div className="flex flex-wrap gap-1.5">
					<Badge variant="secondary">{SCORE_PROFILE_LABELS.universal}</Badge>
					{listing.buildingType && (
						<Badge variant="outline">{listing.buildingType}</Badge>
					)}
					{ceiling !== null && ceiling !== undefined && (
						<Badge variant="outline">
							sufit {ceiling.toLocaleString("pl-PL")} m
						</Badge>
					)}
					{listing.scoreSignals?.metrics?.large_windows === true && (
						<Badge variant="outline">duże okna</Badge>
					)}
					{item.distanceKm !== null && (
						<Badge variant="outline">
							{item.distanceKm.toLocaleString("pl-PL", {
								maximumFractionDigits: 1,
							})}{" "}
							km od centrum
						</Badge>
					)}
				</div>
				<a
					href={listing.url}
					target="_blank"
					rel="noreferrer"
					className="line-clamp-2 text-sm leading-5 font-semibold hover:underline"
				>
					{listing.title ?? "Oferta nieruchomości"}
				</a>
				{location && (
					<p className="text-xs text-muted-foreground">{location}</p>
				)}
			</CardHeader>
			<CardContent className="flex flex-col gap-3 px-4">
				<div className="flex items-end justify-between gap-3">
					<div>
						<div className="text-lg font-semibold tracking-tight">
							{formatPln(listing.pricePln)}
						</div>
						{pricePerM2 !== null && (
							<div className="text-xs text-muted-foreground">
								{formatPln(pricePerM2)}/m²
							</div>
						)}
					</div>
					<div className="text-right text-xs text-muted-foreground">
						<div>{formatNumber(area, " m²")}</div>
						{listing.rooms !== null && <div>{listing.rooms} pok.</div>}
						{listing.buildYear !== null && <div>rok {listing.buildYear}</div>}
					</div>
				</div>
				<RankingBreakdownView
					breakdown={item.breakdown}
					score={item.score}
					priceEvidence={listing.scoreSignals?.priceEvidence}
				/>
			</CardContent>
			<CardFooter className="flex-wrap justify-between gap-2 px-4 pt-3 pb-4">
				<span className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
					{listing.sourceLabel}
				</span>
				<Button asChild variant="outline" size="sm">
					<a href={listing.url} target="_blank" rel="noreferrer">
						Otwórz ofertę
						<ArrowRightIcon data-icon="inline-end" />
					</a>
				</Button>
			</CardFooter>
		</Card>
	);
}

function RankingWorkbench({
	initialConfig,
	initialResult,
	presets,
	selectedPresetId,
}: {
	initialConfig: RankingConfig;
	initialResult: RankingResult;
	presets: RankingPresetDto[];
	selectedPresetId: number | null;
}) {
	const navigate = useNavigate({ from: "/oferty" });
	const router = useRouter();
	const [config, setConfig] = useState(() =>
		sanitizeRankingConfig(initialConfig),
	);
	const [result, setResult] = useState(initialResult);
	const [loading, setLoading] = useState(false);
	const [previewError, setPreviewError] = useState(false);
	const [presetName, setPresetName] = useState("");
	const [saving, setSaving] = useState(false);
	const [message, setMessage] = useState<string | null>(null);
	const fileRef = useRef<HTMLInputElement>(null);
	const previewTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
	const previewAbort = useRef<AbortController | null>(null);
	const previewRequest = useRef(0);

	const updateConfig = (next: RankingConfig) => {
		setConfig(next);
		setPreviewError(false);
		if (previewTimeout.current) clearTimeout(previewTimeout.current);
		previewTimeout.current = setTimeout(async () => {
			const request = ++previewRequest.current;
			setLoading(true);
			const controller = new AbortController();
			previewAbort.current?.abort();
			previewAbort.current = controller;
			try {
				const nextResult = await getRankingPage({
					data: { config: next },
					fetch: (input, init) =>
						fetch(input, { ...init, signal: controller.signal }),
				});
				if (request === previewRequest.current) setResult(nextResult);
			} catch (error) {
				if (
					request === previewRequest.current &&
					!(error instanceof DOMException && error.name === "AbortError")
				) {
					setPreviewError(true);
				}
				if (request === previewRequest.current && controller.signal.aborted) {
					setLoading(false);
				}
			} finally {
				if (request === previewRequest.current) setLoading(false);
			}
		}, 180);
	};
	const selectedPreset = presets.find(
		(preset) => preset.id === selectedPresetId,
	);

	const selectPreset = (id: number | null) => {
		if (previewTimeout.current) clearTimeout(previewTimeout.current);
		previewAbort.current?.abort();
		navigate({
			search: () => ({
				presetId: id ?? undefined,
			}),
		});
	};

	const save = async () => {
		setSaving(true);
		setMessage(null);
		try {
			const preset = await saveRankingPreset({
				data: { name: presetName, config },
			});
			setPresetName("");
			setMessage(`Zapisano preset „${preset.name}”.`);
			navigate({
				search: () => ({
					presetId: preset.id,
				}),
			});
		} catch (error) {
			setMessage(
				error instanceof Error
					? error.message
					: "Nie udało się zapisać presetu.",
			);
		} finally {
			setSaving(false);
		}
	};

	const activate = async () => {
		if (!selectedPresetId) return;
		setMessage(null);
		try {
			await activateRankingPreset({ data: { id: selectedPresetId } });
			await router.invalidate();
			setMessage("Preset jest aktywny.");
		} catch (error) {
			setMessage(
				error instanceof Error
					? error.message
					: "Nie udało się aktywować presetu.",
			);
		}
	};

	const remove = async () => {
		if (!selectedPresetId) return;
		if (!window.confirm("Usunąć wybrany preset?")) return;
		setMessage(null);
		try {
			await deleteRankingPreset({ data: { id: selectedPresetId } });
			selectPreset(null);
			setMessage("Preset usunięty.");
		} catch (error) {
			setMessage(
				error instanceof Error
					? error.message
					: "Nie udało się usunąć presetu.",
			);
		}
	};

	const exportConfig = () => {
		const blob = new Blob([JSON.stringify(config, null, 2)], {
			type: "application/json",
		});
		const url = URL.createObjectURL(blob);
		const anchor = document.createElement("a");
		anchor.href = url;
		anchor.download = "ranking.json";
		anchor.click();
		URL.revokeObjectURL(url);
	};

	const importConfig = async (event: ChangeEvent<HTMLInputElement>) => {
		const file = event.target.files?.[0];
		event.target.value = "";
		if (!file) return;
		try {
			const parsed: unknown = JSON.parse(await file.text());
			const next = sanitizeRankingConfig(parsed);
			updateConfig(next);
			setMessage("Wczytano konfigurację.");
		} catch {
			setMessage("Plik nie zawiera prawidłowej konfiguracji rankingu.");
		}
	};

	return (
		<div className="grid items-start gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
			<aside className="lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto lg:pr-2">
				<RankingControls config={config} onChange={updateConfig} />
			</aside>

			<div className="flex min-w-0 flex-col gap-5">
				<Card className="gap-3 py-4">
					<div className="flex flex-wrap items-end gap-3 px-4">
						<Field className="min-w-48 flex-1">
							<FieldLabel htmlFor="ranking-preset">Preset</FieldLabel>
							<select
								id="ranking-preset"
								className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
								value={selectedPresetId ?? ""}
								onChange={(event) =>
									selectPreset(
										event.target.value ? Number(event.target.value) : null,
									)
								}
							>
								<option value="">Konfiguracja domyślna</option>
								{presets.map((preset) => (
									<option key={preset.id} value={preset.id}>
										{preset.name}
										{preset.isActive ? " · aktywny" : ""}
									</option>
								))}
							</select>
						</Field>
						<Field className="min-w-48 flex-1">
							<FieldLabel htmlFor="ranking-preset-name">
								Nazwa nowego presetu
							</FieldLabel>
							<Input
								id="ranking-preset-name"
								placeholder="np. Kamienice przy centrum"
								value={presetName}
								onChange={(event) => setPresetName(event.target.value)}
							/>
						</Field>
					</div>
					<div className="flex flex-wrap gap-2 px-4">
						<Button
							size="sm"
							disabled={!presetName.trim() || saving}
							onClick={save}
						>
							{saving ? (
								<Spinner />
							) : (
								<ListOrderedIcon data-icon="inline-start" />
							)}
							Zapisz preset
						</Button>
						<Button
							size="sm"
							variant="secondary"
							disabled={!selectedPreset}
							onClick={activate}
						>
							Aktywuj
						</Button>
						<Button size="sm" variant="outline" onClick={exportConfig}>
							<DownloadIcon data-icon="inline-start" />
							Eksport
						</Button>
						<Button
							size="sm"
							variant="outline"
							onClick={() => fileRef.current?.click()}
						>
							<UploadIcon data-icon="inline-start" />
							Import
						</Button>
						<input
							ref={fileRef}
							type="file"
							accept="application/json"
							className="hidden"
							onChange={importConfig}
						/>
						{selectedPreset && (
							<Button size="sm" variant="ghost" onClick={remove}>
								<Trash2Icon data-icon="inline-start" />
								Usuń
							</Button>
						)}
					</div>
					{message && (
						<p className="px-4 text-xs text-muted-foreground">{message}</p>
					)}
					{previewError && (
						<p className="px-4 text-xs text-destructive">
							Nie udało się przeliczyć rankingu.
						</p>
					)}
				</Card>

				<div className="flex flex-wrap items-end justify-between gap-3 border-b pb-4">
					<div>
						<h2 className="text-2xl font-semibold tracking-tight">Wyniki</h2>
						<p className="text-xs text-muted-foreground">
							{loading
								? "Przeliczam wyniki dla nowych wag…"
								: `${result.totalMatched.toLocaleString("pl-PL")} po filtrach · ${result.totalScored.toLocaleString("pl-PL")} punktowanych · ${result.items.length.toLocaleString("pl-PL")} wyświetlonych${result.excludedMissing > 0 ? ` · ${result.excludedMissing.toLocaleString("pl-PL")} z brakującymi danymi` : ""}`}
						</p>
					</div>
					<div className="flex items-center gap-2 text-xs text-muted-foreground">
						{loading && <Spinner />}
						{loading ? "Przeliczam…" : "Wynik aktualny"}
					</div>
				</div>

				{loading ? (
					<Card className="border-dashed py-12 text-center">
						<CardContent className="flex flex-col items-center gap-3 text-sm text-muted-foreground">
							<Spinner />
							Dopasowujemy oferty do nowych kryteriów.
						</CardContent>
					</Card>
				) : result.totalMatched === 0 ? (
					<Card className="border-dashed py-12 text-center">
						<CardContent className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
							<SlidersHorizontalIcon className="size-8 opacity-40" />
							Brak ofert spełniających wybrane kryteria.
							<p className="max-w-md text-xs">
								Jeśli lista jest pusta mimo świeżych danych, uruchom ekstrakcję
								sygnałów w scraperze.
							</p>
						</CardContent>
					</Card>
				) : (
					<div className="flex flex-col gap-4">
						{result.items.map((item, index) => (
							<ResultCard key={item.listing.id} item={item} rank={index + 1} />
						))}
					</div>
				)}
			</div>
		</div>
	);
}

export const Route = createFileRoute("/oferty")({
	validateSearch: (search: Record<string, unknown>): RankingSearch => ({
		presetId: validPresetId(search.presetId),
	}),
	loaderDeps: ({ search }) => ({
		presetId: search.presetId,
	}),
	loader: async ({ deps }) => await getRankingInitialData({ data: deps }),
	pendingComponent: RankingPending,
	component: RankingPage,
	head: () => ({
		meta: [
			{ title: "Ranking ofert — Plaszczaki" },
			{
				name: "description",
				content:
					"Konfigurowalny ranking nieruchomości: metraż, cena za metr, typ budynku, sufity, lokalizacja i wiele innych kryteriów.",
			},
		],
	}),
});

function RankingPending() {
	return (
		<main className="mx-auto flex min-h-[60vh] w-full max-w-[1600px] items-center justify-center px-4">
			<div className="flex items-center gap-3 text-muted-foreground">
				<Spinner />
				Przygotowuję ranking…
			</div>
		</main>
	);
}

function RankingPage() {
	const data = Route.useLoaderData();
	return (
		<main className="mx-auto w-full max-w-[1600px] px-4 py-10 sm:py-14">
			<div className="flex flex-col gap-6 border-b border-border pb-8 lg:flex-row lg:items-end lg:justify-between">
				<div className="flex flex-col gap-3">
					<Badge
						variant="outline"
						className="w-fit font-mono uppercase tracking-[0.16em]"
					>
						<AwardIcon />
						Twój ranking nieruchomości
					</Badge>
					<h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
						Ustaw kryteria. Zobacz najlepsze oferty.
					</h1>
					<p className="max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">
						Ustaw wagę odległości do kolejki, tramwaju, jeziora, rzeki, lasu i
						centrum miasta. Suwaki zawsze sumują się do 100%, a wyniki
						przeliczają się automatycznie.
					</p>
				</div>
				<Button asChild variant="outline">
					<Link to="/map">
						<MapIcon data-icon="inline-start" />
						Zobacz na mapie
					</Link>
				</Button>
			</div>

			<div className="mt-6">
				<RankingWorkbench
					key={data.selectedPresetId ?? "default"}
					initialConfig={data.config}
					initialResult={data.result}
					presets={data.presets}
					selectedPresetId={data.selectedPresetId}
				/>
			</div>
		</main>
	);
}

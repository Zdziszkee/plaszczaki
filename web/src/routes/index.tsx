import { createFileRoute, Link } from "@tanstack/react-router";
import {
	ArrowDownIcon,
	ArrowRightIcon,
	ArrowUpRightIcon,
	ListOrderedIcon,
	MapIcon,
	MapPinIcon,
	TrainFrontIcon,
} from "lucide-react";
import AsciiArt from "#/components/AsciiArt";
import SkylineBackground from "#/components/SkylineBackground";
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

export const Route = createFileRoute("/")({
	head: () => ({
		meta: [
			{ title: "Plaszczaki — rynek nieruchomości w kontekście" },
			{
				name: "description",
				content:
					"Oferty nieruchomości z wielu portali, mapa ofert i warstwy kolejowe dla Małopolski i Śląska.",
			},
		],
		scripts: [
			{ type: "module", src: "https://www.ascii-magic.com/ascii/ascii.js" },
		],
	}),
	component: App,
});

const SIGNALS = [
	{
		value: "154",
		label: "źródeł katalogowych",
		detail: "portale i inne źródła",
	},
	{ value: "02", label: "województwa", detail: "małopolskie i śląskie" },
	{ value: "01", label: "wspólna baza", detail: "SQLite + Drizzle" },
];

const FEATURES = [
	{
		piece: "world-map-pings",
		size: 7,
		eyebrow: "MAPA / OFERTY",
		title: "Mapa ofert",
		description:
			"Klasterowanie, filtry i ceny w jednym widoku. Przybliżaj mapę, żeby przejść od regionu do konkretnych ogłoszeń.",
		icon: MapIcon,
		cta: "Otwórz mapę",
		to: "/map",
	},
	{
		piece: "train-at-dusk",
		size: 6,
		eyebrow: "RANKING / DZIENNIE",
		title: "Top oferty",
		description:
			"25 najlepiej ocenionych mieszkań, domów i działek dodanych każdego dnia. Zobacz punktację i konkretne powody wyboru.",
		icon: ListOrderedIcon,
		cta: "Zobacz ranking",
		to: "/oferty",
	},
	{
		piece: "train-at-dusk",
		size: 6,
		eyebrow: "KOLEJ / KONTEKST",
		title: "Kolej w kontekście",
		description:
			"Linie istniejące, planowane i Kolej Plus na tej samej mapie. Oceniaj lokalizację nie tylko po cenie za metr.",
		icon: TrainFrontIcon,
		cta: "Zobacz warstwy",
		to: "/map",
	},
];

const PIPELINE = [
	{
		title: "Zbieramy",
		description: "Adaptery pobierają oferty z portali i innych źródeł.",
	},
	{
		title: "Porządkujemy",
		description: "Jedna schema, walidacja, deduplikacja i geokodowanie.",
	},
	{
		title: "Pokazujemy",
		description: "Mapa, filtry i warstwy kolejowe czytają tę samą bazę.",
	},
];

const REGIONS = [
	{
		code: "PL-12",
		name: "Małopolskie",
		cities: "Kraków · Tarnów · Nowy Sącz · Oświęcim · Zakopane",
		coordinates: ["19.08–21.42° E", "49.17–50.61° N"],
	},
	{
		code: "PL-24",
		name: "Śląskie",
		cities: "Katowice · Gliwice · Częstochowa · Bielsko-Biała · Rybnik",
		coordinates: ["17.64–20.11° E", "49.35–51.06° N"],
	},
];

function App() {
	return (
		<>
			<SkylineBackground />
			<main className="relative mx-auto w-full max-w-5xl px-4 pb-8 pt-10 sm:pt-14">
				<section
					aria-labelledby="hero-heading"
					className="grid items-center gap-10 lg:grid-cols-[1.04fr_0.96fr] lg:gap-12"
				>
					<div className="flex flex-col items-start gap-6">
						<Badge
							variant="outline"
							className="font-mono uppercase tracking-[0.16em]"
						>
							<MapPinIcon />
							Małopolskie × Śląskie
						</Badge>
						<h1
							id="hero-heading"
							className="max-w-3xl text-5xl leading-[0.98] font-semibold tracking-[-0.045em] text-balance sm:text-6xl lg:text-7xl"
						>
							Miasto w liczbach.
							<span className="block text-muted-foreground">
								Mieszkanie w kontekście.
							</span>
						</h1>
						<p className="max-w-xl text-base leading-7 text-muted-foreground sm:text-lg">
							Oferty z wielu portali, zebrane w jednym miejscu. Porównuj nie
							tylko cenę i metraż — także okolice, połączenia i rytm miasta.
						</p>
						<div className="flex flex-wrap gap-3">
							<Button asChild size="lg">
								<Link to="/map">
									<MapIcon data-icon="inline-start" />
									Otwórz mapę
								</Link>
							</Button>
							<Button asChild variant="outline" size="lg">
								<Link to="/oferty">
									<ListOrderedIcon data-icon="inline-start" />
									Top oferty
								</Link>
							</Button>
							<Button asChild variant="outline" size="lg">
								<a href="#jak-to-dziala">
									Jak to działa
									<ArrowDownIcon data-icon="inline-end" />
								</a>
							</Button>
						</div>
						<div className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
							<span>154 źródeł</span>
							<span>2 województwa</span>
							<span>jedna baza</span>
						</div>
					</div>

					<Card className="relative gap-0 overflow-hidden border-border/70 bg-card/70 py-0 backdrop-blur-sm">
						<CardHeader className="relative z-10 gap-1 border-b bg-card/60 px-4 py-3 backdrop-blur-sm sm:px-5">
							<CardTitle className="font-mono text-[10px] font-normal uppercase tracking-[0.2em] text-muted-foreground">
								Obserwatorium / 001
							</CardTitle>
							<CardDescription className="font-mono text-[10px] tracking-[0.08em]">
								50.0614° N · 19.9366° E
							</CardDescription>
							<CardAction>
								<Badge
									variant="secondary"
									className="font-mono text-[9px] uppercase tracking-[0.12em]"
								>
									<span className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />
									ASCII / 3D
								</Badge>
							</CardAction>
						</CardHeader>
						<CardContent className="relative px-0">
							<div className="relative h-72 overflow-hidden sm:h-80 lg:h-96">
								<AsciiArt
									piece="wireframe-city"
									size={10}
									speed={0.7}
									unconstrained
									className="absolute inset-0 flex items-center justify-center opacity-85"
								/>
								<div className="art-vignette pointer-events-none absolute inset-0" />
								<div className="pointer-events-none absolute top-4 left-4 font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground">
									PL-12 / PL-24
								</div>
								<div className="pointer-events-none absolute top-1/2 left-1/2 h-10 w-px -translate-x-1/2 -translate-y-1/2 bg-border/70" />
								<div className="pointer-events-none absolute top-1/2 left-1/2 h-px w-10 -translate-x-1/2 -translate-y-1/2 bg-border/70" />
								<div className="pointer-events-none absolute right-4 bottom-4 left-4 flex justify-between gap-3 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
									<span>Kraków ↔ Katowice</span>
									<span>Mapbox Standard</span>
								</div>
							</div>
						</CardContent>
						<CardFooter className="flex-wrap justify-between gap-2 border-t px-4 py-3 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground sm:px-5">
							<span>Mapa ofert</span>
							<span>Warstwy kolejowe</span>
							<span>SQLite</span>
						</CardFooter>
					</Card>
				</section>

				<section
					aria-label="Plaszczaki w liczbach"
					className="mt-12 grid divide-y divide-border border-y border-border sm:grid-cols-3 sm:divide-x sm:divide-y-0"
				>
					{SIGNALS.map((signal) => (
						<div
							key={signal.label}
							className="flex flex-col gap-1 px-1 py-6 sm:px-6"
						>
							<span className="font-mono text-2xl tracking-tight">
								{signal.value}
							</span>
							<span className="text-sm font-medium">{signal.label}</span>
							<span className="text-xs text-muted-foreground">
								{signal.detail}
							</span>
						</div>
					))}
				</section>

				<section aria-labelledby="context-heading" className="mt-20">
					<div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
						<div className="flex flex-col gap-2">
							<p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
								01 / kontekst
							</p>
							<h2
								id="context-heading"
								className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl"
							>
								Nie tylko cena. Cały kontekst.
							</h2>
						</div>
						<p className="max-w-md text-sm leading-6 text-muted-foreground">
							Mieszkanie to metraż, ale też czas dojazdu, okolice i to, co
							powstaje obok. Dlatego dane i mapa są tutaj jednym produktem.
						</p>
					</div>

					<div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
						{FEATURES.map((feature) => {
							const Icon = feature.icon;
							return (
								<Card
									key={feature.title}
									className="group gap-0 overflow-hidden border-border/70 bg-card/70 py-0 backdrop-blur-sm"
								>
									<CardContent className="relative h-44 overflow-hidden px-0">
										<AsciiArt
											piece={feature.piece}
											size={feature.size}
											speed={0.8}
											mono
											unconstrained
											className="absolute inset-0 flex items-center justify-center text-muted-foreground/80 opacity-85 transition-opacity duration-500 group-hover:opacity-100 motion-reduce:transition-none"
										/>
										<div className="art-vignette pointer-events-none absolute inset-0" />
										<Badge
											variant="secondary"
											className="absolute top-4 left-4 z-10 bg-card/80 font-mono text-[9px] uppercase tracking-[0.16em] backdrop-blur-sm"
										>
											{feature.eyebrow}
										</Badge>
									</CardContent>
									<CardHeader className="gap-3 px-5 pt-5">
										<div className="flex items-center gap-2 text-muted-foreground">
											<Icon />
											<span className="font-mono text-[10px] uppercase tracking-[0.16em]">
												{feature.eyebrow}
											</span>
										</div>
										<CardTitle>
											<h3 className="text-xl">{feature.title}</h3>
										</CardTitle>
										<CardDescription className="text-sm leading-6">
											{feature.description}
										</CardDescription>
									</CardHeader>
									<CardFooter className="px-5 pb-5">
										<Button asChild variant="outline" size="sm">
											<Link to={feature.to}>
												{feature.cta}
												<ArrowUpRightIcon data-icon="inline-end" />
											</Link>
										</Button>
									</CardFooter>
								</Card>
							);
						})}
					</div>
				</section>

				<Card
					id="jak-to-dziala"
					className="mt-20 scroll-mt-24 gap-0 overflow-hidden border-border/70 bg-card/70 py-0 backdrop-blur-sm"
				>
					<div className="grid lg:grid-cols-[0.9fr_1.1fr]">
						<div className="flex flex-col">
							<CardHeader className="p-6 sm:p-8">
								<Badge
									variant="outline"
									className="font-mono text-[9px] uppercase tracking-[0.16em]"
								>
									02 / przepływ
								</Badge>
								<CardTitle>
									<h2 className="text-2xl tracking-tight sm:text-3xl">
										Od portalu do mapy.
									</h2>
								</CardTitle>
								<CardDescription className="leading-6">
									Jeden powtarzalny proces od pobrania ogłoszenia do jego
									lokalizacji na mapie.
								</CardDescription>
							</CardHeader>
							<CardContent className="px-6 pb-7 sm:px-8">
								<ol className="flex flex-col gap-4">
									{PIPELINE.map((step, index) => (
										<li key={step.title} className="flex gap-3">
											<span className="shrink-0 pt-0.5 font-mono text-[10px] whitespace-nowrap text-muted-foreground">
												{String(index + 1).padStart(2, "0")}
											</span>
											<div className="flex flex-col gap-1">
												<span className="text-sm font-medium">
													{step.title}
												</span>
												<span className="text-sm leading-5 text-muted-foreground">
													{step.description}
												</span>
											</div>
										</li>
									))}
								</ol>
							</CardContent>
						</div>
						<div className="relative min-h-72 overflow-hidden border-t lg:min-h-0 lg:border-t-0 lg:border-l">
							<AsciiArt
								piece="data-flow"
								size={10.5}
								speed={0.8}
								mono
								unconstrained
								className="absolute inset-0 flex items-center justify-center text-muted-foreground/70 opacity-80"
							/>
							<div className="art-vignette pointer-events-none absolute inset-0" />
							<Badge
								variant="secondary"
								className="absolute top-4 left-4 z-10 bg-card/80 font-mono text-[9px] uppercase tracking-[0.16em] backdrop-blur-sm"
							>
								SCHEMAT / ASCII
							</Badge>
							<div className="absolute right-4 bottom-4 left-4 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
								źródła → normalizacja → deduplikacja → SQLite
							</div>
						</div>
					</div>
				</Card>

				<section aria-labelledby="regions-heading" className="mt-20">
					<div className="flex flex-col gap-2">
						<p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
							03 / zasięg
						</p>
						<h2
							id="regions-heading"
							className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl"
						>
							Dwa regiony. Jeden widok.
						</h2>
					</div>
					<div className="mt-8 grid gap-4 sm:grid-cols-2">
						{REGIONS.map((region) => (
							<Card
								key={region.code}
								className="border-border/70 bg-card/70 backdrop-blur-sm"
							>
								<CardHeader>
									<CardAction>
										<Badge variant="outline" className="font-mono text-[10px]">
											{region.code}
										</Badge>
									</CardAction>
									<CardTitle>
										<h3 className="text-xl">{region.name}</h3>
									</CardTitle>
									<CardDescription className="leading-6">
										{region.cities}
									</CardDescription>
								</CardHeader>
								<CardContent className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
									<div className="flex flex-wrap justify-between gap-2">
										{region.coordinates.map((coordinate) => (
											<span key={coordinate}>{coordinate}</span>
										))}
									</div>
								</CardContent>
							</Card>
						))}
					</div>
				</section>

				<Card className="mt-20 flex flex-col gap-6 border-border/70 bg-card/80 backdrop-blur-sm sm:flex-row sm:items-center sm:justify-between">
					<CardHeader className="p-6 sm:p-8">
						<Badge
							variant="secondary"
							className="font-mono text-[9px] uppercase tracking-[0.16em]"
						>
							04 / mapa
						</Badge>
						<CardTitle>
							<h2 className="text-3xl tracking-tight text-balance sm:text-4xl">
								Zobacz miasto na mapie.
							</h2>
						</CardTitle>
						<CardDescription className="max-w-2xl text-base leading-7">
							Oferty, filtry i warstwy kolejowe w jednym interaktywnym widoku.
						</CardDescription>
					</CardHeader>
					<CardFooter className="px-6 pb-6 sm:px-8 sm:pb-8">
						<Button asChild size="lg">
							<Link to="/map">
								Otwórz mapę
								<ArrowRightIcon data-icon="inline-end" />
							</Link>
						</Button>
					</CardFooter>
				</Card>
			</main>
		</>
	);
}

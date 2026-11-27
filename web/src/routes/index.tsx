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
			{ title: "Plaszczaki — dom, mieszkanie lub działka" },
			{
				name: "description",
				content:
					"Przeglądaj domy, mieszkania i działki do kupna lub wynajmu w Małopolsce i na Śląsku. Porównuj oferty, ceny i okolice na mapie.",
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
		value: "01",
		label: "wszystkie oferty",
		detail: "z wielu portali w jednym miejscu",
	},
	{ value: "02", label: "formy transakcji", detail: "kupno i wynajem" },
	{
		value: "03",
		label: "typy nieruchomości",
		detail: "dom, mieszkanie, działka",
	},
];

const FEATURES = [
	{
		piece: "world-map-pings",
		size: 7,
		eyebrow: "MAPA / KUPNO I WYNAJEM",
		title: "Mapa ofert",
		description:
			"Filtruj domy, mieszkania i działki do kupna lub wynajmu. Porównuj ceny i lokalizacje, a potem przejdź do wybranego ogłoszenia.",
		icon: MapIcon,
		cta: "Przeglądaj oferty",
		to: "/map",
	},
	{
		piece: "train-at-dusk",
		size: 6,
		eyebrow: "RANKING / TWOJE KRYTERIA",
		title: "Ranking ofert",
		description:
			"Sam ustaw zakres ofert i wagę cech: metraż, cenę za metr, typ budynku, wysokość sufitów czy odległość od centrum. Wynik przelicza się od razu.",
		icon: ListOrderedIcon,
		cta: "Zobacz propozycje",
		to: "/oferty",
	},
	{
		piece: "train-at-dusk",
		size: 6,
		eyebrow: "KOLEJ / OKOLICA",
		title: "Sprawdź, co jest wokół",
		description:
			"Istniejące i planowane linie kolejowe na tej samej mapie. Oceniaj nieruchomość nie tylko po cenie za metr, ale też po połączeniach i otoczeniu.",
		icon: TrainFrontIcon,
		cta: "Sprawdź okolice",
		to: "/map",
	},
];

const SEARCH_STEPS = [
	{
		title: "Określ, czego szukasz",
		description: "Dom, mieszkanie lub działka — do kupna lub wynajmu.",
	},
	{
		title: "Porównaj oferty",
		description: "Ceny, metraż, lokalizacja i najważniejsze cechy obok siebie.",
	},
	{
		title: "Oceń okolice",
		description:
			"Sprawdź dojazd, połączenia i to, co powstaje wokół nieruchomości.",
	},
];

const REGIONS = [
	{
		code: "Kraków",
		name: "Małopolskie",
		cities: "Kraków · Tarnów · Nowy Sącz · Oświęcim · Zakopane",
	},
	{
		code: "Katowice",
		name: "Śląskie",
		cities: "Katowice · Gliwice · Częstochowa · Bielsko-Biała · Rybnik",
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
							Dom, mieszkanie czy działka?
							<span className="block text-muted-foreground">
								Znajdź swoje miejsce.
							</span>
						</h1>
						<p className="max-w-xl text-base leading-7 text-muted-foreground sm:text-lg">
							Przeglądaj oferty kupna i wynajmu z wielu portali. Porównuj ceny,
							lokalizację, dojazd i otoczenie — bez przeskakiwania między
							serwisami.
						</p>
						<div className="flex flex-wrap gap-3">
							<Button asChild size="lg">
								<Link to="/map">
									<MapIcon data-icon="inline-start" />
									Szukaj na mapie
								</Link>
							</Button>
							<Button asChild variant="outline" size="lg">
								<Link to="/oferty">
									<ListOrderedIcon data-icon="inline-start" />
									Ranking ofert
								</Link>
							</Button>
							<Button asChild variant="outline" size="lg">
								<a href="#jak-szukac">
									Jak szukać
									<ArrowDownIcon data-icon="inline-end" />
								</a>
							</Button>
						</div>
						<div className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
							<span>kupno i wynajem</span>
							<span>domy · mieszkania · działki</span>
							<span>Małopolskie · Śląskie</span>
						</div>
					</div>

					<Card className="relative gap-0 overflow-hidden border-border/70 bg-card/70 py-0 backdrop-blur-sm">
						<CardHeader className="relative z-10 gap-1 border-b bg-card/60 px-4 py-3 backdrop-blur-sm sm:px-5">
							<CardTitle className="font-mono text-[10px] font-normal uppercase tracking-[0.2em] text-muted-foreground">
								MAPA / OFERTY
							</CardTitle>
							<CardDescription className="font-mono text-[10px] tracking-[0.08em]">
								Małopolskie · Śląskie
							</CardDescription>
							<CardAction>
								<Badge
									variant="secondary"
									className="font-mono text-[9px] uppercase tracking-[0.12em]"
								>
									<span className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />
									KUPNO I WYNAJEM
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
									<span>Lokalizacje i ceny</span>
								</div>
							</div>
						</CardContent>
						<CardFooter className="flex-wrap justify-between gap-2 border-t px-4 py-3 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground sm:px-5">
							<span>Domy i mieszkania</span>
							<span>Działki</span>
							<span>Kupno i wynajem</span>
						</CardFooter>
					</Card>
				</section>

				<section
					aria-label="Co możesz znaleźć"
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
							Dom, mieszkanie lub działka to więcej niż cena za metr. Zobacz
							ofertę razem z okolicą, połączeniami i planami rozwoju, żeby
							podejmować decyzję z większą pewnością.
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
					id="jak-szukac"
					className="mt-20 scroll-mt-24 gap-0 overflow-hidden border-border/70 bg-card/70 py-0 backdrop-blur-sm"
				>
					<div className="grid lg:grid-cols-[0.9fr_1.1fr]">
						<div className="flex flex-col">
							<CardHeader className="p-6 sm:p-8">
								<Badge
									variant="outline"
									className="font-mono text-[9px] uppercase tracking-[0.16em]"
								>
									02 / wyszukiwanie
								</Badge>
								<CardTitle>
									<h2 className="text-2xl tracking-tight sm:text-3xl">
										Trzy kroki do właściwego miejsca.
									</h2>
								</CardTitle>
								<CardDescription className="leading-6">
									Od pierwszego filtra aż po sprawdzenie okolicy — wszystko w
									jednym miejscu.
								</CardDescription>
							</CardHeader>
							<CardContent className="px-6 pb-7 sm:px-8">
								<ol className="flex flex-col gap-4">
									{SEARCH_STEPS.map((step, index) => (
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
								piece="world-map-pings"
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
								MAPA / WYSZUKIWANIE
							</Badge>
							<div className="absolute right-4 bottom-4 left-4 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
								wybór → porównanie → mapa
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
							Dwa regiony. Wiele możliwości.
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
									Domy · Mieszkania · Działki
								</CardContent>
							</Card>
						))}
					</div>
				</section>

				<Card className="mt-20 gap-6 border-border/70 bg-card/80 p-6 backdrop-blur-sm sm:flex-row sm:items-center sm:justify-between sm:gap-10 sm:p-8">
					<CardHeader className="min-w-0 flex-1 gap-3 p-0">
						<Badge
							variant="secondary"
							className="w-fit font-mono text-[9px] uppercase tracking-[0.16em]"
						>
							04 / mapa
						</Badge>
						<CardTitle>
							<h2 className="text-2xl tracking-tight text-balance sm:text-3xl">
								Znajdź swoją następną nieruchomość.
							</h2>
						</CardTitle>
						<CardDescription className="max-w-2xl text-sm leading-6 sm:text-base sm:leading-7">
							Przeglądaj domy, mieszkania i działki do kupna lub wynajmu w
							Małopolsce i na Śląsku.
						</CardDescription>
					</CardHeader>
					<CardFooter className="w-full shrink-0 p-0 sm:w-auto">
						<Button asChild size="lg" className="w-full sm:w-auto">
							<Link to="/map">
								Szukaj na mapie
								<ArrowRightIcon data-icon="inline-end" />
							</Link>
						</Button>
					</CardFooter>
				</Card>
			</main>
		</>
	);
}

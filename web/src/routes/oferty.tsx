import { createFileRoute, Link } from "@tanstack/react-router";
import { createServerFn } from "@tanstack/react-start";
import { and, desc, gte, inArray, isNotNull, lt } from "drizzle-orm";
import {
	ArrowLeftIcon,
	ArrowRightIcon,
	AwardIcon,
	BuildingIcon,
	CalendarDaysIcon,
	HouseIcon,
	LandPlotIcon,
	MapIcon,
} from "lucide-react";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
	Card,
	CardContent,
	CardFooter,
	CardHeader,
} from "#/components/ui/card";
import { Spinner } from "#/components/ui/spinner";
import { db } from "#/db";
import { listing } from "#/db/schema";
import type { ScoreProfile } from "#/lib/listing-filter-types";

type TopOffersSearch = {
	date?: string;
};

type TopPropertyType = "flat" | "house" | "plot";

type ScoreReason = {
	code: string;
	label: string;
	points: number;
};

type TopOffer = {
	id: number;
	title: string;
	url: string;
	sourceLabel: string;
	propertyType: TopPropertyType;
	offerType: string;
	pricePln: number | null;
	priceKind: string;
	pricePerM2Pln: number | null;
	areaM2: number | null;
	landAreaM2: number | null;
	rooms: number | null;
	buildYear: number | null;
	city: string | null;
	district: string | null;
	image: string | null;
	score: number;
	scoreProfile: string | null;
	scoreReasons: ScoreReason[];
	firstSeenAt: number;
};

type TopOffersData = {
	date: string;
	previousDate: string;
	nextDate: string;
	isToday: boolean;
	groups: Record<TopPropertyType, TopOffer[]>;
};

const PROPERTY_GROUPS: Array<{
	type: TopPropertyType;
	title: string;
	description: string;
	icon: typeof BuildingIcon;
}> = [
	{
		type: "flat",
		title: "Mieszkania",
		description: "Kamienice i nowe mieszkania",
		icon: BuildingIcon,
	},
	{
		type: "house",
		title: "Domy",
		description: "Klimat i potencjał Airbnb",
		icon: HouseIcon,
	},
	{
		type: "plot",
		title: "Działki",
		description: "Kolej, las, woda i góry",
		icon: LandPlotIcon,
	},
];

const PROFILE_LABELS: Record<ScoreProfile, string> = {
	kamienica: "Kamienica",
	nowe_mieszkanie: "Nowe mieszkanie",
	dzialka_kolej: "Pod kolej",
	dom_airbnb: "Dom pod Airbnb",
	dzialka_airbnb: "Przy naturze",
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function validDate(value: string | undefined): string {
	if (value && DATE_RE.test(value)) {
		const parsed = new Date(`${value}T00:00:00Z`);
		if (
			!Number.isNaN(parsed.getTime()) &&
			parsed.toISOString().slice(0, 10) === value
		) {
			return value;
		}
	}
	return new Date().toISOString().slice(0, 10);
}

function shiftDate(date: string, days: number): string {
	const parsed = new Date(`${date}T00:00:00Z`);
	parsed.setUTCDate(parsed.getUTCDate() + days);
	return parsed.toISOString().slice(0, 10);
}

/** Midnight in Europe/Warsaw for a calendar date, including DST changes. */
function warsawMidnight(date: string): number {
	const utcMidnight = Date.parse(`${date}T00:00:00Z`);
	const parts = new Intl.DateTimeFormat("en-US", {
		timeZone: "Europe/Warsaw",
		timeZoneName: "shortOffset",
	}).formatToParts(new Date(utcMidnight));
	const offset =
		parts.find((part) => part.type === "timeZoneName")?.value ?? "GMT+1";
	const match = /GMT([+-])(\d{1,2})(?::(\d{2}))?/.exec(offset);
	const minutes = match
		? (match[1] === "-" ? -1 : 1) *
			(Number(match[2]) * 60 + Number(match[3] ?? 0))
		: 60;
	return utcMidnight - minutes * 60_000;
}

function formatDate(date: string): string {
	return new Intl.DateTimeFormat("pl-PL", {
		weekday: "long",
		day: "numeric",
		month: "long",
		year: "numeric",
		timeZone: "UTC",
	}).format(new Date(`${date}T00:00:00Z`));
}

function formatPln(value: number): string {
	return new Intl.NumberFormat("pl-PL", {
		style: "currency",
		currency: "PLN",
		maximumFractionDigits: 0,
	}).format(value);
}

const getTopOffers = createServerFn({ method: "GET" })
	.validator((input: TopOffersSearch) => ({
		date: validDate(input?.date),
	}))
	.handler(async ({ data }): Promise<TopOffersData> => {
		const date = validDate(data.date);
		const start = warsawMidnight(date);
		const end = warsawMidnight(shiftDate(date, 1));
		const types: TopPropertyType[] = ["flat", "house", "plot"];

		const rowsByType = await Promise.all(
			types.map(async (propertyType) => {
				const rows = await db
					.select({
						id: listing.id,
						title: listing.title,
						url: listing.url,
						sourceLabel: listing.sourceId,
						propertyType: listing.propertyType,
						offerType: listing.offerType,
						pricePln: listing.pricePln,
						priceKind: listing.priceKind,
						pricePerM2Pln: listing.pricePerM2Pln,
						areaM2: listing.areaM2,
						landAreaM2: listing.landAreaM2,
						rooms: listing.rooms,
						buildYear: listing.buildYear,
						city: listing.city,
						district: listing.district,
						image: listing.images,
						score: listing.score,
						scoreProfile: listing.scoreProfile,
						scoreReasons: listing.scoreReasons,
						firstSeenAt: listing.firstSeenAt,
						canonicalKey: listing.canonicalKey,
					})
					.from(listing)
					.where(
						and(
							inArray(listing.propertyType, [propertyType]),
							inArray(listing.offerType, ["sale", "auction"]),
							gte(listing.firstSeenAt, new Date(start)),
							lt(listing.firstSeenAt, new Date(end)),
							isNotNull(listing.score),
						),
					)
					.orderBy(
						desc(listing.score),
						desc(listing.firstSeenAt),
						desc(listing.id),
					)
					.limit(200);
				const seen = new Set<string>();
				const offers: TopOffer[] = [];
				for (const row of rows) {
					const key = row.canonicalKey ?? `id:${row.id}`;
					if (seen.has(key)) continue;
					seen.add(key);
					offers.push({
						id: row.id,
						title: row.title ?? "Oferta nieruchomości",
						url: row.url,
						sourceLabel: row.sourceLabel,
						propertyType: row.propertyType as TopPropertyType,
						offerType: row.offerType,
						pricePln: row.pricePln,
						priceKind: row.priceKind,
						pricePerM2Pln: row.pricePerM2Pln,
						areaM2: row.areaM2,
						landAreaM2: row.landAreaM2,
						rooms: row.rooms,
						buildYear: row.buildYear,
						city: row.city,
						district: row.district,
						image: row.image?.[0] ?? null,
						score: row.score ?? 0,
						scoreProfile: row.scoreProfile,
						scoreReasons: row.scoreReasons ?? [],
						firstSeenAt: row.firstSeenAt.getTime(),
					});
					if (offers.length === 25) break;
				}
				return offers;
			}),
		);
		const today = new Date().toISOString().slice(0, 10);
		return {
			date,
			previousDate: shiftDate(date, -1),
			nextDate: shiftDate(date, 1),
			isToday: date === today,
			groups: {
				flat: rowsByType[0],
				house: rowsByType[1],
				plot: rowsByType[2],
			},
		};
	});

export const Route = createFileRoute("/oferty")({
	validateSearch: (search: Record<string, unknown>): TopOffersSearch => ({
		date: validDate(typeof search.date === "string" ? search.date : undefined),
	}),
	loaderDeps: ({ search }) => ({ date: search.date }),
	loader: async ({ deps }) => await getTopOffers({ data: { date: deps.date } }),
	pendingComponent: TopOffersPending,
	component: TopOffersPage,
	head: () => ({
		meta: [
			{ title: "Top oferty — Plaszczaki" },
			{
				name: "description",
				content:
					"25 najlepiej ocenionych ofert dodanych każdego dnia: mieszkania, domy i działki w Małopolsce i na Śląsku.",
			},
		],
	}),
});

function TopOffersPending() {
	return (
		<main className="mx-auto flex min-h-[60vh] w-full max-w-[1600px] items-center justify-center px-4">
			<div className="flex items-center gap-3 text-muted-foreground">
				<Spinner />
				Rankingujemy oferty…
			</div>
		</main>
	);
}

function OfferCard({ offer, rank }: { offer: TopOffer; rank: number }) {
	const profile = offer.scoreProfile as ScoreProfile | null;
	const reasons = offer.scoreReasons
		.filter((reason) => reason.points > 0)
		.slice(0, 2);
	const location = [offer.district, offer.city].filter(Boolean).join(" · ");
	const area = offer.propertyType === "plot" ? offer.landAreaM2 : offer.areaM2;
	return (
		<Card className="group gap-0 overflow-hidden border-border/70 bg-card/75 py-0 backdrop-blur-sm transition-colors hover:border-primary/40">
			<CardContent className="relative h-36 overflow-hidden bg-muted p-0">
				{offer.image ? (
					<img
						src={offer.image}
						alt=""
						loading="lazy"
						className="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03] motion-reduce:transition-none"
					/>
				) : (
					<div className="flex size-full items-center justify-center text-muted-foreground">
						<LandPlotIcon className="size-10 opacity-30" />
					</div>
				)}
				<div className="absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-3">
					<Badge className="bg-background/85 font-mono text-[10px] text-foreground backdrop-blur">
						#{String(rank).padStart(2, "0")}
					</Badge>
					<Badge className="gap-1 bg-primary text-primary-foreground">
						<AwardIcon className="size-3" />
						{offer.score}/100
					</Badge>
				</div>
			</CardContent>
			<CardHeader className="gap-2 px-4 pt-4">
				<div className="flex flex-wrap items-center gap-1.5">
					{profile && (
						<Badge variant="secondary">{PROFILE_LABELS[profile]}</Badge>
					)}
					<Badge variant="outline">
						{offer.offerType === "auction" ? "Licytacja" : "Sprzedaż"}
					</Badge>
				</div>
				<a
					href={offer.url}
					target="_blank"
					rel="noreferrer"
					className="line-clamp-2 text-sm leading-5 font-semibold hover:underline"
				>
					{offer.title}
				</a>
				{location && (
					<p className="text-xs text-muted-foreground">{location}</p>
				)}
			</CardHeader>
			<CardContent className="flex flex-col gap-3 px-4">
				<div className="flex items-end justify-between gap-3">
					<div>
						<div className="text-lg font-semibold tracking-tight">
							{offer.pricePln === null
								? "Cena do uzgodnienia"
								: formatPln(offer.pricePln)}
						</div>
						{offer.pricePerM2Pln !== null && (
							<div className="text-xs text-muted-foreground">
								{formatPln(offer.pricePerM2Pln)}/m²
							</div>
						)}
					</div>
					<div className="text-right text-xs text-muted-foreground">
						{area !== null && <div>{area.toLocaleString("pl-PL")} m²</div>}
						{offer.rooms !== null && <div>{offer.rooms} pok.</div>}
						{offer.buildYear !== null && <div>rok {offer.buildYear}</div>}
					</div>
				</div>
				{reasons.length > 0 && (
					<ul className="flex flex-col gap-1 border-t pt-3 text-xs text-muted-foreground">
						{reasons.map((reason) => (
							<li key={reason.code} className="flex gap-1.5">
								<span className="text-primary">+</span>
								<span>{reason.label}</span>
							</li>
						))}
					</ul>
				)}
			</CardContent>
			<CardFooter className="px-4 pt-3 pb-4">
				<Button asChild variant="outline" size="sm" className="w-full">
					<a href={offer.url} target="_blank" rel="noreferrer">
						Otwórz ofertę
						<ArrowRightIcon data-icon="inline-end" />
					</a>
				</Button>
			</CardFooter>
		</Card>
	);
}

function TopOffersPage() {
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
						Ranking atrakcyjności
					</Badge>
					<h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
						Top 25 ofert dziennie
					</h1>
					<p className="max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">
						Najlepiej ocenione oferty dodane tego dnia. Mieszkania, domy i
						działki ma osobne rankingi — zgodnie z Twoimi preferencjami.
					</p>
				</div>
				<div className="flex flex-col gap-3">
					<div className="flex items-center gap-2">
						<Button
							asChild
							variant="outline"
							size="icon"
							aria-label="Poprzedni dzień"
						>
							<Link from="/oferty" search={{ date: data.previousDate }}>
								<ArrowLeftIcon />
							</Link>
						</Button>
						<div className="flex min-w-56 items-center justify-center gap-2 rounded-md border border-input px-3 py-2 text-sm">
							<CalendarDaysIcon className="size-4 text-muted-foreground" />
							<span className="capitalize">{formatDate(data.date)}</span>
						</div>
						<Button
							asChild={!data.isToday}
							variant="outline"
							size="icon"
							aria-label="Następny dzień"
							disabled={data.isToday}
						>
							{data.isToday ? (
								<ArrowRightIcon />
							) : (
								<Link from="/oferty" search={{ date: data.nextDate }}>
									<ArrowRightIcon />
								</Link>
							)}
						</Button>
					</div>
					<div className="flex gap-2">
						{!data.isToday && (
							<Button asChild variant="secondary" size="sm">
								<Link from="/oferty" search={{ date: undefined }}>
									Dzisiaj
								</Link>
							</Button>
						)}
						<Button asChild variant="ghost" size="sm">
							<Link to="/map">
								<MapIcon data-icon="inline-start" />
								Zobacz na mapie
							</Link>
						</Button>
					</div>
				</div>
			</div>

			<div className="mt-8 grid gap-6 xl:grid-cols-3">
				{PROPERTY_GROUPS.map((group) => {
					const Icon = group.icon;
					const offers = data.groups[group.type];
					return (
						<section key={group.type} aria-labelledby={`group-${group.type}`}>
							<header className="mb-4 flex items-center justify-between gap-3">
								<div className="flex items-center gap-3">
									<div className="flex size-10 items-center justify-center rounded-lg border bg-card">
										<Icon className="size-5" />
									</div>
									<div>
										<h2
											id={`group-${group.type}`}
											className="text-xl font-semibold"
										>
											{group.title}
										</h2>
										<p className="text-xs text-muted-foreground">
											{group.description}
										</p>
									</div>
								</div>
								<Badge variant="secondary">{offers.length}/25</Badge>
							</header>
							{offers.length === 0 ? (
								<Card className="border-dashed py-12 text-center">
									<CardContent className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
										<Icon className="size-8 opacity-40" />
										Brak ocenionych ofert dodanych tego dnia.
									</CardContent>
								</Card>
							) : (
								<div className="flex flex-col gap-4">
									{offers.map((offer, index) => (
										<OfferCard key={offer.id} offer={offer} rank={index + 1} />
									))}
								</div>
							)}
						</section>
					);
				})}
			</div>
		</main>
	);
}

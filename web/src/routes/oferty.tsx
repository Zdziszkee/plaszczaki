import { createFileRoute, Link } from "@tanstack/react-router";
import { createServerFn } from "@tanstack/react-start";
import { and, desc, eq, gte, inArray, isNotNull, lt, sql } from "drizzle-orm";
import {
	ArrowLeftIcon,
	ArrowRightIcon,
	AwardIcon,
	BuildingIcon,
	CalendarDaysIcon,
	ChevronDownIcon,
	HouseIcon,
	LandPlotIcon,
	MapIcon,
} from "lucide-react";
import ScoreBreakdown from "#/components/ScoreBreakdown";
import ScoreMethodology from "#/components/ScoreMethodology";
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
import { listing, source } from "#/db/schema";
import type { ScoreProfile } from "#/lib/listing-filter-types";
import { listingScoreExpression } from "#/lib/listing-filters";
import {
	SCORE_PROFILE_SHORT_LABELS,
	type ScoreBreakdown as ScoreBreakdownData,
} from "#/lib/score-types";

type TopOffersSearch = {
	date?: string;
	flatProfile?: ScoreProfile;
	houseProfile?: ScoreProfile;
	plotProfile?: ScoreProfile;
};

type TopPropertyType = "flat" | "house" | "plot";
type ProfileSearchKey = "flatProfile" | "houseProfile" | "plotProfile";

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
	pricePerM2Pln: number | null;
	areaM2: number | null;
	landAreaM2: number | null;
	rooms: number | null;
	buildYear: number | null;
	city: string | null;
	district: string | null;
	image: string | null;
	score: number;
	scoreProfile: ScoreProfile | null;
	scores: Partial<Record<ScoreProfile, number>>;
	scoreReasons: ScoreReason[];
	scoreBreakdown: ScoreBreakdownData | null;
	scoreVersion: string | null;
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
	searchKey: ProfileSearchKey;
	profiles: ScoreProfile[];
	icon: typeof BuildingIcon;
}> = [
	{
		type: "flat",
		title: "Mieszkania",
		description: "Kamienice i nowe mieszkania",
		searchKey: "flatProfile",
		profiles: ["kamienica", "nowe_mieszkanie"],
		icon: BuildingIcon,
	},
	{
		type: "house",
		title: "Domy",
		description: "Airbnb lub dom blisko kolei",
		searchKey: "houseProfile",
		profiles: ["dom_airbnb", "dom_kolej"],
		icon: HouseIcon,
	},
	{
		type: "plot",
		title: "Działki",
		description: "Airbnb lub działka pod kolej",
		searchKey: "plotProfile",
		profiles: ["dzialka_airbnb", "dzialka_kolej"],
		icon: LandPlotIcon,
	},
];

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function todayInWarsaw(): string {
	const parts = new Intl.DateTimeFormat("en-US", {
		timeZone: "Europe/Warsaw",
		year: "numeric",
		month: "2-digit",
		day: "2-digit",
	}).formatToParts(new Date());
	const value = (type: Intl.DateTimeFormatPartTypes) =>
		parts.find((part) => part.type === type)?.value ?? "";
	return `${value("year")}-${value("month")}-${value("day")}`;
}

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
	return todayInWarsaw();
}

function validProfile(
	value: unknown,
	profiles: ScoreProfile[],
): ScoreProfile | undefined {
	return typeof value === "string" && profiles.includes(value as ScoreProfile)
		? (value as ScoreProfile)
		: undefined;
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
	}).formatToParts(utcMidnight);
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

function updateProfileSearch(
	previous: TopOffersSearch,
	key: ProfileSearchKey,
	value: ScoreProfile | undefined,
): TopOffersSearch {
	return { ...previous, [key]: value };
}

const getTopOffers = createServerFn({ method: "GET" })
	.validator((input: TopOffersSearch) => ({
		date: validDate(input?.date),
		flatProfile: validProfile(input?.flatProfile, [
			"kamienica",
			"nowe_mieszkanie",
		]),
		houseProfile: validProfile(input?.houseProfile, [
			"dom_airbnb",
			"dom_kolej",
		]),
		plotProfile: validProfile(input?.plotProfile, [
			"dzialka_airbnb",
			"dzialka_kolej",
		]),
	}))
	.handler(async ({ data }): Promise<TopOffersData> => {
		const date = validDate(data.date);
		const start = warsawMidnight(date);
		const end = warsawMidnight(shiftDate(date, 1));
		const types: TopPropertyType[] = ["flat", "house", "plot"];
		const selections: Record<TopPropertyType, ScoreProfile | undefined> = {
			flat: data.flatProfile,
			house: data.houseProfile,
			plot: data.plotProfile,
		};

		const rowsByType = await Promise.all(
			types.map(async (propertyType) => {
				const selectedProfile = selections[propertyType];
				const effectiveDiscount = sql<number>`coalesce(json_extract(${listing.scoreBreakdown}, '$.priceEvidence.effectiveDiscount'), -1)`;
				const confidenceRank = sql<number>`case json_extract(${listing.scoreBreakdown}, '$.priceEvidence.confidence')
					when 'high' then 3
					when 'medium' then 2
					when 'low' then 1
					else 0
				end`;
				const conditions = [
					inArray(listing.propertyType, [propertyType]),
					inArray(listing.offerType, ["sale", "auction"]),
					gte(listing.firstSeenAt, new Date(start)),
					lt(listing.firstSeenAt, new Date(end)),
					isNotNull(listing.score),
					sql`json_extract(${listing.scoreBreakdown}, '$.priceEvidence.topEligible') = 1`,
				];
				let order = desc(listing.score);

				if (selectedProfile) {
					const eligiblePath = `$.profiles.${selectedProfile}.eligible`;
					const selectedScore = listingScoreExpression(selectedProfile);
					conditions.push(
						isNotNull(selectedScore),
						sql`json_extract(${listing.scoreBreakdown}, ${eligiblePath}) = 1`,
					);
					order = desc(selectedScore);
				}

				const candidates = await db
					.select({
						id: listing.id,
						canonicalKey: listing.canonicalKey,
					})
					.from(listing)
					.where(and(...conditions))
					.orderBy(
						order,
						desc(effectiveDiscount),
						desc(confidenceRank),
						desc(listing.firstSeenAt),
						desc(listing.id),
					);

				const seen = new Set<string>();
				const rankedIds: number[] = [];
				for (const candidate of candidates) {
					const key = candidate.canonicalKey ?? `id:${candidate.id}`;
					if (seen.has(key)) continue;
					seen.add(key);
					rankedIds.push(candidate.id);
					if (rankedIds.length === 25) break;
				}
				if (rankedIds.length === 0) return [];

				const rows = await db
					.select({
						id: listing.id,
						title: listing.title,
						url: listing.url,
						sourceLabel: source.label,
						propertyType: listing.propertyType,
						offerType: listing.offerType,
						pricePln: listing.pricePln,
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
						scores: listing.scores,
						scoreReasons: listing.scoreReasons,
						scoreBreakdown: listing.scoreBreakdown,
						scoreVersion: listing.scoreVersion,
					})
					.from(listing)
					.innerJoin(source, eq(source.id, listing.sourceId))
					.where(inArray(listing.id, rankedIds));
				const rowsById = new Map(rows.map((row) => [row.id, row]));

				return rankedIds.flatMap((id) => {
					const row = rowsById.get(id);
					if (!row) return [];
					const winnerProfile = row.scoreProfile as ScoreProfile | null;
					const profile = selectedProfile ?? winnerProfile;
					const score = selectedProfile
						? (row.scores?.[selectedProfile] ?? 0)
						: (row.score ?? 0);
					return [
						{
							id: row.id,
							title: row.title ?? "Oferta nieruchomości",
							url: row.url,
							sourceLabel: row.sourceLabel,
							propertyType: row.propertyType as TopPropertyType,
							offerType: row.offerType,
							pricePln: row.pricePln,
							pricePerM2Pln: row.pricePerM2Pln,
							areaM2: row.areaM2,
							landAreaM2: row.landAreaM2,
							rooms: row.rooms,
							buildYear: row.buildYear,
							city: row.city,
							district: row.district,
							image: row.image?.[0] ?? null,
							score,
							scoreProfile: profile,
							scores: row.scores ?? {},
							scoreReasons: row.scoreReasons ?? [],
							scoreBreakdown: row.scoreBreakdown,
							scoreVersion: row.scoreVersion,
						},
					];
				});
			}),
		);
		const today = todayInWarsaw();
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
		flatProfile: validProfile(search.flatProfile, [
			"kamienica",
			"nowe_mieszkanie",
		]),
		houseProfile: validProfile(search.houseProfile, [
			"dom_airbnb",
			"dom_kolej",
		]),
		plotProfile: validProfile(search.plotProfile, [
			"dzialka_airbnb",
			"dzialka_kolej",
		]),
	}),
	loaderDeps: ({ search }) => ({
		date: search.date,
		flatProfile: search.flatProfile,
		houseProfile: search.houseProfile,
		plotProfile: search.plotProfile,
	}),
	loader: async ({ deps }) => await getTopOffers({ data: deps }),
	pendingComponent: TopOffersPending,
	component: TopOffersPage,
	head: () => ({
		meta: [
			{ title: "Top oferty — Plaszczaki" },
			{
				name: "description",
				content:
					"25 najlepiej ocenionych ofert dodanych każdego dnia: mieszkania, domy i działki w Małopolsce i na Śląsku, z pełnym wyjaśnieniem punktów.",
			},
		],
	}),
});

function TopOffersPending() {
	return (
		<main className="mx-auto flex min-h-[60vh] w-full max-w-[1600px] items-center justify-center px-4">
			<div className="flex items-center gap-3 text-muted-foreground">
				<Spinner />
				Rankujemy oferty…
			</div>
		</main>
	);
}

function OfferCard({ offer, rank }: { offer: TopOffer; rank: number }) {
	const group = PROPERTY_GROUPS.find(
		(item) => item.type === offer.propertyType,
	);
	const breakdown = offer.scoreProfile
		? offer.scoreBreakdown?.profiles[offer.scoreProfile]
		: null;
	const reasons = (breakdown?.contributions ?? [])
		.filter((reason) => reason.points > 0)
		.slice(0, 3);
	const location = [offer.district, offer.city].filter(Boolean).join(" · ");
	const area = offer.propertyType === "plot" ? offer.landAreaM2 : offer.areaM2;
	const FallbackIcon =
		offer.propertyType === "house"
			? HouseIcon
			: offer.propertyType === "flat"
				? BuildingIcon
				: LandPlotIcon;
	const alternativeProfiles =
		group?.profiles.filter((profile) => profile !== offer.scoreProfile) ?? [];

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
						<FallbackIcon className="size-10 opacity-30" />
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
					{offer.scoreProfile && (
						<Badge variant="secondary">
							{SCORE_PROFILE_SHORT_LABELS[offer.scoreProfile]}
						</Badge>
					)}
					{alternativeProfiles.map((profile) => (
						<Badge key={profile} variant="outline">
							{SCORE_PROFILE_SHORT_LABELS[profile]} {offer.scores[profile] ?? 0}
						</Badge>
					))}
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
					<ul className="flex flex-col gap-1.5 border-t pt-3 text-xs text-muted-foreground">
						{reasons.map((reason) => (
							<li
								key={reason.code}
								className="flex items-start justify-between gap-2"
							>
								<span>{reason.label}</span>
								<span className="shrink-0 font-mono text-foreground">
									+{reason.points.toFixed(1).replace(".", ",")}
								</span>
							</li>
						))}
					</ul>
				)}
				<ScoreBreakdown
					breakdown={breakdown}
					score={offer.score}
					priceEvidence={offer.scoreBreakdown?.priceEvidence}
				/>
			</CardContent>
			<CardFooter className="flex-wrap justify-between gap-2 px-4 pt-3 pb-4">
				<span className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
					{offer.sourceLabel}
					{offer.scoreVersion ? ` · model ${offer.scoreVersion}` : ""}
				</span>
				<Button asChild variant="outline" size="sm">
					<a href={offer.url} target="_blank" rel="noreferrer">
						Otwórz ofertę
						<ArrowRightIcon data-icon="inline-end" />
					</a>
				</Button>
			</CardFooter>
		</Card>
	);
}

function ProfileSwitch({ group }: { group: (typeof PROPERTY_GROUPS)[number] }) {
	const search = Route.useSearch();
	const selected = search[group.searchKey];
	const options: Array<ScoreProfile | undefined> = [
		undefined,
		...group.profiles,
	];
	return (
		<nav
			aria-label={`Profile: ${group.title}`}
			className="mb-4 flex flex-wrap gap-1"
		>
			{options.map((profile) => {
				const active = selected === profile;
				return (
					<Button
						key={profile ?? "best"}
						asChild
						variant={active ? "secondary" : "ghost"}
						size="sm"
					>
						<Link
							from="/oferty"
							search={(previous) =>
								updateProfileSearch(previous, group.searchKey, profile)
							}
							activeOptions={{ exact: true }}
							aria-current={active ? "page" : undefined}
						>
							{profile ? SCORE_PROFILE_SHORT_LABELS[profile] : "Najlepszy"}
						</Link>
					</Button>
				);
			})}
		</nav>
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
						Najlepiej ocenione oferty dodane tego dnia, z potwierdzoną przewagą
						cenową nad podobnymi ofertami lub transakcjami RCN. Mieszkania, domy
						i działki mają osobne rankingi. Domy i działki możesz oceniać pod
						Airbnb albo pod bliskość stacji kolejowej.
					</p>
					<Button asChild variant="link" className="w-fit px-0">
						<a href="#jak-liczymy">
							Jak policzono punkty?
							<ChevronDownIcon data-icon="inline-end" />
						</a>
					</Button>
				</div>
				<div className="flex flex-col gap-3">
					<div className="flex items-center gap-2">
						<Button
							asChild
							variant="outline"
							size="icon"
							aria-label="Poprzedni dzień"
						>
							<Link
								from="/oferty"
								search={(previous) => ({
									...previous,
									date: data.previousDate,
								})}
							>
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
								<Link
									from="/oferty"
									search={(previous) => ({ ...previous, date: data.nextDate })}
								>
									<ArrowRightIcon />
								</Link>
							)}
						</Button>
					</div>
					<div className="flex gap-2">
						{!data.isToday && (
							<Button asChild variant="secondary" size="sm">
								<Link
									from="/oferty"
									search={(previous) => ({ ...previous, date: undefined })}
								>
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
							<ProfileSwitch group={group} />
							{offers.length === 0 ? (
								<Card className="border-dashed py-12 text-center">
									<CardContent className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
										<Icon className="size-8 opacity-40" />
										Brak ofert spełniających profil i wymagania cenowe.
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

			<ScoreMethodology />
		</main>
	);
}

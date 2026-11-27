import { ChevronDownIcon } from "lucide-react";
import { Badge } from "#/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "#/components/ui/collapsible";
import {
	formatScorePoints,
	type PriceEvidence,
	type ScoreCategoryCode,
	type ScoreProfileBreakdown,
} from "#/lib/score-types";

const CONFIDENCE_LABELS: Record<
	Exclude<PriceEvidence["confidence"], null>,
	string
> = {
	high: "wysoka",
	medium: "średnia",
	low: "niska",
};

function formatPln(value: number): string {
	return new Intl.NumberFormat("pl-PL", {
		style: "currency",
		currency: "PLN",
		maximumFractionDigits: 0,
	}).format(value);
}

function formatDiscount(value: number): string {
	const percent = Math.round(Math.abs(value) * 100);
	return `${percent}% ${value >= 0 ? "poniżej" : "powyżej"}`;
}

const CATEGORY_ORDER: readonly ScoreCategoryCode[] = [
	"price",
	"location",
	"attributes",
	"nature",
	"opportunity",
] as const;

function ContributionRow({
	label,
	points,
	maxPoints,
	negative = false,
	zero = false,
}: {
	label: string;
	points: number;
	maxPoints: number;
	negative?: boolean;
	zero?: boolean;
}) {
	const sign = zero ? "" : negative ? "−" : "+";

	return (
		<li className="flex items-start justify-between gap-3 text-xs leading-5">
			<span className="text-muted-foreground">{label}</span>
			<span
				className={
					negative
						? "shrink-0 font-mono font-medium text-destructive"
						: "shrink-0 font-mono font-medium text-foreground"
				}
			>
				{sign}
				{formatScorePoints(Math.abs(points))}
				<span className="text-muted-foreground">
					{" / "}
					{formatScorePoints(maxPoints)}
				</span>
			</span>
		</li>
	);
}

export default function ScoreBreakdown({
	breakdown,
	score,
	priceEvidence,
	open,
	onOpenChange,
	contentId,
}: {
	breakdown: ScoreProfileBreakdown | null | undefined;
	score: number;
	priceEvidence?: PriceEvidence | null;
	open?: boolean;
	onOpenChange?: (open: boolean) => void;
	contentId?: string;
}) {
	if (!breakdown) {
		return (
			<div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
				Pełne uzasadnienie pojawi się po przeliczeniu wyniku.
			</div>
		);
	}

	const categories = [...breakdown.categories].sort(
		(a, b) => CATEGORY_ORDER.indexOf(a.code) - CATEGORY_ORDER.indexOf(b.code),
	);
	const contributions = [...breakdown.contributions].sort((a, b) => {
		const categoryDifference =
			CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category);
		return categoryDifference || b.points - a.points;
	});
	const capped = breakdown.cap !== null && breakdown.rawScore > breakdown.cap;

	return (
		<Collapsible className="border-t" open={open} onOpenChange={onOpenChange}>
			<CollapsibleTrigger className="group flex w-full items-center justify-between gap-3 py-3 text-left text-xs font-medium">
				<span>Dlaczego {score}/100?</span>
				<ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none" />
			</CollapsibleTrigger>
			<CollapsibleContent id={contentId} className="flex flex-col gap-4 pb-4">
				{breakdown.eligible === false && (
					<Badge variant="destructive">Profil nie spełnia wymagań</Badge>
				)}
				{breakdown.eligible === null && (
					<Badge variant="outline">Wymagania do potwierdzenia</Badge>
				)}

				{priceEvidence && (
					<div className="flex flex-col gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs">
						<div className="flex items-center justify-between gap-3">
							<p className="font-medium">Porównanie ceny</p>
							{priceEvidence.confidence && (
								<span className="text-muted-foreground">
									pewność: {CONFIDENCE_LABELS[priceEvidence.confidence]}
								</span>
							)}
						</div>
						{priceEvidence.listing && (
							<div className="flex justify-between gap-3 text-muted-foreground">
								<span>
									Oferty w promieniu {priceEvidence.listing.radiusKm} km (n=
									{priceEvidence.listing.count})
								</span>
								<span className="shrink-0 font-mono text-foreground">
									{formatPln(priceEvidence.listing.median)}/m²
								</span>
							</div>
						)}
						{priceEvidence.rcn && (
							<div className="flex justify-between gap-3 text-muted-foreground">
								<span>Transakcje RCN (n={priceEvidence.rcn.count})</span>
								<span className="shrink-0 font-mono text-foreground">
									{formatPln(priceEvidence.rcn.median)}/m²
								</span>
							</div>
						)}
						{priceEvidence.effectiveDiscount !== null && (
							<div className="flex justify-between gap-3 border-t pt-2">
								<span className="text-muted-foreground">
									Użyta przewaga cenowa
								</span>
								<span className="shrink-0 font-mono font-medium">
									{formatDiscount(priceEvidence.effectiveDiscount)}
								</span>
							</div>
						)}
						{priceEvidence.rcn && (
							<p className="text-[10px] leading-4 text-muted-foreground">
								Wykorzystano/opracowano na podstawie materiałów państwowego
								zasobu geodezyjnego i kartograficznego
							</p>
						)}
					</div>
				)}

				<p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
					Punkty według kategorii
				</p>
				<ul className="flex flex-col gap-4">
					{categories.map((category) => {
						const categoryContributions = contributions.filter(
							(reason) => reason.category === category.code,
						);
						return (
							<li key={category.code} className="flex flex-col gap-1.5">
								<div className="flex items-center justify-between gap-3 text-xs">
									<span className="font-medium">{category.label}</span>
									<span className="font-mono text-muted-foreground">
										{formatScorePoints(category.points)} / {category.maxPoints}
									</span>
								</div>
								<progress
									value={Math.max(0, category.points)}
									max={Math.max(1, category.maxPoints)}
									aria-label={`${category.label}: ${formatScorePoints(category.points)} z ${category.maxPoints} punktów`}
									className="h-1.5 w-full appearance-none overflow-hidden rounded-full bg-muted [&::-moz-progress-bar]:rounded-full [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-primary"
								/>
								{categoryContributions.length > 0 && (
									<ul className="ml-3 flex flex-col gap-1.5 border-l pl-3">
										{categoryContributions.map((reason) => (
											<ContributionRow
												key={reason.code}
												label={reason.label}
												points={reason.points}
												maxPoints={reason.maxPoints}
											/>
										))}
									</ul>
								)}
							</li>
						);
					})}
				</ul>

				{breakdown.penalties.length > 0 && (
					<div className="flex flex-col gap-2">
						<p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
							Kary
						</p>
						<ul className="flex flex-col gap-1.5">
							{breakdown.penalties.map((reason) => (
								<ContributionRow
									key={reason.code}
									label={reason.label}
									points={reason.points}
									maxPoints={reason.maxPoints}
									negative
								/>
							))}
						</ul>
					</div>
				)}

				{breakdown.zero.length > 0 && (
					<div className="flex flex-col gap-2">
						<p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
							Bez punktów
						</p>
						<ul className="flex flex-col gap-1.5">
							{breakdown.zero.map((reason) => (
								<ContributionRow
									key={reason.code}
									label={reason.label}
									points={0}
									maxPoints={reason.maxPoints}
									zero
								/>
							))}
						</ul>
					</div>
				)}

				<dl className="flex flex-col gap-1.5 border-t pt-3 text-xs">
					<div className="flex justify-between gap-3">
						<dt className="text-muted-foreground">Suma surowa</dt>
						<dd className="font-mono">
							{formatScorePoints(breakdown.rawScore)}
						</dd>
					</div>
					{capped && (
						<div className="flex justify-between gap-3">
							<dt className="text-muted-foreground">
								Limit{breakdown.capLabel ? `: ${breakdown.capLabel}` : ""}
							</dt>
							<dd className="font-mono">{breakdown.cap}</dd>
						</div>
					)}
					<div className="flex justify-between gap-3 font-semibold">
						<dt>Wynik</dt>
						<dd className="font-mono">{breakdown.score}/100</dd>
					</div>
				</dl>

				{breakdown.missing.length > 0 && (
					<div className="flex flex-col gap-1.5 text-xs text-muted-foreground">
						<p className="font-medium">Brakujące dane</p>
						<ul className="list-disc pl-4">
							{breakdown.missing.map((item) => (
								<li key={item}>{item}</li>
							))}
						</ul>
					</div>
				)}
			</CollapsibleContent>
		</Collapsible>
	);
}

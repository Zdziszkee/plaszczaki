import { ChevronDownIcon } from "lucide-react";
import { Badge } from "#/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "#/components/ui/collapsible";
import {
	formatScorePoints,
	type ScoreCategoryCode,
	type ScoreProfileBreakdown,
} from "#/lib/score-types";

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
	negative = false,
}: {
	label: string;
	points: number;
	negative?: boolean;
}) {
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
				{negative ? "−" : "+"}
				{formatScorePoints(Math.abs(points))}
			</span>
		</li>
	);
}

export default function ScoreBreakdown({
	breakdown,
	score,
}: {
	breakdown: ScoreProfileBreakdown | null | undefined;
	score: number;
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
		<Collapsible className="border-t">
			<CollapsibleTrigger className="group flex w-full items-center justify-between gap-3 py-3 text-left text-xs font-medium">
				<span>Dlaczego {score} pkt?</span>
				<ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none" />
			</CollapsibleTrigger>
			<CollapsibleContent className="flex flex-col gap-4 pb-4">
				{breakdown.eligible === false && (
					<Badge variant="destructive">Profil nie spełnia wymagań</Badge>
				)}
				{breakdown.eligible === null && (
					<Badge variant="outline">Wymagania do potwierdzenia</Badge>
				)}

				<ul className="flex flex-col gap-3">
					{categories.map((category) => (
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
						</li>
					))}
				</ul>

				{contributions.length > 0 && (
					<div className="flex flex-col gap-2">
						<p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
							Punkty w szczegółach
						</p>
						<ul className="flex flex-col gap-1.5">
							{contributions.map((reason) => (
								<ContributionRow
									key={reason.code}
									label={reason.label}
									points={reason.points}
								/>
							))}
						</ul>
					</div>
				)}

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

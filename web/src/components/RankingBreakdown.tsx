import { ChevronDownIcon } from "lucide-react";
import { Badge } from "#/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "#/components/ui/collapsible";
import type { PriceEvidence, RankingBreakdown } from "#/lib/ranking-types";

function points(value: number): string {
	return new Intl.NumberFormat("pl-PL", {
		minimumFractionDigits: 1,
		maximumFractionDigits: 1,
	}).format(value);
}

export default function RankingBreakdownView({
	breakdown,
	score,
	priceEvidence,
	defaultOpen = false,
}: {
	breakdown: RankingBreakdown;
	score: number;
	priceEvidence?: PriceEvidence | null;
	defaultOpen?: boolean;
}) {
	const positive = breakdown.contributions.filter((item) => item.points > 0);
	const penalties = breakdown.penalties;
	return (
		<Collapsible defaultOpen={defaultOpen} className="border-t">
			<CollapsibleTrigger className="group flex w-full items-center justify-between gap-3 py-3 text-left text-xs font-medium">
				<span>Dlaczego {score}/100?</span>
				<ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none" />
			</CollapsibleTrigger>
			<CollapsibleContent className="flex flex-col gap-4 pb-4">
				{breakdown.eligible === false && (
					<Badge variant="destructive">Profil nie spełnia wymagań</Badge>
				)}
				{priceEvidence && (
					<div className="flex flex-col gap-1 rounded-lg border bg-muted/30 px-3 py-2 text-[11px]">
						<div className="flex items-center justify-between gap-3">
							<span className="font-medium">Porównanie ceny</span>
							{priceEvidence.confidence && (
								<span className="text-muted-foreground">
									pewność: {priceEvidence.confidence}
								</span>
							)}
						</div>
						{priceEvidence.benchmark !== null && (
							<div className="flex justify-between gap-3 text-muted-foreground">
								<span>Referencja</span>
								<span className="font-mono text-foreground">
									{points(priceEvidence.benchmark)} zł/m²
								</span>
							</div>
						)}
						{priceEvidence.effectiveDiscount !== null && (
							<div className="flex justify-between gap-3 text-muted-foreground">
								<span>Przewaga cenowa</span>
								<span className="font-mono text-foreground">
									{(priceEvidence.effectiveDiscount * 100).toLocaleString(
										"pl-PL",
										{
											maximumFractionDigits: 1,
										},
									)}
									%
								</span>
							</div>
						)}
					</div>
				)}
				<ul className="flex flex-col gap-3">
					{breakdown.categories.map((category) => (
						<li key={category.code} className="flex flex-col gap-1.5">
							<div className="flex items-center justify-between gap-3 text-xs">
								<span className="font-medium">{category.label}</span>
								<span className="font-mono text-muted-foreground">
									{points(category.points)} / {points(category.maxPoints)}
								</span>
							</div>
							<progress
								value={Math.max(0, category.points)}
								max={Math.max(1, category.maxPoints)}
								aria-label={`${category.label}: ${points(category.points)} z ${points(category.maxPoints)} punktów`}
								className="h-1.5 w-full appearance-none overflow-hidden rounded-full bg-muted [&::-moz-progress-bar]:rounded-full [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-primary"
							/>
							<ul className="ml-3 flex flex-col gap-1 border-l pl-3">
								{positive
									.filter((item) => item.category === category.code)
									.map((item) => (
										<li
											key={item.code}
											className="flex items-start justify-between gap-3 text-[11px] leading-4"
										>
											<span className="text-muted-foreground">
												{item.label}
											</span>
											<span className="shrink-0 font-mono">
												+{points(item.points)}
												<span className="text-muted-foreground">
													{" / "}
													{points(item.maxPoints)}
												</span>
											</span>
										</li>
									))}
							</ul>
						</li>
					))}
				</ul>
				{penalties.length > 0 && (
					<div className="flex flex-col gap-1.5">
						<p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
							Kary
						</p>
						<ul className="flex flex-col gap-1">
							{penalties.map((item) => (
								<li
									key={item.code}
									className="flex items-center justify-between gap-3 text-[11px]"
								>
									<span className="text-muted-foreground">{item.label}</span>
									<span className="font-mono text-destructive">
										−{points(Math.abs(item.points))}
									</span>
								</li>
							))}
						</ul>
					</div>
				)}
				<dl className="flex flex-col gap-1.5 border-t pt-3 text-xs">
					<div className="flex justify-between gap-3">
						<dt className="text-muted-foreground">Suma surowa</dt>
						<dd className="font-mono">{points(breakdown.rawScore)}</dd>
					</div>
					{breakdown.cap !== null && (
						<div className="flex justify-between gap-3">
							<dt className="text-muted-foreground">
								Limit{breakdown.capLabel ? `: ${breakdown.capLabel}` : ""}
							</dt>
							<dd className="font-mono">{breakdown.cap}</dd>
						</div>
					)}
					<div className="flex justify-between gap-3 font-semibold">
						<dt>Wynik</dt>
						<dd className="font-mono">{score}/100</dd>
					</div>
				</dl>
				{breakdown.missing.length > 0 && (
					<div className="flex flex-col gap-1 text-[11px] text-muted-foreground">
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

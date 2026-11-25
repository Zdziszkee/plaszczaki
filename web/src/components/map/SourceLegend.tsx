import { useState } from "react";
import { Button } from "#/components/ui/button";
import type { ListingsStats } from "./useListingsStats";

const COLLAPSED_SOURCES = 8;

export default function SourceLegend({
	stats,
}: {
	stats: ListingsStats | null;
}) {
	const [expanded, setExpanded] = useState(false);

	const positiveSources =
		stats?.bySource.filter((source) => source.count > 0) ?? [];
	// Prefer sources that currently have offers; fall back to the full list
	// when every counter is zero so the legend still names the portals.
	const sources =
		positiveSources.length > 0 ? positiveSources : (stats?.bySource ?? []);
	const visibleSources = expanded
		? sources
		: sources.slice(0, COLLAPSED_SOURCES);

	return (
		<div className="flex flex-col gap-1.5 border-t pt-3">
			<div className="flex items-center justify-between text-xs">
				<span className="font-medium text-muted-foreground">
					Legenda źródeł
				</span>
				{stats !== null && (
					<span className="text-[10px] text-muted-foreground tabular-nums">
						źródeł: {positiveSources.length}
					</span>
				)}
			</div>
			{stats === null ? (
				<span className="text-xs text-muted-foreground">Ładowanie…</span>
			) : sources.length === 0 ? (
				<span className="text-xs text-muted-foreground">
					Brak ofert w źródłach
				</span>
			) : (
				<>
					<div className="flex max-h-44 flex-col gap-1 overflow-y-auto">
						{visibleSources.map((source) => (
							<div
								key={source.id}
								className="flex items-center gap-1.5 text-xs"
							>
								<span
									className="size-2 shrink-0 rounded-full"
									style={{ backgroundColor: source.color }}
								/>
								<span className="min-w-0 flex-1 truncate">{source.label}</span>
								<span className="shrink-0 text-muted-foreground tabular-nums">
									{source.count.toLocaleString("pl-PL")}
								</span>
							</div>
						))}
					</div>
					{sources.length > COLLAPSED_SOURCES && (
						<Button
							variant="ghost"
							size="xs"
							className="self-start"
							onClick={() => setExpanded((value) => !value)}
						>
							{expanded ? "Zwiń" : `Pokaż wszystkie (${sources.length})`}
						</Button>
					)}
				</>
			)}
		</div>
	);
}

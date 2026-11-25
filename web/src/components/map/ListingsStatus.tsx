import { RefreshCwIcon } from "lucide-react";
import { Button } from "#/components/ui/button";
import { Spinner } from "#/components/ui/spinner";
import { cn } from "#/lib/utils";
import type { ListingsMode } from "./listings-types";

export default function ListingsStatus({
	enabled,
	loading,
	total,
	totalAll,
	mode,
	error,
	onRetry,
	onRefresh,
}: {
	enabled: boolean;
	loading: boolean;
	total: number;
	totalAll: number;
	mode: ListingsMode;
	error: string | null;
	onRetry: () => void;
	onRefresh: () => void;
}) {
	if (!enabled) return null;

	const showTotalAll = !(totalAll === 0 && loading);

	return (
		<div className="pointer-events-auto absolute bottom-16 left-4 z-20 flex items-center gap-2 rounded-md border bg-card/90 px-3 py-2 text-xs shadow-lg backdrop-blur">
			{loading ? (
				<Spinner className="size-3.5" />
			) : (
				<span
					className={cn(
						"size-2 rounded-full",
						error
							? "bg-destructive"
							: total > 0
								? "bg-emerald-400"
								: "bg-muted-foreground",
					)}
				/>
			)}
			{error ? (
				<>
					<span className="text-destructive">Błąd wczytywania ofert</span>
					<Button variant="ghost" size="xs" onClick={onRetry}>
						Ponów
					</Button>
				</>
			) : (
				<>
					{showTotalAll && (
						<>
							<span className="font-medium">
								{totalAll.toLocaleString("pl-PL")} ofert łącznie
							</span>
							<span className="text-muted-foreground">·</span>
						</>
					)}
					<span>{total.toLocaleString("pl-PL")} w widoku</span>
					{mode === "clusters" && total > 0 ? (
						<span className="text-muted-foreground">· zgrupowane</span>
					) : null}
				</>
			)}
			<Button
				variant="ghost"
				size="icon-xs"
				onClick={onRefresh}
				aria-label="Odśwież oferty"
			>
				<RefreshCwIcon className={loading ? "animate-spin" : undefined} />
			</Button>
		</div>
	);
}

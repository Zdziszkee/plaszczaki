import { Badge } from "#/components/ui/badge";
import { Separator } from "#/components/ui/separator";

export default function Footer() {
	const year = new Date().getFullYear();
	return (
		<footer className="mt-20 px-4 pb-14">
			<Separator />
			<div className="mx-auto flex w-full max-w-5xl flex-col items-center justify-between gap-4 pt-10 text-center sm:flex-row sm:text-left">
				<p className="text-sm text-muted-foreground">
					&copy; {year} Plaszczaki. Oferty z wielu portali, jeden kontekst.
				</p>
				<Badge variant="outline">Małopolskie · Śląskie</Badge>
			</div>
		</footer>
	);
}

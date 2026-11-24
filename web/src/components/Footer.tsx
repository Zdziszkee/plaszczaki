import { GithubIcon, TwitterIcon } from "lucide-react";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { Separator } from "#/components/ui/separator";

export default function Footer() {
	const year = new Date().getFullYear();

	return (
		<footer className="mt-20 px-4 pb-14">
			<Separator />
			<div className="mx-auto flex w-full max-w-5xl flex-col items-center justify-between gap-4 pt-10 text-center sm:flex-row sm:text-left">
				<p className="text-sm text-muted-foreground">
					&copy; {year} Plaszczaki. All rights reserved.
				</p>
				<Badge variant="outline">Built with TanStack Start</Badge>
			</div>
			<div className="mt-4 flex justify-center gap-1">
				<Button asChild variant="ghost" size="icon">
					<a href="https://x.com/tan_stack" target="_blank" rel="noreferrer">
						<span className="sr-only">Follow TanStack on X</span>
						<TwitterIcon />
					</a>
				</Button>
				<Button asChild variant="ghost" size="icon">
					<a
						href="https://github.com/TanStack"
						target="_blank"
						rel="noreferrer"
					>
						<span className="sr-only">Go to TanStack GitHub</span>
						<GithubIcon />
					</a>
				</Button>
			</div>
		</footer>
	);
}

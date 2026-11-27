import { Link } from "@tanstack/react-router";
import { ListOrderedIcon, MapIcon } from "lucide-react";
import { Button } from "#/components/ui/button";
import ThemeToggle from "./ThemeToggle";

const NAV_LINKS = [
	{ to: "/", label: "Start", exact: true },
	{ to: "/map", label: "Mapa", exact: false },
	{ to: "/oferty", label: "Ranking ofert", exact: false },
] as const;

export default function Header() {
	return (
		<header className="sticky top-0 z-50 border-b bg-background/80 px-4 backdrop-blur-lg">
			<nav className="mx-auto flex w-full max-w-5xl items-center gap-2 py-3">
				<Button asChild variant="ghost" className="gap-2 px-2 font-semibold">
					<Link to="/">
						<span className="size-2 rounded-full bg-primary" />
						Plaszczaki
					</Link>
				</Button>
				<div className="flex items-center gap-1">
					{NAV_LINKS.map((link) => (
						<Button key={link.to} asChild variant="ghost" size="sm">
							<Link
								to={link.to}
								activeOptions={{ exact: link.exact }}
								activeProps={{ className: "bg-accent text-accent-foreground" }}
							>
								{link.to === "/map" && <MapIcon data-icon="inline-start" />}
								{link.to === "/oferty" && (
									<ListOrderedIcon data-icon="inline-start" />
								)}
								{link.label}
							</Link>
						</Button>
					))}
				</div>
				<div className="ml-auto">
					<ThemeToggle />
				</div>
			</nav>
		</header>
	);
}

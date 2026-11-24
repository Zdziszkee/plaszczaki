import { Link, useNavigate } from "@tanstack/react-router";
import { ChevronDownIcon, GithubIcon, TwitterIcon } from "lucide-react";
import { Button } from "#/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuGroup,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "#/components/ui/dropdown-menu";
import BetterAuthHeader from "../integrations/better-auth/header-user.tsx";
import ThemeToggle from "./ThemeToggle";

const NAV_LINKS = [
	{ to: "/", label: "Home" },
	{ to: "/about", label: "About" },
	{ to: "/map", label: "Mapa" },
] as const;

const DEMO_LINKS = [
	{ to: "/demo/tanstack-query", label: "TanStack Query" },
	{ to: "/demo/drizzle", label: "Drizzle" },
	{ to: "/demo/better-auth", label: "Better Auth" },
] as const;

export default function Header() {
	const navigate = useNavigate();

	return (
		<header className="sticky top-0 z-50 border-b bg-background/80 px-4 backdrop-blur-lg">
			<nav className="mx-auto flex w-full max-w-5xl flex-wrap items-center gap-x-2 gap-y-2 py-3">
				<Button asChild variant="ghost" className="gap-2 px-2 font-semibold">
					<Link to="/">
						<span className="size-2 rounded-full bg-primary" />
						Plaszczaki
					</Link>
				</Button>

				<div className="order-3 flex w-full flex-wrap items-center gap-1 sm:order-none sm:w-auto">
					{NAV_LINKS.map((link) => (
						<Button key={link.to} asChild variant="ghost" size="sm">
							<Link
								to={link.to}
								activeOptions={{ exact: link.to === "/" }}
								activeProps={{
									className: "bg-accent text-accent-foreground",
								}}
							>
								{link.label}
							</Link>
						</Button>
					))}
					<Button asChild variant="ghost" size="sm">
						<a
							href="https://tanstack.com/start/latest/docs/framework/react/overview"
							target="_blank"
							rel="noreferrer"
						>
							Docs
						</a>
					</Button>

					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button variant="ghost" size="sm">
								Demos
								<ChevronDownIcon data-icon="inline-end" />
							</Button>
						</DropdownMenuTrigger>
						<DropdownMenuContent align="start">
							<DropdownMenuGroup>
								{DEMO_LINKS.map((demo) => (
									<DropdownMenuItem
										key={demo.to}
										onSelect={() => navigate({ to: demo.to })}
									>
										{demo.label}
									</DropdownMenuItem>
								))}
							</DropdownMenuGroup>
						</DropdownMenuContent>
					</DropdownMenu>
				</div>

				<div className="ml-auto flex items-center gap-1.5">
					<Button
						asChild
						variant="ghost"
						size="icon"
						className="hidden sm:inline-flex"
					>
						<a href="https://x.com/tan_stack" target="_blank" rel="noreferrer">
							<span className="sr-only">Follow TanStack on X</span>
							<TwitterIcon />
						</a>
					</Button>
					<Button
						asChild
						variant="ghost"
						size="icon"
						className="hidden sm:inline-flex"
					>
						<a
							href="https://github.com/TanStack"
							target="_blank"
							rel="noreferrer"
						>
							<span className="sr-only">Go to TanStack GitHub</span>
							<GithubIcon />
						</a>
					</Button>
					<BetterAuthHeader />
					<ThemeToggle />
				</div>
			</nav>
		</header>
	);
}

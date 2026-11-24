import { createFileRoute, Link } from "@tanstack/react-router";
import { Button } from "#/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "#/components/ui/card";

export const Route = createFileRoute("/")({ component: App });

function App() {
	return (
		<main className="page-wrap px-4 pb-8 pt-14">
			<section className="island-shell rise-in relative overflow-hidden rounded-[2rem] px-6 py-10 sm:px-10 sm:py-14">
				<div className="pointer-events-none absolute -left-20 -top-24 h-56 w-56 rounded-full bg-[radial-gradient(circle,rgba(79,184,178,0.32),transparent_66%)]" />
				<div className="pointer-events-none absolute -bottom-20 -right-20 h-56 w-56 rounded-full bg-[radial-gradient(circle,rgba(47,106,74,0.18),transparent_66%)]" />
				<p className="island-kicker mb-3">TanStack Start Base Template</p>
				<h1 className="display-title mb-5 max-w-3xl text-4xl leading-[1.02] font-bold tracking-tight text-[var(--sea-ink)] sm:text-6xl">
					Start simple, ship quickly.
				</h1>
				<p className="mb-8 max-w-2xl text-base text-[var(--sea-ink-soft)] sm:text-lg">
					This base starter intentionally keeps things light: two routes, clean
					structure, and the essentials you need to build from scratch.
				</p>
				<div className="flex flex-wrap gap-3">
					<Button
						asChild
						size="lg"
						className="rounded-full border border-[rgba(50,143,151,0.3)] bg-[rgba(79,184,178,0.14)] text-[var(--lagoon-deep)] no-underline shadow-none hover:bg-[rgba(79,184,178,0.24)]"
					>
						<Link to="/about">About This Starter</Link>
					</Button>
					<Button
						asChild
						variant="outline"
						size="lg"
						className="rounded-full border-[rgba(23,58,64,0.2)] bg-white/50 text-[var(--sea-ink)] no-underline hover:border-[rgba(23,58,64,0.35)] hover:bg-white/70"
					>
						<a
							href="https://tanstack.com/router"
							target="_blank"
							rel="noopener noreferrer"
						>
							Router Guide
						</a>
					</Button>
				</div>
			</section>

			<section className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
				{[
					[
						"Type-Safe Routing",
						"Routes and links stay in sync across every page.",
					],
					[
						"Server Functions",
						"Call server code from your UI without creating API boilerplate.",
					],
					[
						"Streaming by Default",
						"Ship progressively rendered responses for faster experiences.",
					],
					[
						"Tailwind Native",
						"Design quickly with utility-first styling and reusable tokens.",
					],
				].map(([title, desc], index) => (
					<Card
						key={title}
						className="island-shell feature-card rise-in"
						style={{ animationDelay: `${index * 90 + 80}ms` }}
					>
						<CardHeader>
							<CardTitle className="text-base text-[var(--sea-ink)]">
								{title}
							</CardTitle>
						</CardHeader>
						<CardContent>
							<p className="m-0 text-sm text-[var(--sea-ink-soft)]">{desc}</p>
						</CardContent>
					</Card>
				))}
			</section>

			<Card className="island-shell mt-8">
				<CardHeader>
					<CardTitle className="island-kicker text-sm">Quick Start</CardTitle>
				</CardHeader>
				<CardContent>
					<ul className="m-0 list-disc space-y-2 pl-5 text-sm text-[var(--sea-ink-soft)]">
						<li>
							Edit <code>src/routes/index.tsx</code> to customize the home page.
						</li>
						<li>
							Update <code>src/components/Header.tsx</code> and{" "}
							<code>src/components/Footer.tsx</code> for brand links.
						</li>
						<li>
							Add routes in <code>src/routes</code> and tweak visual tokens in{" "}
							<code>src/styles.css</code>.
						</li>
					</ul>
				</CardContent>
			</Card>
		</main>
	);
}

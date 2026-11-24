import { createFileRoute, Link } from "@tanstack/react-router";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";

export const Route = createFileRoute("/")({ component: App });

const FEATURES = [
	{
		title: "Type-Safe Routing",
		description: "Routes and links stay in sync across every page.",
	},
	{
		title: "Server Functions",
		description: "Call server code from your UI without API boilerplate.",
	},
	{
		title: "Streaming by Default",
		description:
			"Ship progressively rendered responses for faster experiences.",
	},
	{
		title: "Tailwind Native",
		description:
			"Design quickly with utility-first styling and reusable tokens.",
	},
];

function App() {
	return (
		<main className="mx-auto w-full max-w-5xl px-4 pb-8 pt-14">
			<Card>
				<CardHeader>
					<Badge variant="secondary" className="w-fit">
						TanStack Start Base Template
					</Badge>
					<CardTitle className="max-w-3xl text-4xl leading-tight font-bold tracking-tight sm:text-6xl">
						Start simple, ship quickly.
					</CardTitle>
					<CardDescription className="max-w-2xl text-base sm:text-lg">
						This base starter intentionally keeps things light: two routes,
						clean structure, and the essentials you need to build from scratch.
					</CardDescription>
				</CardHeader>
				<CardContent className="flex flex-wrap gap-3">
					<Button asChild size="lg">
						<Link to="/about">About This Starter</Link>
					</Button>
					<Button asChild variant="outline" size="lg">
						<a
							href="https://tanstack.com/router"
							target="_blank"
							rel="noopener noreferrer"
						>
							Router Guide
						</a>
					</Button>
				</CardContent>
			</Card>

			<section className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
				{FEATURES.map((feature) => (
					<Card key={feature.title}>
						<CardHeader>
							<CardTitle className="text-base">{feature.title}</CardTitle>
							<CardDescription>{feature.description}</CardDescription>
						</CardHeader>
					</Card>
				))}
			</section>

			<Card className="mt-8">
				<CardHeader>
					<CardTitle>Quick Start</CardTitle>
					<CardDescription>
						Where to look first when customizing the starter.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-muted-foreground">
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

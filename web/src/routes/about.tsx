import { createFileRoute } from "@tanstack/react-router";
import { Badge } from "#/components/ui/badge";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";

export const Route = createFileRoute("/about")({
	component: About,
});

function About() {
	return (
		<main className="mx-auto w-full max-w-5xl px-4 py-12">
			<Card>
				<CardHeader>
					<Badge variant="secondary" className="w-fit">
						About
					</Badge>
					<CardTitle className="text-4xl font-bold tracking-tight sm:text-5xl">
						A small starter with room to grow.
					</CardTitle>
					<CardDescription className="max-w-3xl text-base leading-8">
						TanStack Start gives you type-safe routing, server functions, and
						modern SSR defaults. Use this as a clean foundation, then layer in
						your own routes, styling, and add-ons.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<Badge variant="outline">TanStack Start</Badge>
				</CardContent>
			</Card>
		</main>
	);
}

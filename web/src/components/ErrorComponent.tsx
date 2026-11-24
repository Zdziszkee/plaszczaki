import type { ErrorComponentProps } from "@tanstack/react-router";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
	Card,
	CardDescription,
	CardFooter,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";

export default function ErrorComponent({ error, reset }: ErrorComponentProps) {
	const detail =
		import.meta.env.DEV && error instanceof Error
			? error.message
			: "Something went wrong while loading this page. Please try again.";

	return (
		<main className="mx-auto flex min-h-[60vh] w-full max-w-3xl items-center justify-center px-4 py-20">
			<Card className="w-full text-center">
				<CardHeader>
					<Badge variant="destructive" className="mx-auto w-fit">
						Error
					</Badge>
					<CardTitle className="text-4xl font-bold tracking-tight sm:text-5xl">
						Something went wrong
					</CardTitle>
					<CardDescription className="mx-auto max-w-md">
						{detail}
					</CardDescription>
				</CardHeader>
				<CardFooter className="justify-center">
					<Button type="button" onClick={reset}>
						Try again
					</Button>
				</CardFooter>
			</Card>
		</main>
	);
}

import { Link } from "@tanstack/react-router";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
	Card,
	CardDescription,
	CardFooter,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";

export default function NotFound() {
	return (
		<main className="mx-auto flex min-h-[60vh] w-full max-w-3xl items-center justify-center px-4 py-20">
			<Card className="w-full text-center">
				<CardHeader>
					<Badge variant="outline" className="mx-auto w-fit">
						404
					</Badge>
					<CardTitle className="text-4xl font-bold tracking-tight sm:text-5xl">
						Page not found
					</CardTitle>
					<CardDescription className="mx-auto max-w-md">
						The page you are looking for does not exist or has been moved.
					</CardDescription>
				</CardHeader>
				<CardFooter className="justify-center">
					<Button asChild>
						<Link to="/">Back to home</Link>
					</Button>
				</CardFooter>
			</Card>
		</main>
	);
}

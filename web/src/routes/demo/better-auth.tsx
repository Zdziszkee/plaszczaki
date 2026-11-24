import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "#/components/ui/alert";
import { Avatar, AvatarFallback, AvatarImage } from "#/components/ui/avatar";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardFooter,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "#/components/ui/field";
import { Input } from "#/components/ui/input";
import { Spinner } from "#/components/ui/spinner";
import { authClient } from "#/lib/auth-client";

export const Route = createFileRoute("/demo/better-auth")({
	component: BetterAuthDemo,
});

function BetterAuthDemo() {
	const { data: session, isPending } = authClient.useSession();
	const [isSignUp, setIsSignUp] = useState(false);
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [name, setName] = useState("");
	const [error, setError] = useState("");
	const [loading, setLoading] = useState(false);

	if (isPending) {
		return (
			<main className="flex min-h-[60vh] items-center justify-center px-4">
				<Spinner className="size-6" />
			</main>
		);
	}

	if (session?.user) {
		return (
			<main className="mx-auto flex w-full max-w-3xl items-center justify-center px-4 py-12">
				<Card className="w-full max-w-md">
					<CardHeader>
						<Badge variant="secondary" className="w-fit">
							Better Auth
						</Badge>
						<CardTitle>Welcome back</CardTitle>
						<CardDescription>
							You're signed in as {session.user.email}
						</CardDescription>
					</CardHeader>
					<CardContent className="flex items-center gap-3">
						<Avatar className="size-10">
							{session.user.image ? (
								<AvatarImage src={session.user.image} alt="" />
							) : null}
							<AvatarFallback>
								{session.user.name?.charAt(0).toUpperCase() || "U"}
							</AvatarFallback>
						</Avatar>
						<div className="min-w-0 flex-1">
							<p className="truncate text-sm font-medium">
								{session.user.name}
							</p>
							<p className="truncate text-xs text-muted-foreground">
								{session.user.email}
							</p>
						</div>
					</CardContent>
					<CardFooter className="flex-col items-stretch gap-3">
						<Button
							variant="secondary"
							className="w-full"
							onClick={() => {
								void authClient.signOut();
							}}
						>
							Sign out
						</Button>
						<p className="text-center text-xs text-muted-foreground">
							Built with{" "}
							<a
								href="https://better-auth.com"
								target="_blank"
								rel="noopener noreferrer"
								className="font-medium underline underline-offset-4 hover:text-primary"
							>
								BETTER-AUTH
							</a>
							.
						</p>
					</CardFooter>
				</Card>
			</main>
		);
	}

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError("");
		setLoading(true);

		try {
			if (isSignUp) {
				const result = await authClient.signUp.email({
					email,
					password,
					name,
				});
				if (result.error) {
					setError(result.error.message || "Sign up failed");
				}
			} else {
				const result = await authClient.signIn.email({
					email,
					password,
				});
				if (result.error) {
					setError(result.error.message || "Sign in failed");
				}
			}
		} catch {
			setError("An unexpected error occurred");
		} finally {
			setLoading(false);
		}
	};

	return (
		<main className="mx-auto flex w-full max-w-3xl items-center justify-center px-4 py-12">
			<Card className="w-full max-w-md">
				<CardHeader>
					<Badge variant="secondary" className="w-fit">
						Better Auth
					</Badge>
					<CardTitle>{isSignUp ? "Create an account" : "Sign in"}</CardTitle>
					<CardDescription>
						{isSignUp
							? "Enter your information to create an account"
							: "Enter your email below to login to your account"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<form onSubmit={handleSubmit}>
						<FieldGroup>
							{isSignUp && (
								<Field>
									<FieldLabel htmlFor="name">Name</FieldLabel>
									<Input
										id="name"
										type="text"
										value={name}
										onChange={(e) => setName(e.target.value)}
										required
									/>
								</Field>
							)}

							<Field>
								<FieldLabel htmlFor="email">Email</FieldLabel>
								<Input
									id="email"
									type="email"
									value={email}
									onChange={(e) => setEmail(e.target.value)}
									required
								/>
							</Field>

							<Field>
								<FieldLabel htmlFor="password">Password</FieldLabel>
								<Input
									id="password"
									type="password"
									value={password}
									onChange={(e) => setPassword(e.target.value)}
									required
									minLength={8}
								/>
							</Field>

							{error && (
								<Alert variant="destructive">
									<AlertTitle>Something went wrong</AlertTitle>
									<AlertDescription>{error}</AlertDescription>
								</Alert>
							)}

							<Button type="submit" disabled={loading} className="w-full">
								{loading ? (
									<>
										<Spinner data-icon="inline-start" />
										Please wait
									</>
								) : isSignUp ? (
									"Create account"
								) : (
									"Sign in"
								)}
							</Button>
						</FieldGroup>
					</form>
				</CardContent>
				<CardFooter className="flex-col items-stretch gap-3">
					<Button
						type="button"
						variant="link"
						onClick={() => {
							setIsSignUp(!isSignUp);
							setError("");
						}}
					>
						{isSignUp
							? "Already have an account? Sign in"
							: "Don't have an account? Sign up"}
					</Button>
					<p className="text-center text-xs text-muted-foreground">
						Built with{" "}
						<a
							href="https://better-auth.com"
							target="_blank"
							rel="noopener noreferrer"
							className="font-medium underline underline-offset-4 hover:text-primary"
						>
							BETTER-AUTH
						</a>
						.
					</p>
				</CardFooter>
			</Card>
		</main>
	);
}

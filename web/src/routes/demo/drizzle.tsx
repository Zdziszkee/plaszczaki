import { createFileRoute, useRouter } from "@tanstack/react-router";
import { createServerFn } from "@tanstack/react-start";
import { desc } from "drizzle-orm";
import { Alert, AlertDescription, AlertTitle } from "#/components/ui/alert";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "#/components/ui/card";
import { Input } from "#/components/ui/input";
import { db } from "#/db/index";
import { todos } from "#/db/schema";

const getTodos = createServerFn({
	method: "GET",
}).handler(async () => {
	return await db.query.todos.findMany({
		orderBy: [desc(todos.createdAt)],
	});
});

const createTodo = createServerFn({
	method: "POST",
})
	.validator((data: { title: string }) => data)
	.handler(async ({ data }) => {
		await db.insert(todos).values({ title: data.title });
		return { success: true };
	});

export const Route = createFileRoute("/demo/drizzle")({
	component: DemoDrizzle,
	loader: async () => await getTodos(),
});

function DemoDrizzle() {
	const router = useRouter();
	const todos = Route.useLoaderData();

	const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		const formData = new FormData(e.target as HTMLFormElement);
		const title = formData.get("title") as string;

		if (!title) return;

		try {
			await createTodo({ data: { title } });
			router.invalidate();
			(e.target as HTMLFormElement).reset();
		} catch (error) {
			console.error("Failed to create todo:", error);
		}
	};

	return (
		<main className="mx-auto w-full max-w-3xl px-4 py-12">
			<Card>
				<CardHeader>
					<div className="flex items-center gap-3">
						<div className="flex size-12 items-center justify-center rounded-md border bg-muted p-2">
							<img src="/drizzle.svg" alt="Drizzle Logo" className="size-8" />
						</div>
						<div className="flex flex-col gap-1">
							<Badge variant="secondary" className="w-fit">
								Database
							</Badge>
							<CardTitle>Drizzle Demo</CardTitle>
						</div>
					</div>
				</CardHeader>
				<CardContent className="flex flex-col gap-6">
					<div className="flex flex-col gap-3">
						<h2 className="text-lg font-semibold">Todos</h2>
						<ul className="flex flex-col gap-2">
							{todos.map((todo) => (
								<li key={todo.id}>
									<Card className="gap-0 py-3">
										<CardContent className="flex items-center justify-between px-4">
											<span className="font-medium">{todo.title}</span>
											<Badge variant="outline">#{todo.id}</Badge>
										</CardContent>
									</Card>
								</li>
							))}
							{todos.length === 0 && (
								<li>
									<Card className="gap-0 py-3">
										<CardContent className="px-4 text-center text-sm text-muted-foreground">
											No todos yet. Create one below!
										</CardContent>
									</Card>
								</li>
							)}
						</ul>
					</div>

					<form
						onSubmit={handleSubmit}
						className="flex flex-col gap-2 sm:flex-row"
					>
						<Input
							type="text"
							name="title"
							placeholder="Add a new todo..."
							className="min-w-0 flex-1"
						/>
						<Button type="submit" className="whitespace-nowrap">
							Add Todo
						</Button>
					</form>
				</CardContent>
			</Card>

			<Alert className="mt-6">
				<AlertTitle>Powered by Drizzle ORM</AlertTitle>
				<AlertDescription>
					<p>Next-generation ORM for Node.js & TypeScript.</p>
					<ol className="mt-2 flex list-inside list-decimal flex-col gap-2">
						<li>
							Configure your <code>DATABASE_URL</code> in .env.local
						</li>
						<li>
							Run: <code>bunx --bun drizzle-kit generate</code>
						</li>
						<li>
							Run: <code>bunx --bun drizzle-kit migrate</code>
						</li>
						<li>
							Optional: <code>bunx --bun drizzle-kit studio</code>
						</li>
					</ol>
				</AlertDescription>
			</Alert>
		</main>
	);
}

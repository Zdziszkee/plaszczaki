import { queryOptions, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Badge } from "#/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "#/components/ui/card";
import { Separator } from "#/components/ui/separator";

interface Todo {
	id: number;
	name: string;
}

const todosQueryOptions = queryOptions({
	queryKey: ["todos"],
	queryFn: async (): Promise<Array<Todo>> => [
		{ id: 1, name: "Alice" },
		{ id: 2, name: "Bob" },
		{ id: 3, name: "Charlie" },
	],
});

export const Route = createFileRoute("/demo/tanstack-query")({
	loader: ({ context }) =>
		context.queryClient.ensureQueryData(todosQueryOptions),
	component: TanStackQueryDemo,
});

function TanStackQueryDemo() {
	const { data: todos } = useSuspenseQuery(todosQueryOptions);

	return (
		<main className="mx-auto w-full max-w-3xl px-4 py-12">
			<Card>
				<CardHeader>
					<Badge variant="secondary" className="w-fit">
						TanStack Query
					</Badge>
					<CardTitle>TanStack Query SSR with ensureQueryData</CardTitle>
				</CardHeader>
				<CardContent>
					<ul className="flex flex-col gap-3">
						{todos.map((todo, index) => (
							<li key={todo.id}>
								{index > 0 ? <Separator className="mb-3" /> : null}
								<div className="flex items-center justify-between">
									<span className="text-base font-medium">{todo.name}</span>
									<Badge variant="outline">#{todo.id}</Badge>
								</div>
							</li>
						))}
					</ul>
				</CardContent>
			</Card>
		</main>
	);
}

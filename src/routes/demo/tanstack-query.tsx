import { queryOptions, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

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
		<main className="demo-page demo-center">
			<section className="demo-panel w-full max-w-2xl">
				<p className="island-kicker mb-2">TanStack Query</p>
				<h1 className="demo-title mb-6">
					TanStack Query SSR with ensureQueryData
				</h1>
				<ul className="mb-4 space-y-2">
					{todos.map((todo) => (
						<li key={todo.id} className="demo-list-item">
							<span className="text-base font-medium">{todo.name}</span>
						</li>
					))}
				</ul>
			</section>
		</main>
	);
}

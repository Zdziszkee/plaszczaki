import { QueryClient } from "@tanstack/react-query";

export function getContext() {
	const queryClient = new QueryClient({
		defaultOptions: {
			queries: {
				staleTime: 60_000,
				retry: 1,
				refetchOnWindowFocus: false,
			},
		},
	});

	return {
		queryClient,
	};
}

export type TanStackQueryContext = ReturnType<typeof getContext>;

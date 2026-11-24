import type { TanStackDevtoolsReactPlugin } from "@tanstack/react-devtools";
import { ReactQueryDevtoolsPanel } from "@tanstack/react-query-devtools";

function NoopDevtoolsPanel() {
	return null;
}

const tanstackQueryDevtools: TanStackDevtoolsReactPlugin = {
	name: "TanStack Query",
	render: import.meta.env.DEV ? (
		<ReactQueryDevtoolsPanel />
	) : (
		<NoopDevtoolsPanel />
	),
};

export default tanstackQueryDevtools;

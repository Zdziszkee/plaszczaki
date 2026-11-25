import tailwindcss from "@tailwindcss/vite";
import { devtools } from "@tanstack/devtools-vite";

import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { nitro } from "nitro/vite";
import { defineConfig } from "vite";

const config = defineConfig({
	resolve: { tsconfigPaths: true },
	plugins: [
		devtools(),
		tailwindcss(),
		tanstackStart(),
		nitro({
			preset: "bun",
			publicAssets: [
				{
					dir: "node_modules/.nitro/vite/services/ssr/assets",
					baseURL: "/assets",
					maxAge: 60 * 60 * 24 * 365,
					ignore: ["**/*.js", "**/*.mjs", "**/*.map"],
				},
			],
		}),
		viteReact(),
	],
});

export default config;

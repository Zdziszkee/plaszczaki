import { existsSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

/**
 * One canonical listings database: `<repo>/data/plaszczaki.db`.
 *
 * `DATABASE_URL` may be absolute (containers use `/data/plaszczaki.db`) or
 * relative to `web/` (the documented dev form, `../data/plaszczaki.db`). Relative
 * values used to be resolved against `process.cwd()`, so running a command from
 * the repo root, `web/` or anywhere else could silently create a second empty
 * SQLite file. Resolve against the repository root instead.
 */
const DEFAULT_DATABASE_URL = "../data/plaszczaki.db";

const REPO_MARKERS = ["web/src/db/schema.ts", "scraper/pyproject.toml"];

function isRepoRoot(dir: string): boolean {
	return REPO_MARKERS.every((marker) => existsSync(join(dir, marker)));
}

function findRepoRoot(start: string): string | undefined {
	let dir = resolve(start);
	for (;;) {
		if (isRepoRoot(dir)) return dir;
		const parent = dirname(dir);
		if (parent === dir) return undefined;
		dir = parent;
	}
}

export function resolveDatabaseUrl(
	raw: string | undefined = process.env.DATABASE_URL,
): string {
	const value = raw?.trim();
	if (value && isAbsolute(value)) return value;

	const repoRoot = findRepoRoot(process.cwd());
	if (repoRoot) {
		// Relative URLs are documented as relative to `web/`.
		return resolve(join(repoRoot, "web"), value || DEFAULT_DATABASE_URL);
	}
	return resolve(process.cwd(), value || DEFAULT_DATABASE_URL);
}

# plaszczaki

A TanStack Start v1 app with file-based routing, TanStack Query, Drizzle ORM on SQLite, Better Auth, shadcn/ui, Tailwind CSS v4, Biome, and Nitro (`preset: 'bun'`).

## Requirements

- [Bun](https://bun.sh/) >= 1.4 (developed on Bun 1.4.0)
- Use Bun for every command in this repo: `bun`, `bun run`, and `bunx`
- Do not use npm, pnpm, or yarn — the repo has a `bun.lock`

## Development

```bash
bun install
bun --bun run dev
```

The dev server runs at <http://localhost:3000>.

`bun --bun` makes Vite run on the Bun runtime. Keep it for the Vite scripts (`dev`, `build`, `preview`, `generate-routes`).

## Production

Build with the Nitro Bun preset, then start the built server:

```bash
bun --bun run build
bun run start
```

- Build output: `.output/`
- Server entry: `.output/server/index.mjs`
- Preview the production build locally: `bun --bun run preview`

## Environment

Create `.env.local` in the project root:

```bash
DATABASE_URL="dev.db"
BETTER_AUTH_URL=http://localhost:3000
BETTER_AUTH_SECRET=
```

`.env.local` is gitignored. Never commit secrets.

## Database

The app uses SQLite through Bun's native `bun:sqlite` driver and Drizzle ORM (`drizzle-orm/bun-sqlite`). The database file is `dev.db` by default, configured by `DATABASE_URL`.

- Schema: `src/db/schema.ts`
- Client: `src/db/index.ts`
- Drizzle Kit config: `drizzle.config.ts` (loads `.env.local`, then `.env`)

```bash
bun run db:generate   # generate SQL migrations in ./drizzle
bun run db:push       # push the schema directly to the database
bun run db:migrate    # apply generated migrations
bun run db:studio     # open Drizzle Studio
```

`bun run db:pull` is also available for introspecting an existing database.

## Auth

Better Auth is configured in `src/lib/auth.ts` with the Drizzle SQLite adapter and email/password enabled. The React client is `src/lib/auth-client.ts`; the handler is `src/routes/api/auth/$.ts`.

Set `BETTER_AUTH_SECRET` and `BETTER_AUTH_URL` in `.env.local`. Generate a secret:

```bash
bunx --bun @better-auth/cli secret
```

Better Auth tables (`user`, `session`, `account`, `verification`) are defined in `src/db/schema.ts`. Generate and apply their migrations with the Drizzle scripts above, or run:

```bash
bunx --bun @better-auth/cli migrate
```

## shadcn/ui

Components use the `new-york` style and these aliases from `components.json`:

- `#/components`
- `#/components/ui`
- `#/lib/utils`

Add a component:

```bash
bunx shadcn@latest add <component>
# e.g.
bunx shadcn@latest add button
```

Tailwind CSS v4 is wired through `@tailwindcss/vite`; global styles live in `src/styles.css`.

## Linting and formatting

Biome handles both:

```bash
bun --bun run check
bun --bun run format
bun --bun run lint
```

`check` runs the linter and formatter together; `format` and `lint` apply or report one at a time. `src/routeTree.gen.ts` and `src/styles.css` are excluded in `biome.json`.

## Project structure

```text
src/
  routes/            file-based routes (createFileRoute); __root.tsx is the app shell
  routes/api/        server route handlers (e.g. /api/auth/*)
  db/                Drizzle client and schema
  lib/auth.ts        Better Auth server config (plus auth-client.ts, utils.ts)
  integrations/      TanStack Query provider/devtools and Better Auth UI
  components/        shared UI and components/ui
  router.tsx         router factory with SSR Query integration
  routeTree.gen.ts   generated route tree (do not edit)
  styles.css         Tailwind entry
```

## Verification

There is no test runner configured in `package.json`. Verify changes with:

```bash
bun --bun run check
bun --bun run build
```

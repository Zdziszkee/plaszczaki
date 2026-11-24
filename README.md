# plaszczaki

Polish real-estate data platform: a TanStack Start web app and a Python collection
sidecar, sharing one repo-level workspace and runtime data directory.

## Layout

```text
web/        TanStack Start app (routes, UI, Drizzle/SQLite, Better Auth)
scraper/    Python sidecar (scraping + source list in scraper/config/sources.json)
deploy/     Containerfiles and Podman quadlets
data/       Runtime only: SQLite DB + scraper output (gitignored)
```

`scraper/config/sources.json` is the single source of truth for the portal list:

```json
{ "urls": ["https://www.otodom.pl"] }
```

It is loaded and validated by `scraper/src/plaszczaki_scraper/config.py`. The web app
does not read it.

## Requirements

- Bun >= 1.4 (developed on 1.4.0) — use `bun`, `bun run`, `bunx` only
- Python 3.15 + uv 0.13+ (scraper)
- Podman + systemd/Quadlet (deployment, optional)

## Setup

```bash
bun install                      # installs the web workspace
uv sync --directory scraper --locked
cp web/.env.local.example web/.env.local
```

## Web app

```bash
bun run dev                      # http://localhost:3000
bun run build                    # web/.output (Nitro bun preset)
bun run start                    # run the built server
```

Root scripts delegate to `web/` with `bun run --cwd web <script>`. Use
`bun --bun` for Vite commands so they run on the Bun runtime.

### Environment

`web/.env.local` is loaded by Vite and Drizzle:

```bash
DATABASE_URL="../data/plaszczaki.db"
BETTER_AUTH_URL=http://localhost:3000
BETTER_AUTH_SECRET=
VITE_MAPBOX_TOKEN=
```

`DATABASE_URL` is relative to `web/`. In containers it is absolute (`/data/plaszczaki.db`).
Never commit `.env.local` or secrets.

### Database

SQLite via `bun:sqlite` and Drizzle ORM.

- Schema: `web/src/db/schema.ts`
- Client: `web/src/db/index.ts`
- Drizzle Kit config: `web/drizzle.config.ts`
- Migrations: `web/drizzle/`
- Database file: `data/plaszczaki.db`

```bash
bun run db:generate
bun run db:push
bun run db:migrate
bun run db:studio
```

## Scraper

```bash
uv run --directory scraper python -m plaszczaki_scraper
bun run py:lint
bun run py:format
bun run py:typecheck
bun run py:check
```

The source config lives at `scraper/config/sources.json`; `load_source_urls()`
validates it (https only, no unknown keys, no normalized duplicates).

## Deploy (Podman + Quadlet)

One command builds both images, creates the env files if missing, installs the
Quadlets, starts `plaszczaki-web` and runs `plaszczaki-scraper` once:

```bash
bun run deploy:up            # or: ./deploy/plaszczaki.sh up
bun run deploy:status
bun run deploy:logs          # follow web logs
bun run deploy:down
```

`deploy:up` rebuilds first; add `--no-build` (`./deploy/plaszczaki.sh up --no-build`)
to skip rebuilding. `deploy:build` rebuilds images only, `deploy:restart` restarts
web without rebuilding, and `./deploy/plaszczaki.sh uninstall` stops the services
and removes the Quadlets.

The script is a thin wrapper over the manual steps, which are:

```bash
podman build -f deploy/containers/web.Containerfile -t localhost/plaszczaki-web:0.1.0 .
podman build -f deploy/containers/scraper.Containerfile -t localhost/plaszczaki-scraper:0.1.0 scraper/

cp deploy/quadlet/*.container deploy/quadlet/*.timer ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start plaszczaki-web
```

Environment files expected by the quadlets live outside the repo (created by
`deploy:up` on first run, mode 0600):

- `~/.config/plaszczaki/web.env` — `DATABASE_URL=/data/plaszczaki.db`,
  `BETTER_AUTH_URL`, `BETTER_AUTH_SECRET`
- `~/.config/plaszczaki/scraper.env`

Images are fully qualified (`docker.io/...`); Quadlets are generated into
`/run/user/$UID/systemd/generator` and autostart at login via their
`WantedBy=default.target`. The scraper is a one-shot job — starting it runs once
and exits, and `plaszczaki-scraper.timer` is disabled until the first spider exists.

## Checks

```bash
bun run check        # Biome lint + format
bun run typecheck    # tsc --noEmit in web/
bun run py:check     # ruff + ty
bun --bun run build
```

There is no Bun test runner configured.

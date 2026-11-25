# plaszczaki

Polish real-estate data platform: a TanStack Start web app and a Python collection
sidecar, sharing one repo-level workspace and runtime data directory.

## Layout

```text
web/        TanStack Start app (routes, UI, Drizzle/SQLite, Better Auth)
scraper/    Python sidecar (scraping + sources/adapter config in scraper/config/sources.json)
deploy/     Containerfiles and Podman quadlets
data/       Runtime only: SQLite DB + scraper output (gitignored)
```

`scraper/config/sources.json` is the single source of truth for both the portal
list and the scraper settings: a `defaults` block plus 154 sources (161 URLs).
Every entry carries catalog fields — `id`, `label`, `kind`, `tier`, `status`,
`enabled`, `urls`, `notes`, `last_verified`, `recheck_days`, `refresh_minutes` —
and `implemented` entries add their adapter fields in the same object:

```json
{
  "defaults": { "rps": 1.0 },
  "sources": [
    {
      "id": "otodom",
      "label": "Otodom",
      "kind": "portal",
      "tier": 1,
      "status": "implemented",
      "enabled": true,
      "urls": ["https://www.otodom.pl"],
      "refresh_minutes": 10,
      "adapter": "otodom",
      "rps": 1.0,
      "incremental": true,
      "incremental_pages": 2,
      "hydrate_details": true,
      "fetch_mode": "http",
      "crawl_delay_mode": "sleep"
    }
  ]
}
```

Parked sources keep only catalog fields. `defaults.rps` is the rate fallback and
`refresh_minutes` is the per-source refresh cadence. The file is loaded and
validated by `scraper/src/plaszczaki_scraper/config.py`; the geographic scope is
separate in `scraper/config/regions.json`, and the web app does not read any of it.

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

### Mapa

`/map` renders a Mapbox Standard night map with custom rail layers. Existing
rail comes from Mapbox Streets; planned and under-construction heavy rail in
Śląskie and Małopolskie comes from a committed OpenStreetMap snapshot at
`web/public/data/planned-rail.geojson` (© OpenStreetMap contributors, ODbL).
Segments belonging to **Port Polska (CPK)** — Katowice–Ostrawa and the
Jastrzębie/Wodzisław connectors — are tagged `program=port-polska` and drawn in
teal with their own toggle. The tag also covers the PLK-managed
Katowice–Mikołów approach, because the investment is joint CPK/PLK. In the
current ZSK plan there is **no Port Polska rail project in Małopolska**: the
former Małopolsko-Śląski hub (including a new Katowice–Kraków line) was dropped,
and Podłęże–Piekiełko is a PKP PLK project shown as ordinary planned rail. The
Olkusz–Kraków improvement is also mapped: it is not a new full line but the
4 km Borowa Górka–Pieczyska connector in Jaworzno (Kolej Plus, PKP PLK). Its
environmental-decision procedure resumed in 2026 and PLK lists 2028–2030.

Kolej Plus has its own lime layer and `web/public/data/kolej-plus.geojson`
(© OpenStreetMap contributors, ODbL). It includes both new lines and
modernisations of existing tracks for the current PKP PLK projects in the two
voivodeships. Refresh it with:

```bash
bun run data:kolej-plus
```

Two Kolej Plus projects from the current list have no mapped public alignment
in OSM yet — Kraków–Niepołomice and LK 198 Pyskowice–Pyskowice Miasto — so they
appear as clearly labelled “przebieg orientacyjny” points, not invented lines.
The rest have mapped geometry. All remaining OSM `construction`/`proposed` rail
stays in the generic “Kolej planowana” layer. Major PLK modernisations that are
not Kolej Plus (E65, E30, Orzesze–Tychy, Tychy–Bieruń) are shown where OSM
already maps works; projects without mapped works are not drawn. Refresh both
snapshots when OSM changes:

```bash
bun run data:planned-rail
bun run data:kolej-plus
```

The scripts query the Overpass API within `PL-24` and `PL-12`, keep the relevant
heavy-rail ways and write deterministic GeoJSON snapshots. The map never calls
Overpass at runtime, and the snapshots are not an official investment schedule.
Kolej Plus project boxes and metadata live in
`web/scripts/kolej-plus-projects.ts`.

The same map renders scraped offers from `data/plaszczaki.db`. At low zoom it
shows aggregated cells with counts; from zoom 10 it switches to individual
listings. The **Oferty** toggle and its filters (offer type, property type,
price range) are persisted in `localStorage`. The layer is viewport-driven and
calls `/api/listings` or `/api/listings/clusters` for the current bounds.

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
uv run --directory scraper python -m plaszczaki_scraper run --source otodom --limit 20
uv run --directory scraper python -m plaszczaki_scraper run --source otodom --newest
uv run --directory scraper python -m plaszczaki_scraper run --source otodom --mode discover
uv run --directory scraper python -m plaszczaki_scraper run --source otodom --mode backfill
uv run --directory scraper python -m plaszczaki_scraper watch --once
uv run --directory scraper python -m plaszczaki_scraper geocode --limit 100 --enqueue-existing
uv run --directory scraper python -m plaszczaki_scraper geocode --limit 100 --requeue-rejected
uv run --directory scraper python -m plaszczaki_scraper list-sources
uv run --directory scraper python -m plaszczaki_scraper audit
uv run --directory scraper python -m plaszczaki_scraper review-sources --due
uv run --directory scraper python -m plaszczaki_scraper dedupe --backfill
uv run --directory scraper python -m plaszczaki_scraper verify-schema
uv run --directory scraper python -m plaszczaki_scraper llm models
uv run --directory scraper python -m plaszczaki_scraper llm check
bun run py:test
bun run py:lint
bun run py:format
bun run py:typecheck
bun run py:check
```

`scraper/config/sources.json` holds the whole catalog and each implemented
source's adapter settings in one entry — RPS, incremental mode, newest-sort
parameters, detail hydration, fetch mode and crawl-delay mode — with
`defaults.rps` as the fallback. `refresh_minutes` in the same file is the `watch`
cadence. The Małopolskie/Śląskie scope is in `scraper/config/regions.json`.
`load_sources_config()` validates the inventory (https only, no unknown keys,
adapter fields only on `implemented` sources, no normalized duplicates).

PDF-only sources go through `scraper/src/plaszczaki_scraper/pdf.py`
(`pdfplumber`, cached under `data/cache/pdf/`); the first adapter using it is
`zgm-gliwice`.

`run` accepts `--region` and `--offer-type`, so each voivodeship and each
transaction is its own job with its own limit and checkpoint. Do not backfill
both regions with one `--limit`: queries run małopolskie first and the limit
cuts śląskie entirely. For example:

```bash
uv run --directory scraper python -m plaszczaki_scraper run --source otodom \
  --region slaskie --offer-type rent --mode backfill
uv run --directory scraper python -m plaszczaki_scraper run --source otodom \
  --region malopolskie --offer-type sale --mode backfill
```

The scraper writes directly to `data/plaszczaki.db`. The listing schema is owned
by Drizzle in `web/src/db/schema.ts`; apply it with `bun run db:push` (or
`bun run db:migrate` on a fresh database) before running the scraper. The scraper
never runs DDL itself.

`run --mode backfill` is a long, resumable job (Otodom is roughly 60k listings in
these two voivodeships). Each search page is hydrated immediately and the parsed
offers are upserted as a batch; unparsed search summaries are never stored. The
systemd timer runs the incremental `watch --once` job, not a backfill.

## Deploy (Podman + Quadlet)

One command stops the running containers, rebuilds both images, installs the
Quadlets and starts `plaszczaki-web` + `plaszczaki-scraper` again:

```bash
bun run deploy            # or: ./deploy/plaszczaki.sh
```

It is also the only deploy command: there is no separate build, down, restart or
logs script. The systemd units are still there for inspection:

```bash
systemctl --user status plaszczaki-web
journalctl --user -u plaszczaki-web -f
journalctl --user -u plaszczaki-scraper -f
```

The script is a thin wrapper over the manual steps, which are:

```bash
podman build -f deploy/containers/web.Containerfile -t localhost/plaszczaki-web:0.1.0 .
podman build -f deploy/containers/scraper.Containerfile -t localhost/plaszczaki-scraper:0.1.0 scraper/

cp deploy/quadlet/*.container ~/.config/containers/systemd/
cp deploy/quadlet/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start plaszczaki-web plaszczaki-scraper
systemctl --user enable --now plaszczaki-scraper.timer plaszczaki-review.timer
```

Environment files expected by the quadlets live outside the repo (created on the
first `bun run deploy`, mode 0600):

- `~/.config/plaszczaki/web.env` — `DATABASE_URL=/data/plaszczaki.db`,
  `BETTER_AUTH_URL`, `BETTER_AUTH_SECRET`
- `~/.config/plaszczaki/scraper.env` — `DATABASE_URL=/data/plaszczaki.db`,
  `PLASZCZAKI_CACHE_DIR=/data/cache`, optional `OPENCODE_API_KEY` and LLM settings

Images are fully qualified (`docker.io/...`); Quadlets are generated into
`/run/user/$UID/systemd/generator` and autostart at login via their
`WantedBy=default.target`. The scraper is a one-shot job — starting it runs once
and exits. Deploy enables two timers: `plaszczaki-scraper.timer` runs
`watch --once --limit 200` every five minutes, and `plaszczaki-review.timer`
runs the monthly parked-source review. Backfills are still run manually because
they take many hours; there is no separate gdom unit.

Refresh cadence lives in `scraper/config/sources.json` (`refresh_minutes`) and
`watch` reads it directly. `gdom` is not a separate service or quadlet: it runs
inside `watch` with `crawl_delay_mode: "defer"` and `hydrate_details: false`.
Because its robots.txt asks for a 20-minute crawl delay, the client records the
last request time per host, makes at most one request per pass, and stops with a
`deferred` stat until the delay elapses. It rotates one category per pass
(`refresh_minutes: 1440`) and skips detail hydration because its search cards
already carry coordinates and the address.

## Checks

```bash
bun run check        # Biome lint + format
bun run typecheck    # tsc --noEmit in web/
bun run py:check     # ruff + ty
bun run py:test      # pytest
bun --bun run build
```

There is no JavaScript test runner configured.

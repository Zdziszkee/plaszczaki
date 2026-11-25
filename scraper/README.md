# plaszczaki-scraper

Python sidecar for data collection. Runs in a rootless Podman container managed by
systemd/Quadlet. It writes canonical listings **directly to `data/plaszczaki.db`**,
the SQLite database also used by the web app. The schema is owned by Drizzle in
`web/src/db/schema.ts`; the scraper only verifies and writes rows.

The scope is **małopolskie + śląskie** by default. The catalog in
`config/sources.json` lists every known portal (158 URLs across 153 sources) with
its tier, kind and implementation status. Every source now has a decision:

- `implemented` — adapter exists (see `list-sources`; `gdom` is implemented but
  disabled because its robots.txt asks for a 20-minute crawl delay).
- `blocked` — no usable coordinates/descriptions, paywall/login, WAF/captcha,
  out-of-scope geography, or non-property content. The reason is in `notes`.
- `excluded` — short-term rentals and non-property pages, out of scope for now.

`list-sources` prints the whole catalog with status, tier and note. Adapters are
added one portal at a time; `run-all` runs every enabled adapter.

## Toolchain

- Python 3.15 (uv-managed; see `.python-version`)
- uv 0.13+
- Scrapling 0.5 (`Fetcher`) for HTTP
- Ruff for linting and formatting
- ty for type checking (beta, pinned exactly)
- pytest for tests

## Commands

Run from the repository root:

```bash
bun run py:sync        # uv sync --locked
bun run py:lint        # ruff check
bun run py:format      # ruff format
bun run py:typecheck   # ty check
bun run py:test        # pytest
bun run py:check       # lint + format check + typecheck
```

Or from this directory:

```bash
uv run python -m plaszczaki_scraper run --source otodom --limit 20
uv run python -m plaszczaki_scraper run --source otodom --dry-run --limit 1
uv run python -m plaszczaki_scraper run --source otodom --mode discover
uv run python -m plaszczaki_scraper run --source otodom --mode backfill
uv run python -m plaszczaki_scraper run-all --tier 1
uv run python -m plaszczaki_scraper run --source otodom --newest
uv run python -m plaszczaki_scraper watch --once
uv run python -m plaszczaki_scraper watch --interval 60
uv run python -m plaszczaki_scraper geocode --limit 100
uv run python -m plaszczaki_scraper list-sources
uv run python -m plaszczaki_scraper probe --source otodom
uv run python -m plaszczaki_scraper probe --tier 1
uv run python -m plaszczaki_scraper audit
uv run python -m plaszczaki_scraper llm models
uv run python -m plaszczaki_scraper llm check
uv run python -m plaszczaki_scraper verify-schema
```

`watch` polls only sources marked `incremental` in `config/adapters.json`, at
their `poll_minutes` cadence. Each cycle reads the newest pages
(`incremental_pages`, default 2), skips listings already in the database, and
hydrates only new detail pages. `watch --once` is suitable for a systemd timer.

`geocode` drains the street-level geocoding queue in
`data/cache/geocode.db` with Nominatim at 1 req/s. Only house/street results are
accepted; city-only addresses stay unlocated. Geocoding data ©
OpenStreetMap contributors.

`run-all` runs every enabled adapter, optionally filtered with `--tier`, and keeps
going when one source fails. `probe` fetches one page per source and reports HTTP
status, JSON-LD/Next.js data, link count and challenge detection; its report is
written to `data/out/source-probes.json`.

`--mode run` (the default) fetches each search page, hydrates that page's detail
pages and upserts the parsed offers as a batch. `--mode backfill` is the same
long, resumable full job. `--mode discover` is diagnostics only and writes
nothing. `--limit` caps parsed offers per run, `--max-pages` caps discovery per
query, and `--dry-run` prints records without writing.

## Configuration

- `config/sources.json` — the complete source catalog: id, label, kind, tier,
  status, enabled flag and one or more inventory URLs.
- `config/adapters.json` — implemented adapters, RPS, notes, incremental mode,
  newest-sort parameter, incremental page count and polling cadence.
- `config/regions.json` — Małopolskie/Śląskie, TERYT codes, bboxes and place lists.

`PLASZCZAKI_CONFIG_DIR` overrides the config directory. `DATABASE_URL` points at
the SQLite file (defaults to `../data/plaszczaki.db` relative to the repo root;
the container uses `/data/plaszczaki.db`). `PLASZCZAKI_CACHE_DIR` stores robots
cache and reports (default `data/cache`).

## Database

The web app owns the schema:

```bash
bun run db:push        # apply schema to an existing dev DB
bun run db:migrate     # fresh DB / migration workflow
```

Only fully parsed offers are stored in `listing`; there is no listing lifecycle
status. The scraper checks the expected tables/columns at startup and fails with
a clear message if the migration has not been applied.

## LLM lane

The LLM lane is optional and separate from deterministic adapters. It discovers
free OpenCode models and tests them:

```bash
python -m plaszczaki_scraper llm models
python -m plaszczaki_scraper llm check
```

Put `OPENCODE_API_KEY` in `~/.config/plaszczaki/scraper.env` (never in the repo).
Free-tier models may be rejected from external clients; `llm check` reports which
ones work. The default safe chain is `hy4-flash-free`,
`deepseek-v4.1-flash-free`, then `beam-free`. Set
`LLM_ALLOW_RETENTION_FREE_MODELS=true` to try the other free models.

## Container

```bash
podman build -t localhost/plaszczaki-scraper:0.1.0 -f ../deploy/containers/scraper.Containerfile .
podman run --rm -v "$PWD/../data:/data" localhost/plaszczaki-scraper:0.1.0
```

The container defaults to `run --source otodom`. Override the command for a
backfill or a dry run.

Scheduled runs are defined outside the repository. The source units live in
`../deploy/quadlet/` and are installed to:

- `~/.config/containers/systemd/plaszczaki-scraper.container`
- `~/.config/systemd/user/plaszczaki-scraper.timer`

The timer is installed but disabled until the first production run is validated.
The scraper binds `data/` read-write so it can write the database and robots cache.

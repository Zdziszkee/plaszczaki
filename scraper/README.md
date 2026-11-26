# plaszczaki-scraper

Python sidecar for data collection. Runs in a rootless Podman container managed by
systemd/Quadlet. It writes canonical listings **directly to `data/plaszczaki.db`**,
the SQLite database also used by the web app. The schema is owned by Drizzle in
`web/src/db/schema.ts`; the scraper only verifies and writes rows.

The scope is **małopolskie + śląskie** by default. The catalog in
`config/sources.json` lists every known portal (161 URLs across 154 sources) with
its tier, kind and implementation status. Every source now has a decision:

- `implemented` — adapter exists (see `list-sources`; `gdom` runs as a slow lane
  inside `watch` because its robots.txt asks for a 20-minute crawl delay).
- `planned` — a usable location signal exists (coordinates or a street address
  for the geocoder) but the adapter is not written yet.
- `blocked` — no usable location, paywall/login, WAF/captcha, out-of-scope
  geography, or non-property content. `blocker` records the category and
  `notes` the reason.
- `excluded` — short-term rentals, non-property pages and sites whose inventory
  is already covered by another source.

Parked sources carry `last_verified` and `recheck_days`; `review-sources --due`
refetches the ones past their window and reports challenge, inventory and
location-signal changes.

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
uv run python -m plaszczaki_scraper cycle --limit 200
uv run python -m plaszczaki_scraper run-all --mode run --newest --only-non-incremental
uv run python -m plaszczaki_scraper geocode --limit 100
uv run python -m plaszczaki_scraper geocode --limit 100 --enqueue-existing --requeue-rejected
uv run python -m plaszczaki_scraper list-sources
uv run python -m plaszczaki_scraper probe --source otodom
uv run python -m plaszczaki_scraper probe --tier 1
uv run python -m plaszczaki_scraper audit
uv run python -m plaszczaki_scraper field-audit
uv run python -m plaszczaki_scraper field-audit --source otodom --top 20
uv run python -m plaszczaki_scraper backfill-attributes --dry-run
uv run python -m plaszczaki_scraper backfill-attributes
uv run python -m plaszczaki_scraper backfill-attributes --source otodom --rehydrate-district
uv run python -m plaszczaki_scraper probe --source otodom --fields
uv run python -m plaszczaki_scraper review-sources --due
uv run python -m plaszczaki_scraper dedupe --backfill
uv run python -m plaszczaki_scraper llm models
uv run python -m plaszczaki_scraper llm check
uv run python -m plaszczaki_scraper llm enrich --limit 50
uv run python -m plaszczaki_scraper llm rerank --limit 20 --candidates 50
uv run python -m plaszczaki_scraper geodata --part gtfs --force
uv run python -m plaszczaki_scraper geodata --part nature --force
uv run python -m plaszczaki_scraper geodata --part poi --force
uv run python -m plaszczaki_scraper geodata --part elevation
uv run python -m plaszczaki_scraper score --dry-run
uv run python -m plaszczaki_scraper score
uv run python -m plaszczaki_scraper score --pending
uv run python -m plaszczaki_scraper verify-schema
```

`watch` polls only sources whose `incremental` flag is set in
`config/sources.json`, at their `refresh_minutes` cadence from the same file. Each
cycle reads the newest pages
(`incremental_pages`, default 2), skips listings already in the database, and
hydrates only new detail pages. `watch --once` is suitable for a systemd timer.

`cycle` is the scheduled entry point: it partitions the watchable sources into
independent lanes and runs one pass over each:

- `malopolskie:sale`, `malopolskie:rent`, `slaskie:sale`, `slaskie:rent` — each
  lane has its own `--limit` budget and its own last-run timestamp, so a rent
  pass never masks a sale pass and a śląskie pass never masks a małopolskie one.
- `other` — auction/lease/mixed queries (`offer_type` not sale/rent).
- `national` — sources whose search URLs are identical for both voivodeships
  (Gdom, AMW, Nieruchomościowi), so they run once, not twice. Poczta Polska is
  not in this lane: it is non-incremental and its adapter now asks the site for
  each region separately with `?locationId=`.
- `stealth` — sources with `fetch_mode: "stealth"`; they run through the
  Patchright Chromium session and are kept out of the HTTP lanes so a browser
  launch cannot delay the sale/rent passes.

`--offer-type rent` / `--offer-type sale` and `--region slaskie` /
`--region malopolskie` on `watch` remain available for one-off jobs; the mode key
includes the filters.

Sources that only offer city pages (Flats, Okolica, Rentumo, Rentola) have
no voivodeship-level URL. They crawl every configured city in each region, not
just Kraków/Katowice, and rotate the city order hourly so a per-lane limit
cannot starve the same towns every pass. Flats uses its allowed listings
sitemap because the city page embeds only the first 16 cards.

`run-all --only-non-incremental` runs the enabled adapters that `watch` does not
poll (the `incremental: false` ones) in one full pass. The nightly
`plaszczaki-full.timer` calls it with `--newest`, so those sources are refreshed
from the newest pages instead of being left to a manual backfill. Sources are
ordered by their last attempt (never run first, then oldest), with query count
as the tie-breaker, so a short pass cannot starve the same big aggregators every
night; `okolica` still needs several nightly passes to backfill its full
city × category matrix.

`--newest` always reads only the two newest pages per query, whatever the
source. A source that has never been backfilled therefore needs one full
`run --source <id>` (no `--newest`) before the incremental passes can keep it
current; the nightly full lane only refreshes those first pages.

`elicytacje-mf` (KAS eLicytacje) runs on the stealth lane. Its search endpoint is
a JSON `POST` issued through the browser context, so the Imperva cookies are
reused; `browser_wait_ms: 15000` lets the challenge clear before the first
request. Details are rendered `/auction/announcements/{uuid}` pages.

`kowr` uses the same stealth lane: its list is filtered with `location=88`
(małopolskie) or `location=94` (śląskie), then details are parsed for price,
area, location and auction date. `pge` reads the public
`datahub.gkpge.pl/api/geo/property` feed with `voivodeshipCode=12/24`; the feed
already contains coordinates, address, price and area, so no detail hydration
is needed. The DGLP Lasów Państwowych PDF index is catalogued as `planned` and
still needs the PDF lane.

`geocode` drains the street-level geocoding queue in
`data/cache/geocode.db` with Nominatim at 1 req/s. Queries are structured
(`street`, `city`, `postcode`, `state`) and fall back from a house number to the
street when the building cannot be resolved; the building number comes from
`street` or from `address_text`. Jobs for auctions are drained first, ordered by
`auction_date`. `--requeue-rejected` re-opens the jobs an older, less precise
matcher rejected. Only house/street results are accepted; city-only addresses
stay unlocated. Geocoding data © OpenStreetMap contributors.

`gdom` runs inside `cycle`, not as a separate service or quadlet: it is configured
with `crawl_delay_mode: "defer"` and `hydrate_details: false`. Its robots.txt asks
for a 20-minute crawl delay, so the client records the last request time per host
and a pass makes at most one request, stopping with a `deferred` stat when the
delay has not elapsed. The adapter rotates one category per pass/day, and
`refresh_minutes: 1440` in `config/sources.json` spreads the fourteen categories
over two weeks. Detail hydration is skipped because search cards already carry
coordinates and the address.

PDF-backed sources use `pdf.py`: attachments are fetched through the same
polite HTTP client, cached by SHA-256 under `data/cache/pdf/`, and parsed with
`pdfplumber` into text and tables. The first adapter on this lane is
`zgm-gliwice`; documents without a text layer are reported as `needs_ocr`
instead of being guessed.

`run-all` runs every enabled adapter, optionally filtered with `--tier`, and keeps
going when one source fails. `probe` fetches one page per source and reports HTTP
status, JSON-LD/Next.js data, link count and challenge detection; its report is
written to `data/out/source-probes.json`.

`review-sources` refetches the catalogued URL of every `blocked`/`planned` source,
fingerprints the response and diffs it against the previous report in
`data/out/source-review.json`. It flags cleared/appeared challenges, new listing
links, JSON-LD, coordinates and street addresses, so a source that starts
publishing properties is noticed instead of staying parked forever. `--due` only
checks sources past `last_verified + recheck_days`; the monthly
`plaszczaki-review.timer` runs it in the container.

`field-audit` reads the local database without network requests. It reports
per-source coverage for core, attribute, rental, auction and temporal fields,
weights gaps by analytics impact, and inventories raw keys preserved in
`listing.extra`. The JSON report is written to `data/out/field-audit.json`.
`probe --fields` is the optional network counterpart: it parses the first search
page, inventories raw structured keys/labels, and reports canonical field
coverage from the adapter. It does not maintain a generic field-alias registry.

Adapters preserve source-specific parameter payloads in `extra`; the shared
normalizer in `attributes.py` maps them to dedicated columns for condition,
building type/material, heating, windows, ownership, energy certificate,
ceiling height, building floors, elevator, parking, balcony/terrace/garden,
furnishing, pets, air conditioning, security, internet, fencing, administrative
rent, availability and auction appraisal. Fresh writes and maintenance use the
same mapping.

After applying the schema migration, run `backfill-attributes --dry-run` and then
`backfill-attributes`. It is idempotent, supports `--source`/`--limit`, fills
attributes plus missing market, deposit and derived price-per-m² values, and
does not change `first_seen_at`/`last_seen_at` or create scrape observations.
Historical Otodom rows did not store reverse-geocoded districts, so
`--rehydrate-district` refetches stored detail URLs only for rows still missing
a district. Rehydration is opt-in, rate-limited and uses the normal scraper lock.

`dedupe --backfill` recomputes `listing.canonical_key` for stored rows. The key is
derived from the case number and auction date, coordinates, or the normalized
street address; the map endpoints show one representative per key while every
source keeps its own row.

`--mode run` (the default) fetches each search page, hydrates that page's detail
pages and upserts the parsed offers as a batch. `--mode backfill` is the same
long, resumable full job. `--mode discover` is diagnostics only and writes
nothing. `--limit` caps parsed offers per run, `--max-pages` caps discovery per
query, and `--dry-run` prints records without writing.

## Configuration

- `config/sources.json` — the single config file: `defaults.rps` (rate fallback)
  plus one entry per source. Catalog fields are `id`, `label`, `kind`, `tier`,
  `status`, `enabled`, `urls`, `notes`, `last_verified`, `recheck_days` and
  `refresh_minutes`; `implemented` entries add their adapter settings in the same
  object: `adapter`, `rps`, `incremental`, `incremental_pages`, `newest_param`,
  `newest_value`, `hydrate_details`, `fetch_mode` (`http`, or `stealth` for
  Scrapling's Patchright Chromium session), `browser_headless` (default `true`),
  `browser_solve_cloudflare` (default `false`), `browser_wait_ms` (default `0`,
  used to wait out Imperva/JS challenges) and `crawl_delay_mode` (`sleep`,
  or `defer` to stop instead of sleeping). Parked sources keep only the catalog
  fields.
- `config/regions.json` — Małopolskie/Śląskie, TERYT codes, bboxes and place lists.

`PLASZCZAKI_CONFIG_DIR` overrides the config directory and
`PLASZCZAKI_SOURCES_CONFIG` points directly at a `sources.json` file.
`DATABASE_URL` points at the SQLite file (defaults to `data/plaszczaki.db`
under the repo root; the container uses `/data/plaszczaki.db`).
`PLASZCZAKI_CACHE_DIR` stores robots cache and reports (default `data/cache`).

## Database

The web app owns the schema:

```bash
bun run db:push        # apply schema to an existing dev DB
bun run db:migrate     # fresh DB / migration workflow
```

Only fully parsed offers are stored in `listing`; there is no listing lifecycle
status. Every row also carries `canonical_key`: auction notices key on case number
+ date, geolocated offers on rounded coordinates + area, and address-only offers
on the normalized street. The scraper never merges rows, so per-source provenance,
snapshots and run stats stay intact; the map endpoints select one representative
per key (complete data first, then newest). The scraper checks the expected tables/columns at startup and fails with
a clear message if the migration has not been applied.

## Attractiveness scoring

`score` is deterministic and training-free. It writes six independent 0–100
profiles to `listing` (`kamienica`, `nowe_mieszkanie`, `dzialka_kolej`,
`dom_airbnb`, `dom_kolej`, `dzialka_airbnb`) plus the best score and Polish
explanations. Every profile has an explicit 100-point budget in
`config/scoring.json`; price, location, attributes, nature and market opportunity
are scaled to that budget. `score_breakdown` stores all contributions, penalties,
caps, eligibility and missing-data notes needed to reconstruct the final score.

Inputs include canonical attributes, listing text, local price quantiles and
distance-weighted comparable sales from this database, auction discounts,
days on market, price cuts, gross rental yield, GTFS rail service, BDOT10k
forest/water data, OSM POIs, mountain regions and the map's planned-rail snapshots.
Changed listings are invalidated automatically and rescored by `watch`, `run`
and `run-all`; use `score --pending` for a manual catch-up.

`geodata` refreshes the open-data cache under `data/cache/`. GTFS comes from
PolishTrainsGTFS (PolRegio, PKP Intercity, Koleje Śląskie/Małopolskie and other
operators); nature uses BDOT10k (regional FlatGeoBuf/SHP) with OpenStreetMap as
a fallback, and POIs come from OpenStreetMap. Missing cache files degrade
to neutral geo signals rather than failing the scrape.

`llm enrich` extracts objective facts from public descriptions with any approved
OpenCode Go `-free` model and caches them in `llm_job`. `llm rerank` re-ranks the
deterministic shortlist using the user's preference profile. No model training
is performed. Set `OPENCODE_API_KEY` to enable these optional commands.

The web app exposes the score in map popups and filters, and `/oferty` shows the
top 25 scored sale/auction offers first seen each day in three columns:
apartments, houses and plots. Houses and plots can be ranked independently for
Airbnb or rail proximity, and every card exposes its full score breakdown.

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

The container defaults to `cycle --limit 200`, which is what the five-minute
timer runs. Override the command for a backfill or a dry run.

The image installs Patchright Chromium at `/opt/patchright` for
`fetch_mode: "stealth"` sources; the browser is read-only at runtime and uses
`/tmp` for its profile. No Camoufox/Firefox layer is required.

Scheduled runs are defined in `../deploy/quadlet/` and installed to:

- `~/.config/containers/systemd/plaszczaki-scraper.container`
- `~/.config/systemd/user/plaszczaki-scraper.timer`
- `~/.config/containers/systemd/plaszczaki-full.container`
- `~/.config/systemd/user/plaszczaki-full.timer`
- `~/.config/containers/systemd/plaszczaki-review.container`
- `~/.config/systemd/user/plaszczaki-review.timer`

`bun run deploy` copies and enables all timers: the scraper runs `cycle --limit
200` every five minutes, `plaszczaki-full.timer` runs the nightly
`run-all --newest --only-non-incremental` pass at 02:30 with its own lock file,
and `plaszczaki-review.timer` runs the monthly parked-source review. There is no
gdom unit; gdom is a slow lane inside `cycle`. The scraper container binds
`data/` read-write so it can write the database and robots cache; the full-pass
container writes concurrently through SQLite WAL.

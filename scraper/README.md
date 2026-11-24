# plaszczaki-scraper

Python sidecar for data collection. Runs in a rootless Podman container managed by
systemd/Quadlet; it writes JSONL artifacts to `../data/out` on the host. Ingestion into
the app's SQLite database is done by the Bun side and is not part of this project yet.

Scrapling is intentionally not installed yet.

## Toolchain

- Python 3.15 (uv-managed; see `.python-version`)
- uv 0.13+
- Ruff for linting and formatting
- ty for type checking (beta, pinned exactly)

## Commands

Run from the repository root:

```bash
bun run py:sync        # uv sync --locked
bun run py:lint        # ruff check
bun run py:format      # ruff format
bun run py:typecheck   # ty check
bun run py:check       # lint + format check + typecheck
```

Or from this directory:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run python -m plaszczaki_scraper
```

## Sources config

`config/sources.json` holds the list of URLs the scraper may target:

```json
{ "urls": ["https://www.otodom.pl"] }
```

It is the single source of truth — the Bun app reads the same file through
`src/lib/sources` and validates it with Zod, and `bun run sources:validate` checks it.

```python
from plaszczaki_scraper.config import load_source_urls

urls = load_source_urls()
```

Set `PLASZCZAKI_SOURCES_CONFIG` to point at a different file.

## Container

```bash
podman build -t localhost/plaszczaki-scraper:0.1.0 -f ../deploy/containers/scraper.Containerfile .
podman run --rm localhost/plaszczaki-scraper:0.1.0
```

Scheduled runs are defined outside the repository. The source units live in
`../deploy/quadlet/` and are installed to:

- `~/.config/containers/systemd/plaszczaki-scraper.container`
- `~/.config/systemd/user/plaszczaki-scraper.timer`

The timer is installed but disabled until the first real spider exists.

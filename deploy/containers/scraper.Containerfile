# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.13 AS uv

FROM docker.io/library/python:3.15-slim

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY config ./config
RUN uv sync --locked --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=/data/plaszczaki.db \
    PLASZCZAKI_CACHE_DIR=/data/cache

# Stealth fetching (fetch_mode="stealth") needs a browser. No adapter enables it
# yet, so the image stays lean. To use it, install Chromium at build time:
#   RUN python -m playwright install --with-deps chromium
# and set PLAYWRIGHT_BROWSERS_PATH=/ms-playwright (plus SCRAPER_CHROMIUM_PATH if
# you use a system Chromium instead).

ENTRYPOINT ["python", "-m", "plaszczaki_scraper"]
CMD ["watch", "--once", "--limit", "200"]

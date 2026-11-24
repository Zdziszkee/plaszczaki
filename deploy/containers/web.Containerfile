# syntax=docker/dockerfile:1

FROM docker.io/oven/bun:1 AS build

WORKDIR /app

COPY package.json bun.lock ./
COPY web/package.json ./web/package.json
# No install scripts: better-sqlite3 (devDependency, drizzle-kit CLI only) would
# need python/gyp here, while the shipped app uses bun:sqlite and never loads it.
RUN bun install --frozen-lockfile --ignore-scripts

COPY web ./web

ARG VITE_MAPBOX_TOKEN
ENV VITE_MAPBOX_TOKEN=$VITE_MAPBOX_TOKEN

RUN bun run --cwd web build

FROM docker.io/oven/bun:1-slim

WORKDIR /app

COPY --from=build /app/web/.output ./web/.output

ENV NODE_ENV=production \
    PORT=3000 \
    DATABASE_URL=/data/plaszczaki.db

VOLUME ["/data"]
EXPOSE 3000

CMD ["bun", "web/.output/server/index.mjs"]

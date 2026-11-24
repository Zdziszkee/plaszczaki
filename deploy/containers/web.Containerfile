# syntax=docker/dockerfile:1

FROM oven/bun:1 AS build

WORKDIR /app

COPY package.json bun.lock ./
COPY web/package.json ./web/package.json
RUN bun install --frozen-lockfile

COPY web ./web

ARG VITE_MAPBOX_TOKEN
ENV VITE_MAPBOX_TOKEN=$VITE_MAPBOX_TOKEN

RUN bun run --cwd web build

FROM oven/bun:1-slim

WORKDIR /app

COPY --from=build /app/web/.output ./web/.output

ENV NODE_ENV=production \
    PORT=3000 \
    DATABASE_URL=/data/plaszczaki.db

VOLUME ["/data"]
EXPOSE 3000

CMD ["bun", "web/.output/server/index.mjs"]

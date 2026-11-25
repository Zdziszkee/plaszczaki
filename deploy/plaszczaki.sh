#!/usr/bin/env bash
# Single deploy command: stop the running plaszczaki containers, rebuild both
# images and start web + scraper again.
#
#   bun run deploy        # or: ./deploy/plaszczaki.sh
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
WEB_IMAGE=localhost/plaszczaki-web:0.1.0
SCRAPER_IMAGE=localhost/plaszczaki-scraper:0.1.0
DATA_DIR="$ROOT/data"
ENV_DIR=${PLASZCZAKI_ENV_DIR:-$HOME/.config/plaszczaki}
QUADLET_DIR=${PLASZCZAKI_QUADLET_DIR:-$HOME/.config/containers/systemd}
WEB_UNIT=plaszczaki-web.service
SCRAPER_UNIT=plaszczaki-scraper.service
SCRAPER_TIMER=plaszczaki-scraper.timer

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Read a value from web/.env.local without exporting anything.
env_local_value() {
	local key=$1 file="$ROOT/web/.env.local"
	[ -f "$file" ] || return 0
	sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$file" |
		tail -n1 |
		sed -E "s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/"
}

ensure_env() {
	mkdir -p "$ENV_DIR"
	chmod 700 "$ENV_DIR"

	if [ ! -f "$ENV_DIR/web.env" ]; then
		local secret url
		secret=$(env_local_value BETTER_AUTH_SECRET)
		url=$(env_local_value BETTER_AUTH_URL)
		[ -n "$secret" ] || die "BETTER_AUTH_SECRET missing in web/.env.local; cannot create $ENV_DIR/web.env"
		log "creating $ENV_DIR/web.env"
		(
			umask 077
			cat >"$ENV_DIR/web.env" <<EOF
# Runtime env for the plaszczaki-web Quadlet.
DATABASE_URL=/data/plaszczaki.db
BETTER_AUTH_URL=${url:-http://localhost:3000}
BETTER_AUTH_SECRET=$secret
EOF
		)
	fi

	if [ ! -f "$ENV_DIR/scraper.env" ]; then
		log "creating $ENV_DIR/scraper.env"
		(
			umask 077
			cat >"$ENV_DIR/scraper.env" <<'EOF'
# Runtime config for the plaszczaki-scraper Quadlet.
DATABASE_URL=/data/plaszczaki.db
PLASZCZAKI_CACHE_DIR=/data/cache
# SCRAPER_USER_AGENT=
# OPENCODE_API_KEY=
# LLM_MODEL_CHAIN=hy4-flash-free,deepseek-v4.1-flash-free,beam-free
# LLM_ALLOW_RETENTION_FREE_MODELS=false
# LLM_ALLOW_PAID_FALLBACK=false
EOF
		)
	fi

	mkdir -p "$DATA_DIR/out" "$DATA_DIR/cache"
}

stop_services() {
	log "stopping $WEB_UNIT and $SCRAPER_UNIT"
	systemctl --user stop "$WEB_UNIT" "$SCRAPER_UNIT" "$SCRAPER_TIMER" 2>/dev/null || true
}

build() {
	local token
	token=$(env_local_value VITE_MAPBOX_TOKEN)
	[ -n "$token" ] || warn "VITE_MAPBOX_TOKEN is empty in web/.env.local; maps will not render"

	log "building $WEB_IMAGE"
	podman build \
		--build-arg "VITE_MAPBOX_TOKEN=$token" \
		-f "$ROOT/deploy/containers/web.Containerfile" \
		-t "$WEB_IMAGE" "$ROOT"

	log "building $SCRAPER_IMAGE"
	podman build \
		-f "$ROOT/deploy/containers/scraper.Containerfile" \
		-t "$SCRAPER_IMAGE" "$ROOT/scraper"
}

install_quadlets() {
	mkdir -p "$QUADLET_DIR"
	local f
	for f in "$ROOT"/deploy/quadlet/*.container "$ROOT"/deploy/quadlet/*.timer; do
		install -Dm644 "$f" "$QUADLET_DIR/$(basename "$f")"
	done
	log "reloading systemd user units"
	systemctl --user daemon-reload
}

start_services() {
	log "starting $WEB_UNIT"
	systemctl --user start "$WEB_UNIT"

	log "starting $SCRAPER_UNIT and enabling $SCRAPER_TIMER"
	systemctl --user start "$SCRAPER_UNIT"
	systemctl --user enable --now "$SCRAPER_TIMER"
}

print_status() {
	echo
	systemctl --user show "$WEB_UNIT" -p ActiveState -p SubState --value 2>/dev/null |
		paste -sd' ' - |
		awk '{printf "\033[1mweb:\033[0m %s\n", $0}'
	systemctl --user show "$SCRAPER_UNIT" -p ActiveState -p SubState --value 2>/dev/null |
		paste -sd' ' - |
		awk '{printf "\033[1mscraper:\033[0m %s (one-shot)\n", $0}'
	systemctl --user show "$SCRAPER_TIMER" -p ActiveState -p SubState -p NextElapseUSecRealtime --value 2>/dev/null |
		paste -sd' ' - |
		awk '{printf "\033[1mscraper timer:\033[0m %s\n", $0}'
	podman ps -a --filter name=plaszczaki --format '  {{.Names}}  {{.Status}}  {{.Ports}}' || true
	local code
	code=$(curl -fsS -o /dev/null -w '%{http_code}' http://localhost:3000/ 2>/dev/null || true)
	[ -n "$code" ] && [ "$code" != 000 ] && printf '\033[1mhttp:\033[0m http://localhost:3000 -> %s\n' "$code"
	echo
}

ensure_env
stop_services
build
install_quadlets
start_services
print_status

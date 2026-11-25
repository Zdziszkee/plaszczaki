#!/usr/bin/env bash
# One-command deploy for plaszczaki: build images, install Quadlets, start web.
#
#   deploy/plaszczaki.sh up            # build (cached), install Quadlets, start web + scraper
#   deploy/plaszczaki.sh up --no-build # start without rebuilding
#   deploy/plaszczaki.sh down          # stop both services
#   deploy/plaszczaki.sh status        # systemd + container state
#   deploy/plaszczaki.sh restart       # restart web only
#   deploy/plaszczaki.sh restart-all   # restart web and start a fresh scraper run
#   deploy/plaszczaki.sh logs [web|scraper|all]  # follow logs (default web)
#   deploy/plaszczaki.sh build         # rebuild images only
#   deploy/plaszczaki.sh uninstall     # stop, remove Quadlets from ~/.config/containers/systemd
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

usage() {
	sed -n '2,12s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"
	exit "${1:-0}"
}

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

cmd_up() {
	local do_build=1
	case "${1:-}" in
	--no-build) do_build=0 ;;
	"") ;;
	*) usage 2 ;;
	esac

	ensure_env
	[ "$do_build" -eq 1 ] && build
	install_quadlets

	log "starting $WEB_UNIT"
	systemctl --user start "$WEB_UNIT"
	log "starting $SCRAPER_UNIT and enabling $SCRAPER_TIMER"
	systemctl --user start "$SCRAPER_UNIT"
	systemctl --user enable --now "$SCRAPER_TIMER"

	cmd_status
}

cmd_down() {
	log "stopping plaszczaki services"
	systemctl --user stop "$WEB_UNIT" "$SCRAPER_UNIT" "$SCRAPER_TIMER" 2>/dev/null || true
	cmd_status
}

cmd_restart() {
	ensure_env
	systemctl --user daemon-reload
	systemctl --user restart "$WEB_UNIT"
	cmd_status
}

cmd_restart_all() {
	ensure_env
	install_quadlets

	log "restarting $WEB_UNIT"
	systemctl --user restart "$WEB_UNIT"

	log "restarting $SCRAPER_UNIT (one-shot; starts a fresh run)"
	systemctl --user reset-failed "$SCRAPER_UNIT" 2>/dev/null || true
	systemctl --user restart --no-block "$SCRAPER_UNIT"
	systemctl --user enable --now "$SCRAPER_TIMER"

	cmd_status
}

cmd_status() {
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

cmd_logs() {
	local target="${1:-web}"
	case "$target" in
	web)
		journalctl --user -u "$WEB_UNIT" -f
		;;
	scraper)
		journalctl --user -u "$SCRAPER_UNIT" -f
		;;
	all)
		journalctl --user -u "$WEB_UNIT" -u "$SCRAPER_UNIT" -f
		;;
	*)
		die "unknown log target '$target' (use web, scraper or all)"
		;;
	esac
}

cmd_uninstall() {
	cmd_down >/dev/null || true
	log "removing Quadlets from $QUADLET_DIR"
	rm -f "$QUADLET_DIR"/plaszczaki-*.container "$QUADLET_DIR"/plaszczaki-*.timer
	systemctl --user daemon-reload
	log "done (images and $ENV_DIR kept)"
}

case "${1:-}" in
up) shift; cmd_up "$@" ;;
down) cmd_down ;;
restart) cmd_restart ;;
restart-all) cmd_restart_all ;;
status) cmd_status ;;
logs) shift; cmd_logs "$@" ;;
build) build ;;
uninstall) cmd_uninstall ;;
-h | --help | help) usage 0 ;;
*) usage 2 ;;
esac

"""Configuration loading for the scraper.

The portal inventory and the settings of implemented adapters live together in
``scraper/config/sources.json``. The geographic scope lives in
``scraper/config/regions.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

CONFIG_ENV_VAR = "PLASZCZAKI_SOURCES_CONFIG"
CONFIG_DIR_ENV_VAR = "PLASZCZAKI_CONFIG_DIR"
CONFIG_RELATIVE_PATH = Path("config") / "sources.json"
REGIONS_RELATIVE_PATH = Path("config") / "regions.json"

SOURCE_KINDS = {
    "portal",
    "rental",
    "primary",
    "commercial",
    "luxury",
    "auction",
    "government",
    "aggregator",
    "short-term",
    "non-property",
}
SOURCE_STATUSES = {"implemented", "planned", "blocked", "excluded"}

# Adapter settings may only appear on sources with status "implemented".
ADAPTER_FIELDS = {
    "adapter",
    "rps",
    "incremental",
    "incremental_pages",
    "newest_param",
    "newest_value",
    "hydrate_details",
    "fetch_mode",
    "browser_headless",
    "browser_solve_cloudflare",
    "browser_wait_ms",
    "crawl_delay_mode",
}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One catalogued source, with its adapter settings when implemented."""

    id: str
    label: str
    kind: str
    tier: int
    status: str
    enabled: bool
    urls: tuple[str, ...]
    notes: str = ""
    blocker: str | None = None
    last_verified: str | None = None
    recheck_days: int = 90
    refresh_minutes: int | None = None
    adapter: str | None = None
    rps: float = 1.0
    incremental: bool = False
    incremental_pages: int = 2
    newest_param: str | None = None
    newest_value: str | None = None
    # Some sources (e.g. gdom with a 20-minute crawl delay) are complete enough
    # on search cards, so fetching one detail page per listing is not worth it.
    hydrate_details: bool = True
    # "stealth" runs the fetch through Scrapling's Patchright Chromium engine.
    fetch_mode: Literal["http", "stealth"] = "http"
    # Scrapling stealth options; ignored for fetch_mode="http".
    browser_headless: bool = True
    browser_solve_cloudflare: bool = False
    browser_wait_ms: int = 0
    # "defer" makes a run stop when robots.txt crawl delay has not elapsed
    # instead of sleeping inside the process (used by the gdom slow lane).
    crawl_delay_mode: Literal["sleep", "defer"] = "sleep"


@dataclass(frozen=True, slots=True)
class SourcesConfig:
    """The complete source inventory, implemented or not."""

    defaults_rps: float
    sources: dict[str, SourceConfig]

    def urls(self) -> list[str]:
        return [url for source in self.sources.values() for url in source.urls]

    def implemented(self) -> list[SourceConfig]:
        return [source for source in self.sources.values() if source.status == "implemented"]

    def by_tier(self, tier: int) -> list[SourceConfig]:
        return [source for source in self.sources.values() if source.tier == tier]

    def enabled_adapters(self) -> list[SourceConfig]:
        return [
            source
            for source in sorted(self.sources.values(), key=lambda item: item.id)
            if source.enabled and source.adapter is not None
        ]


@dataclass(frozen=True, slots=True)
class RegionConfig:
    """One voivodeship in the scrape scope."""

    id: str
    name: str
    teryt: str
    bbox: tuple[float, float, float, float]
    aliases: tuple[str, ...]
    counties: tuple[str, ...]
    cities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegionsConfig:
    """The geographic scope and its lookup data."""

    default: tuple[str, ...]
    regions: dict[str, RegionConfig]


_REPO_MARKERS = ("web/src/db/schema.ts", "scraper/pyproject.toml")


def _is_repo_root(candidate: Path) -> bool:
    return all((candidate / marker).is_file() for marker in _REPO_MARKERS)


def repo_root() -> Path:
    """Return the repository root (the parent of ``scraper/``).

    In an editable install that is simply the directory three levels above this
    module. A non-editable install (the container image) puts the package under
    ``.venv/lib/...``, where the module path no longer points at the checkout, so
    fall back to walking up from the working directory. Explicit environment
    overrides (``DATABASE_URL``, ``PLASZCZAKI_CACHE_DIR``) win over both.
    """
    module_root = Path(__file__).resolve().parents[3]
    if _is_repo_root(module_root):
        return module_root

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _is_repo_root(candidate):
            return candidate
    return module_root


def _config_dirs() -> list[Path]:
    override = os.environ.get(CONFIG_DIR_ENV_VAR)
    if override:
        return [Path(override).expanduser().resolve()]

    starts = (Path(__file__).resolve(), Path.cwd().resolve(), repo_root())
    dirs: list[Path] = []
    for start in starts:
        for parent in (start, *start.parents):
            candidate = parent / "config"
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)
    return dirs


def find_config_path(relative_path: Path = CONFIG_RELATIVE_PATH) -> Path:
    """Locate a config file from the sources override, package, or CWD."""
    if relative_path == CONFIG_RELATIVE_PATH:
        override = os.environ.get(CONFIG_ENV_VAR)
        if override:
            return Path(override).expanduser().resolve()

    filename = relative_path.name
    for config_dir in _config_dirs():
        candidate = config_dir / filename
        if candidate.is_file():
            return candidate

    raise ConfigError(
        f"Config not found (looked for {relative_path}); set {CONFIG_DIR_ENV_VAR} to override",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config is not valid JSON: {path}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"Config must be a JSON object: {path}")
    return payload


def normalize_url(url: str) -> str:
    """Normalize a URL the same way the Zod schema does (for duplicate checks)."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{host}{path}{query}"


def load_sources_config(path: Path | None = None) -> SourcesConfig:
    """Load and validate the complete source inventory."""
    config_path = path or find_config_path()
    payload = _read_json(config_path)

    unknown = set(payload) - {"defaults", "sources"}
    if unknown:
        raise ConfigError(f"Unknown keys in sources config: {sorted(unknown)}")

    defaults = payload.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError("'defaults' must be an object")
    unknown = set(defaults) - {"rps"}
    if unknown:
        raise ConfigError(f"Unknown keys in defaults: {sorted(unknown)}")

    defaults_rps = defaults.get("rps", 1.0)
    if (
        not isinstance(defaults_rps, int | float)
        or isinstance(defaults_rps, bool)
        or defaults_rps <= 0
    ):
        raise ConfigError("defaults.rps must be a positive number")

    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("'sources' must be a non-empty array")

    seen_urls: dict[str, str] = {}
    sources: dict[str, SourceConfig] = {}

    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise ConfigError(f"sources[{index}] must be an object")

        context = f"sources[{index}]"
        unknown = set(raw) - {
            "id",
            "label",
            "kind",
            "tier",
            "status",
            "enabled",
            "urls",
            "notes",
            "blocker",
            "last_verified",
            "recheck_days",
            "refresh_minutes",
            *ADAPTER_FIELDS,
        }
        if unknown:
            raise ConfigError(f"Unknown keys in {context}: {sorted(unknown)}")

        source_id = _require_string(raw, "id", context)
        if source_id in sources:
            raise ConfigError(f"duplicate source id '{source_id}'")
        context = f"sources.{source_id}"

        kind = _require_string(raw, "kind", context)
        if kind not in SOURCE_KINDS:
            raise ConfigError(f"{context}.kind must be one of {sorted(SOURCE_KINDS)}")

        status = _require_string(raw, "status", context)
        if status not in SOURCE_STATUSES:
            raise ConfigError(f"{context}.status must be one of {sorted(SOURCE_STATUSES)}")

        tier = raw.get("tier")
        if not isinstance(tier, int) or isinstance(tier, bool) or tier < 1:
            raise ConfigError(f"{context}.tier must be a positive integer")

        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{context}.enabled must be a boolean")
        if status in {"excluded", "blocked"} and enabled:
            raise ConfigError(f"{context} cannot be enabled while status is '{status}'")

        notes = raw.get("notes", "")
        if not isinstance(notes, str):
            raise ConfigError(f"{context}.notes must be a string")

        blocker = raw.get("blocker")
        if blocker is not None and not isinstance(blocker, str):
            raise ConfigError(f"{context}.blocker must be a string")

        last_verified = raw.get("last_verified")
        if last_verified is not None and not isinstance(last_verified, str):
            raise ConfigError(f"{context}.last_verified must be an ISO date string")

        recheck_days = raw.get("recheck_days", 90)
        if not isinstance(recheck_days, int) or isinstance(recheck_days, bool):
            raise ConfigError(f"{context}.recheck_days must be an integer")
        if recheck_days < 1:
            raise ConfigError(f"{context}.recheck_days must be positive")

        adapter_fields_present = sorted(set(raw) & ADAPTER_FIELDS)
        if status == "implemented":
            adapter = _require_string(raw, "adapter", context)

            rps = raw.get("rps", defaults_rps)
            if not isinstance(rps, int | float) or isinstance(rps, bool) or rps <= 0:
                raise ConfigError(f"{context}.rps must be a positive number")

            incremental = raw.get("incremental", False)
            if not isinstance(incremental, bool):
                raise ConfigError(f"{context}.incremental must be a boolean")

            incremental_pages = raw.get("incremental_pages", 2)
            if not isinstance(incremental_pages, int) or isinstance(incremental_pages, bool):
                raise ConfigError(f"{context}.incremental_pages must be an integer")
            if incremental_pages < 1:
                raise ConfigError(f"{context}.incremental_pages must be at least 1")

            newest_param = raw.get("newest_param")
            if newest_param is not None and (not isinstance(newest_param, str) or not newest_param):
                raise ConfigError(f"{context}.newest_param must be a non-empty string")

            newest_value = raw.get("newest_value")
            if newest_value is not None and (not isinstance(newest_value, str) or not newest_value):
                raise ConfigError(f"{context}.newest_value must be a non-empty string")

            hydrate_details = raw.get("hydrate_details", True)
            if not isinstance(hydrate_details, bool):
                raise ConfigError(f"{context}.hydrate_details must be a boolean")

            fetch_mode = raw.get("fetch_mode", "http")
            if fetch_mode not in {"http", "stealth"}:
                raise ConfigError(f"{context}.fetch_mode must be 'http' or 'stealth'")

            browser_headless = raw.get("browser_headless", True)
            if not isinstance(browser_headless, bool):
                raise ConfigError(f"{context}.browser_headless must be a boolean")

            browser_solve_cloudflare = raw.get("browser_solve_cloudflare", False)
            if not isinstance(browser_solve_cloudflare, bool):
                raise ConfigError(f"{context}.browser_solve_cloudflare must be a boolean")

            browser_wait_ms = raw.get("browser_wait_ms", 0)
            if not isinstance(browser_wait_ms, int) or isinstance(browser_wait_ms, bool):
                raise ConfigError(f"{context}.browser_wait_ms must be an integer")
            if browser_wait_ms < 0:
                raise ConfigError(f"{context}.browser_wait_ms must be zero or positive")

            crawl_delay_mode = raw.get("crawl_delay_mode", "sleep")
            if crawl_delay_mode not in {"sleep", "defer"}:
                raise ConfigError(f"{context}.crawl_delay_mode must be 'sleep' or 'defer'")
        else:
            if adapter_fields_present:
                raise ConfigError(
                    f"{context} has adapter fields {adapter_fields_present} "
                    f"but status is '{status}'",
                )
            adapter = None
            rps = float(defaults_rps)
            incremental = False
            incremental_pages = 2
            newest_param = None
            newest_value = None
            hydrate_details = True
            fetch_mode = "http"
            browser_headless = True
            browser_solve_cloudflare = False
            browser_wait_ms = 0
            crawl_delay_mode = "sleep"

        refresh_minutes = raw.get("refresh_minutes")
        if refresh_minutes is None:
            refresh_minutes = 60 if adapter is not None else None
        elif not isinstance(refresh_minutes, int) or isinstance(refresh_minutes, bool):
            raise ConfigError(f"{context}.refresh_minutes must be an integer")
        elif refresh_minutes < 1:
            raise ConfigError(f"{context}.refresh_minutes must be positive")

        urls = raw.get("urls")
        if not isinstance(urls, list) or not urls:
            raise ConfigError(f"{context}.urls must be a non-empty array")

        source_urls: list[str] = []
        for url_index, url in enumerate(urls):
            if not isinstance(url, str):
                raise ConfigError(f"{context}.urls[{url_index}] must be a string")

            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.netloc:
                raise ConfigError(
                    f"{context}.urls[{url_index}] must be an https URL: {url}",
                )

            key = normalize_url(url)
            if key in seen_urls:
                raise ConfigError(
                    f"{context}.urls[{url_index}] duplicates {seen_urls[key]}: {key}",
                )
            seen_urls[key] = f"{context}.urls[{url_index}]"
            source_urls.append(url)

        sources[source_id] = SourceConfig(
            id=source_id,
            label=_require_string(raw, "label", context),
            kind=kind,
            tier=tier,
            status=status,
            enabled=enabled,
            urls=tuple(source_urls),
            notes=notes,
            blocker=blocker,
            last_verified=last_verified,
            recheck_days=recheck_days,
            refresh_minutes=refresh_minutes,
            adapter=adapter,
            rps=float(rps),
            incremental=incremental,
            incremental_pages=incremental_pages,
            newest_param=newest_param,
            newest_value=newest_value,
            hydrate_details=hydrate_details,
            fetch_mode=fetch_mode,
            browser_headless=browser_headless,
            browser_solve_cloudflare=browser_solve_cloudflare,
            browser_wait_ms=browser_wait_ms,
            crawl_delay_mode=crawl_delay_mode,
        )

    return SourcesConfig(defaults_rps=float(defaults_rps), sources=sources)


def load_source_urls(path: Path | None = None) -> list[str]:
    """Load every source URL from the catalog."""
    return load_sources_config(path).urls()


def _require_string(payload: dict[str, Any], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return value


def load_regions_config(path: Path | None = None) -> RegionsConfig:
    """Load the geographic scope."""
    config_path = path or find_config_path(REGIONS_RELATIVE_PATH)
    payload = _read_json(config_path)

    unknown = set(payload) - {"default", "voivodeships"}
    if unknown:
        raise ConfigError(f"Unknown keys in regions config: {sorted(unknown)}")

    default = payload.get("default")
    if not isinstance(default, list) or not default:
        raise ConfigError("'default' must be a non-empty array")
    if not all(isinstance(item, str) for item in default):
        raise ConfigError("'default' must contain strings only")

    raw_regions = payload.get("voivodeships")
    if not isinstance(raw_regions, dict) or not raw_regions:
        raise ConfigError("'voivodeships' must be a non-empty object")

    regions: dict[str, RegionConfig] = {}
    for region_id, raw in raw_regions.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"voivodeships.{region_id} must be an object")

        context = f"voivodeships.{region_id}"
        unknown = set(raw) - {"name", "teryt", "bbox", "aliases", "counties", "cities"}
        if unknown:
            raise ConfigError(f"Unknown keys in {context}: {sorted(unknown)}")

        bbox = raw.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(isinstance(value, int | float) for value in bbox)
        ):
            raise ConfigError(f"{context}.bbox must be [min_lon, min_lat, max_lon, max_lat]")

        aliases = raw.get("aliases", [])
        counties = raw.get("counties", [])
        cities = raw.get("cities", [])
        for name, values in (
            ("aliases", aliases),
            ("counties", counties),
            ("cities", cities),
        ):
            if not isinstance(values, list) or not all(
                isinstance(item, str) and item for item in values
            ):
                raise ConfigError(f"{context}.{name} must be an array of strings")

        regions[region_id] = RegionConfig(
            id=region_id,
            name=_require_string(raw, "name", context),
            teryt=_require_string(raw, "teryt", context),
            bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            aliases=tuple(aliases),
            counties=tuple(counties),
            cities=tuple(cities),
        )

    missing = [region for region in default if region not in regions]
    if missing:
        raise ConfigError(f"'default' references unknown regions: {missing}")

    return RegionsConfig(default=tuple(default), regions=regions)

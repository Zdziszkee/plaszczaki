"""Source configuration for the scraper.

The single source of truth lives in ``scraper/config/sources.json`` and is shared
with the Bun app, which imports the same file and validates it with Zod.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_ENV_VAR = "PLASZCZAKI_SOURCES_CONFIG"
CONFIG_RELATIVE_PATH = Path("config") / "sources.json"


class ConfigError(RuntimeError):
    """Raised when the sources config is missing or invalid."""


def find_config_path() -> Path:
    """Locate ``config/sources.json`` from the environment, the package, or CWD."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()

    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in (start, *start.parents):
            candidate = parent / CONFIG_RELATIVE_PATH
            if candidate.is_file():
                return candidate

    raise ConfigError(
        f"Sources config not found (looked for {CONFIG_RELATIVE_PATH}); "
        f"set {CONFIG_ENV_VAR} to override",
    )


def normalize_url(url: str) -> str:
    """Normalize a URL the same way the Zod schema does (for duplicate checks)."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{host}{path}{query}"


def load_source_urls(path: Path | None = None) -> list[str]:
    """Load and validate the list of source URLs."""
    config_path = path or find_config_path()

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Sources config not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Sources config is not valid JSON: {config_path}") from exc

    if not isinstance(payload, dict):
        raise ConfigError("Sources config must be a JSON object")

    unknown = set(payload) - {"urls"}
    if unknown:
        raise ConfigError(f"Unknown keys in sources config: {sorted(unknown)}")

    urls = payload.get("urls")
    if not isinstance(urls, list) or not urls:
        raise ConfigError("'urls' must be a non-empty array")

    seen: dict[str, str] = {}

    for index, url in enumerate(urls):
        if not isinstance(url, str):
            raise ConfigError(f"urls[{index}] must be a string")

        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc:
            raise ConfigError(f"urls[{index}] must be an https URL: {url}")

        key = normalize_url(url)
        if key in seen:
            raise ConfigError(f"urls[{index}] duplicates {seen[key]}: {key}")
        seen[key] = f"urls[{index}]"

    return list(urls)

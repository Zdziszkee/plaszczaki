"""Configuration for the official GUGiK RCN GeoParquet exports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plaszczaki_scraper.config import repo_root

DEFAULT_CONFIG_PATH = repo_root() / "scraper" / "config" / "rcn.json"


@dataclass(frozen=True, slots=True)
class RcnConfig:
    source_page: str
    export_url: str
    object_kinds: tuple[str, ...]
    batch_rows: int
    grid_degrees: float
    request_timeout_seconds: float
    attribution: str
    comparables: dict[str, Any]

    def dataset_url(self, region_teryt: str, object_kind: str) -> str:
        return self.export_url.format(region=region_teryt, kind=object_kind)


def load_rcn_config(path: Path | None = None) -> RcnConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    object_kinds = tuple(str(value) for value in payload["object_kinds"])
    if not object_kinds or any(
        value not in {"dzialki", "budynki", "lokale"} for value in object_kinds
    ):
        raise ValueError("rcn.object_kinds must contain only dzialki, budynki or lokale")
    batch_rows = int(payload.get("batch_rows", 5000))
    grid_degrees = float(payload.get("grid_degrees", 0.02))
    if batch_rows < 1 or batch_rows > 50_000:
        raise ValueError("rcn.batch_rows must be between 1 and 50000")
    if not 0 < grid_degrees <= 0.1:
        raise ValueError("rcn.grid_degrees must be in (0, 0.1]")
    return RcnConfig(
        source_page=str(payload["source_page"]),
        export_url=str(payload["export_url"]),
        object_kinds=object_kinds,
        batch_rows=batch_rows,
        grid_degrees=grid_degrees,
        request_timeout_seconds=float(payload.get("request_timeout_seconds", 900)),
        attribution=str(payload["attribution"]),
        comparables=dict(payload.get("comparables", {})),
    )

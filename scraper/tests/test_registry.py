from __future__ import annotations

import pytest

from plaszczaki_scraper.adapters.registry import build_adapter
from plaszczaki_scraper.config import (
    SourceConfig,
    load_regions_config,
    load_sources_config,
)
from plaszczaki_scraper.regions import RegionRegistry


def _source_config(*, adapter: str = "otodom") -> SourceConfig:
    return SourceConfig(
        id="otodom",
        label="Otodom",
        kind="portal",
        tier=1,
        status="implemented",
        enabled=True,
        urls=("https://www.otodom.pl",),
        adapter=adapter,
    )


def test_build_otodom_adapter() -> None:
    adapter = build_adapter(
        _source_config(),
        regions=RegionRegistry(load_regions_config()),
    )
    assert adapter.id == "otodom"


def test_unknown_adapter_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter(
            _source_config(adapter="does-not-exist"),
            regions=RegionRegistry(load_regions_config()),
        )


def test_every_enabled_adapter_builds() -> None:
    registry = RegionRegistry(load_regions_config())
    catalog = load_sources_config()

    for source in catalog.enabled_adapters():
        adapter = build_adapter(source, regions=registry)
        assert adapter.id == source.id
        assert adapter.queries(("malopolskie", "slaskie"))

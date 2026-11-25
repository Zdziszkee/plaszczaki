from __future__ import annotations

import pytest

from plaszczaki_scraper.adapters.registry import build_adapter
from plaszczaki_scraper.config import (
    AdapterConfig,
    load_adapters_config,
    load_regions_config,
)
from plaszczaki_scraper.regions import RegionRegistry


def _adapter_config(*, adapter: str = "otodom") -> AdapterConfig:
    return AdapterConfig(
        id="otodom",
        adapter=adapter,
        label="Otodom",
        homepage="https://www.otodom.pl",
    )


def test_build_otodom_adapter() -> None:
    adapter = build_adapter(
        _adapter_config(),
        regions=RegionRegistry(load_regions_config()),
    )
    assert adapter.id == "otodom"


def test_unknown_adapter_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter(
            _adapter_config(adapter="does-not-exist"),
            regions=RegionRegistry(load_regions_config()),
        )


def test_every_enabled_adapter_builds() -> None:
    registry = RegionRegistry(load_regions_config())
    adapters = load_adapters_config()

    for source in adapters.enabled():
        adapter = build_adapter(source, regions=registry)
        assert adapter.id == source.id
        assert adapter.queries(("malopolskie", "slaskie"))

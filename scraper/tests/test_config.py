from __future__ import annotations

from plaszczaki_scraper.config import (
    load_adapters_config,
    load_regions_config,
    load_source_urls,
    load_sources_config,
    normalize_url,
)


def test_source_inventory() -> None:
    urls = load_source_urls()
    assert len(urls) == 161
    assert "https://www.otodom.pl" in urls


def test_sources_catalog() -> None:
    catalog = load_sources_config()
    assert len(catalog.sources) == 154
    assert len(catalog.urls()) == 161

    otodom = catalog.sources["otodom"]
    assert otodom.status == "implemented"
    assert otodom.enabled is True
    assert otodom.tier == 1
    assert otodom.kind == "portal"

    rentals = {source.id for source in catalog.sources.values() if source.kind == "short-term"}
    assert "booking" in rentals
    assert all(catalog.sources[source].status == "excluded" for source in rentals)

    assert all(
        source.status != "excluded" or not source.enabled for source in catalog.sources.values()
    )


def test_adapters_cover_catalog() -> None:
    catalog = load_sources_config()
    adapters = load_adapters_config()

    for source_id, adapter in adapters.sources.items():
        assert source_id in catalog.sources
        assert catalog.sources[source_id].status == "implemented"
        if adapter.enabled:
            assert catalog.sources[source_id].enabled

    for source in catalog.implemented():
        assert source.id in adapters.sources, f"{source.id} has no adapter config"


def test_adapters_config() -> None:
    config = load_adapters_config()
    assert config.defaults_rps == 1.0
    assert "otodom" in config.sources
    otodom = config.sources["otodom"]
    assert otodom.adapter == "otodom"
    assert otodom.enabled is True
    assert otodom.rps == 1.0
    assert otodom.poll_minutes == 10
    assert otodom.incremental is True
    assert otodom.incremental_pages == 2
    assert sum(1 for source in config.sources.values() if source.incremental) >= 15


def test_regions_config() -> None:
    config = load_regions_config()
    assert config.default == ("malopolskie", "slaskie")
    assert set(config.regions) == {"malopolskie", "slaskie"}
    assert config.regions["malopolskie"].teryt == "12"
    assert config.regions["slaskie"].teryt == "24"


def test_normalize_url() -> None:
    assert normalize_url("https://www.Otodom.pl/foo/") == "https://otodom.pl/foo"
    assert normalize_url("https://example.com/a?b=1") == "https://example.com/a?b=1"


def test_parked_sources_record_a_blocker_and_review_date() -> None:
    catalog = load_sources_config()

    for source in catalog.sources.values():
        if source.status == "blocked":
            assert source.blocker, f"{source.id} has no blocker category"
            assert source.last_verified, f"{source.id} has no last_verified date"
        if source.status == "planned":
            assert source.last_verified, f"{source.id} has no last_verified date"
        assert source.recheck_days > 0

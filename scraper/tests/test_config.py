from __future__ import annotations

from plaszczaki_scraper.config import (
    load_regions_config,
    load_source_urls,
    load_sources_config,
    normalize_url,
)


def test_source_inventory() -> None:
    urls = load_source_urls()
    assert len(urls) == 162
    assert "https://www.otodom.pl" in urls


def test_sources_catalog() -> None:
    catalog = load_sources_config()
    assert catalog.defaults_rps == 1.0
    assert len(catalog.sources) == 155
    assert len(catalog.urls()) == 162
    assert "txo" not in catalog.sources

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


def test_implemented_sources_have_adapters() -> None:
    catalog = load_sources_config()

    implemented = catalog.implemented()
    assert len(implemented) == 50
    for source in implemented:
        assert source.adapter is not None, f"{source.id} has no adapter"

    for source in catalog.sources.values():
        if source.status != "implemented":
            assert source.adapter is None, f"{source.id} is not implemented but has an adapter"


def test_adapter_settings() -> None:
    config = load_sources_config()
    assert config.defaults_rps == 1.0
    assert "otodom" in config.sources
    otodom = config.sources["otodom"]
    assert otodom.adapter == "otodom"
    assert otodom.enabled is True
    assert otodom.rps == 1.0
    assert otodom.refresh_minutes == 10
    assert otodom.incremental is True
    assert otodom.incremental_pages == 2
    assert otodom.hydrate_details is True
    assert otodom.fetch_mode == "http"
    assert otodom.browser_headless is True
    assert otodom.browser_solve_cloudflare is False
    assert otodom.crawl_delay_mode == "sleep"

    allegro = config.sources["allegrolokalnie"]
    assert allegro.fetch_mode == "stealth"
    assert allegro.rps == 0.1

    gdom = config.sources["gdom"]
    assert gdom.enabled is True
    assert gdom.incremental is True
    assert gdom.hydrate_details is False
    assert gdom.crawl_delay_mode == "defer"
    assert gdom.refresh_minutes == 1440
    assert sum(1 for source in config.sources.values() if source.incremental) >= 15


def test_enabled_adapters() -> None:
    config = load_sources_config()
    adapters = config.enabled_adapters()

    assert adapters
    assert [source.id for source in adapters] == sorted(source.id for source in adapters)
    for source in adapters:
        assert source.enabled is True
        assert source.adapter is not None

    enabled = {source.id for source in config.sources.values() if source.enabled}
    assert {source.id for source in adapters} == enabled


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

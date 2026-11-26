from __future__ import annotations

from plaszczaki_scraper.cli import _full_pass_order
from plaszczaki_scraper.config import SourceConfig, load_sources_config


def _sources() -> tuple[SourceConfig, SourceConfig, SourceConfig]:
    catalog = load_sources_config()
    return (
        catalog.sources["gratka"],
        catalog.sources["morizon"],
        catalog.sources["poczta-polska"],
    )


def test_full_pass_order_runs_never_seen_sources_first() -> None:
    gratka, morizon, poczta = _sources()

    ordered = _full_pass_order(
        [gratka, morizon, poczta],
        {"gratka": 100, "morizon": None, "poczta-polska": 50},
        {"gratka": 22, "morizon": 22, "poczta-polska": 4},
    )

    assert [source.id for source in ordered] == ["morizon", "poczta-polska", "gratka"]


def test_full_pass_order_breaks_never_seen_ties_by_query_count() -> None:
    gratka, morizon, _ = _sources()

    ordered = _full_pass_order(
        [gratka, morizon],
        {"gratka": None, "morizon": None},
        {"gratka": 22, "morizon": 2},
    )

    assert [source.id for source in ordered] == ["morizon", "gratka"]

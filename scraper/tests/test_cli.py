from __future__ import annotations

from plaszczaki_scraper.cli import _full_pass_order, _parser
from plaszczaki_scraper.config import SourceConfig, load_sources_config


def _sources() -> tuple[SourceConfig, SourceConfig, SourceConfig]:
    catalog = load_sources_config()
    return (
        catalog.sources["gratka"],
        catalog.sources["morizon"],
        catalog.sources["poczta-polska"],
    )


def test_signals_command_and_legacy_score_alias_share_options() -> None:
    parser = _parser()
    signals = parser.parse_args(["signals", "--pending", "--limit", "5"])
    legacy = parser.parse_args(["score", "--pending", "--limit", "5"])

    assert signals.command == "signals"
    assert legacy.command == "score"
    assert signals.pending is legacy.pending is True
    assert signals.limit == legacy.limit == 5


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

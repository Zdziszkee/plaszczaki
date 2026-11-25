"""Declared-vs-actual scrape coverage per source, region and transaction.

The catalog answers "which portals exist"; ``config/coverage.json`` answers
"which (region, offer type, property type) cells we intend to scrape there".
This module compares the declaration with the queries an adapter actually
emits and with the rows stored in SQLite, so gaps are visible instead of
implicit.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from plaszczaki_scraper.adapters.base import Adapter, Query
from plaszczaki_scraper.config import (
    AdapterConfig,
    AdaptersConfig,
    CoverageConfig,
    RegionsConfig,
)

OFFER_ORDER = ("sale", "rent", "lease", "auction", "other")
OFFER_FILTERS = ("sale", "rent", "lease", "auction")

StoredCounts = dict[tuple[str, str | None, str, str], int]
CellKey = tuple[str, str, str]  # (region, offer type, property type)


@dataclass(frozen=True, slots=True)
class Cell:
    """One coverage cell with its query and stored-row counts."""

    source_id: str
    region: str
    offer_type: str
    property_type: str
    declared: bool
    queries: int
    stored: int

    @property
    def state(self) -> str:
        if not self.declared:
            return "undeclared"
        if self.queries == 0:
            return "missing-query"
        if self.stored == 0:
            return "empty"
        return "ok"

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def filter_queries(queries: Iterable[Query], offer_types: Sequence[str]) -> list[Query]:
    """Keep only queries for the requested offer types (no filter when empty)."""
    if not offer_types:
        return list(queries)
    wanted = set(offer_types)
    return [query for query in queries if query.offer_type in wanted]


def adapter_cells(adapter: Adapter, regions: Sequence[str]) -> dict[CellKey, int]:
    """Count the queries an adapter emits for each cell, region by region.

    Each region is requested on its own: a nationwide feed must still answer a
    single-region run, and a source that silently drops one voivodeship then
    shows up as ``missing-query`` instead of hiding behind the other region.
    """
    counts: dict[CellKey, int] = defaultdict(int)
    for region in regions:
        for query in adapter.queries([region]):
            counts[(region, query.offer_type, query.property_type)] += 1
    return dict(counts)


def _stored_for(
    stored: StoredCounts,
    source_id: str,
    region: str,
    offer_type: str,
    property_type: str,
) -> int:
    """Stored rows for a cell, widening aggregate ``other`` axes."""
    exact = stored.get((source_id, region, offer_type, property_type))
    if exact:
        return exact
    total = 0
    for (row_source, row_region, row_offer, row_property), count in stored.items():
        if row_source != source_id or row_region != region:
            continue
        if offer_type != "other" and row_offer != offer_type:
            continue
        if property_type != "other" and row_property != property_type:
            continue
        total += count
    return total


def collect_cells(
    *,
    coverage: CoverageConfig,
    adapters: AdaptersConfig,
    regions: Sequence[str],
    stored: StoredCounts | None = None,
    include_disabled: bool = False,
) -> list[Cell]:
    """Build the coverage report rows for every selected source."""
    stored = stored or {}
    rows: list[Cell] = []

    for source_id in sorted(adapters.sources):
        config = adapters.sources[source_id]
        if not config.enabled and not include_disabled:
            continue

        declared = set(coverage.cells(source_id))
        actual = adapter_cells(_build(config), regions)

        keys = {
            *(cell for cell in actual if cell[0] in regions),
            *(cell for cell in declared if cell[0] in regions),
        }
        for region, offer_type, property_type in sorted(keys):
            rows.append(
                Cell(
                    source_id=source_id,
                    region=region,
                    offer_type=offer_type,
                    property_type=property_type,
                    declared=(region, offer_type, property_type) in declared,
                    queries=actual.get((region, offer_type, property_type), 0),
                    stored=_stored_for(
                        stored,
                        source_id,
                        region,
                        offer_type,
                        property_type,
                    ),
                ),
            )
    return rows


def _build(config: AdapterConfig) -> Adapter:
    from plaszczaki_scraper.adapters.registry import build_adapter
    from plaszczaki_scraper.regions import RegionRegistry

    return build_adapter(config, regions=RegionRegistry.load())


def matrix(rows: Sequence[Cell]) -> list[dict[str, Any]]:
    """Aggregate cells into one row per (source, region) with per-offer totals."""
    grouped: dict[tuple[str, str], list[Cell]] = defaultdict(list)
    for cell in rows:
        grouped[(cell.source_id, cell.region)].append(cell)

    result: list[dict[str, Any]] = []
    for (source_id, region), cells in sorted(grouped.items()):
        offers: dict[str, dict[str, int]] = {}
        for offer_type in OFFER_ORDER:
            subset = [cell for cell in cells if cell.offer_type == offer_type]
            if not subset:
                continue
            offers[offer_type] = {
                "cells": len(subset),
                "declared": sum(1 for cell in subset if cell.declared),
                "queries": sum(cell.queries for cell in subset),
                "stored": sum(cell.stored for cell in subset),
                "empty": sum(1 for cell in subset if cell.state == "empty"),
                "missing_query": sum(1 for cell in subset if cell.state == "missing-query"),
                "undeclared": sum(1 for cell in subset if cell.state == "undeclared"),
            }
        result.append({"source_id": source_id, "region": region, "offers": offers})
    return result


def _offer_label(stats: dict[str, int]) -> str:
    if stats["queries"] == 0:
        return f"{stats['declared']}/{stats['stored']}!"
    value = f"{stats['declared']}/{stats['stored']}"
    if stats["empty"] or stats["missing_query"]:
        value += "!"
    if stats["undeclared"]:
        value += "+"
    return value


def render_matrix(rows: Sequence[Cell], *, regions: Sequence[str]) -> str:
    """Render the per-source, per-region, per-transaction matrix."""
    data = matrix(rows)
    if not data:
        return "no coverage rows"

    offer_types = [offer for offer in OFFER_ORDER if any(offer in row["offers"] for row in data)]
    header = f"{'source':22} {'region':12} " + " ".join(f"{offer:>10}" for offer in offer_types)
    lines = [header, "-" * len(header)]
    for row in data:
        values = [
            _offer_label(row["offers"][offer]) if offer in row["offers"] else "-"
            for offer in offer_types
        ]
        lines.append(
            f"{row['source_id']:22} {row['region']:12} "
            + " ".join(f"{value:>10}" for value in values),
        )
    lines.append("")
    lines.append("cells = declared cells / stored rows; ! = empty or missing query; + = undeclared")
    return "\n".join(lines)


def render_cells(rows: Sequence[Cell]) -> str:
    """Render one line per (source, region, offer, property) cell."""
    header = f"{'source':22} {'region':12} {'offer':8} {'property':11} {'queries':>7} {'stored':>7} state"
    lines = [header, "-" * len(header)]
    for cell in sorted(
        rows,
        key=lambda item: (
            item.source_id,
            item.region,
            OFFER_ORDER.index(item.offer_type),
            item.property_type,
        ),
    ):
        lines.append(
            f"{cell.source_id:22} {cell.region:12} {cell.offer_type:8} "
            f"{cell.property_type:11} {cell.queries:>7} {cell.stored:>7} {cell.state}",
        )
    return "\n".join(lines)


def missing_cells(rows: Sequence[Cell]) -> list[Cell]:
    """Cells that need work: no query, no stored row, or undeclared drift."""
    return [cell for cell in rows if not cell.ok]


def summarize(rows: Sequence[Cell]) -> dict[str, int]:
    summary = {
        "cells": len(rows),
        "ok": 0,
        "empty": 0,
        "missing_query": 0,
        "undeclared": 0,
        "stored": sum(cell.stored for cell in rows),
    }
    for cell in rows:
        summary[cell.state] = summary.get(cell.state, 0) + 1
    return summary


def validate_coverage(
    *,
    coverage: CoverageConfig,
    adapters: AdaptersConfig,
    regions: RegionsConfig,
) -> list[str]:
    """Return human-readable coverage problems; empty means consistent."""
    problems: list[str] = []

    for source_id in adapters.sources:
        if source_id not in coverage.sources:
            problems.append(f"{source_id}: enabled adapter has no coverage entry")

    for source_id, declared in coverage.sources.items():
        adapter = adapters.sources.get(source_id)
        if adapter is None:
            problems.append(f"{source_id}: coverage entry has no adapter config")
            continue
        for region in declared.regions:
            if region not in regions.regions:
                problems.append(f"{source_id}: unknown region '{region}'")

        built = _build(adapter)
        for region in declared.regions:
            if region not in regions.regions:
                continue
            emitted = {(query.offer_type, query.property_type) for query in built.queries([region])}
            for offer_type, property_types in declared.offers.items():
                for property_type in property_types:
                    if (offer_type, property_type) not in emitted:
                        problems.append(
                            f"{source_id}: {region} declares {offer_type}/{property_type} "
                            "but the adapter emits no such query",
                        )
    return problems

"""Coverage audit for canonical listing fields and preserved source attributes."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plaszczaki_scraper.attributes import ATTRIBUTE_COLUMNS
from plaszczaki_scraper.db import Database
from plaszczaki_scraper.robots import default_cache_dir


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    group: str
    impact: int
    applies_to: str = "1"


_ALL = "1"
_RESIDENTIAL = "property_type IN ('flat', 'house', 'room')"
_RENT = "offer_type = 'rent'"
_AUCTION = "offer_type = 'auction'"

FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("title", "core", 2),
    FieldSpec("description", "core", 3),
    FieldSpec("images", "core", 2),
    FieldSpec("price_pln", "core", 4),
    FieldSpec("price_kind", "core", 2),
    FieldSpec("price_unit", "core", 2),
    FieldSpec("price_per_m2_pln", "core", 4, "property_type != 'garage'"),
    FieldSpec("area_m2", "core", 4, "property_type IN ('flat', 'house', 'commercial', 'room')"),
    FieldSpec("land_area_m2", "core", 3, "property_type IN ('plot', 'house')"),
    FieldSpec("rooms", "core", 3, _RESIDENTIAL),
    FieldSpec("floor", "core", 2, "property_type IN ('flat', 'commercial', 'room')"),
    FieldSpec("build_year", "core", 3, "property_type IN ('flat', 'house', 'commercial')"),
    FieldSpec("market", "core", 4),
    FieldSpec("city", "core", 4),
    FieldSpec("district", "core", 4, "property_type != 'plot'"),
    FieldSpec("street", "core", 3),
    FieldSpec("lat", "core", 4),
    FieldSpec("lng", "core", 4),
    FieldSpec("seller_type", "core", 2),
    FieldSpec("seller_name", "core", 1),
    FieldSpec("published_at", "temporal", 3),
    FieldSpec("modified_at", "temporal", 2),
    FieldSpec("first_seen_at", "temporal", 3),
    FieldSpec("last_seen_at", "temporal", 2),
    FieldSpec("condition", "attributes", 4),
    FieldSpec("building_type", "attributes", 3, _RESIDENTIAL),
    FieldSpec("building_material", "attributes", 3, _RESIDENTIAL),
    FieldSpec("heating", "attributes", 3, "property_type IN ('flat', 'house', 'commercial')"),
    FieldSpec("windows_type", "attributes", 2, _RESIDENTIAL),
    FieldSpec("ownership", "attributes", 3),
    FieldSpec("energy_certificate", "attributes", 3, _RESIDENTIAL),
    FieldSpec("ceiling_height_m", "attributes", 2, "property_type IN ('flat', 'commercial')"),
    FieldSpec(
        "building_floors", "attributes", 2, "property_type IN ('flat', 'house', 'commercial')"
    ),
    FieldSpec("elevator", "attributes", 3, "property_type IN ('flat', 'commercial')"),
    FieldSpec("parking", "attributes", 3),
    FieldSpec("parking_spaces", "attributes", 2),
    FieldSpec("balcony", "attributes", 2, "property_type IN ('flat', 'house')"),
    FieldSpec("terrace", "attributes", 2, "property_type IN ('flat', 'house')"),
    FieldSpec("garden", "attributes", 2, "property_type IN ('flat', 'house')"),
    FieldSpec("furnished", "attributes", 3, _RENT),
    FieldSpec("pets_allowed", "attributes", 2, _RENT),
    FieldSpec("air_conditioning", "attributes", 2),
    FieldSpec("security", "attributes", 2),
    FieldSpec("internet", "attributes", 2),
    FieldSpec("fenced", "attributes", 2, "property_type IN ('plot', 'house')"),
    FieldSpec("rent_pln", "rental", 4, _RENT),
    FieldSpec("deposit_pln", "rental", 4, _RENT),
    FieldSpec("administrative_rent_pln", "rental", 4, _RENT),
    FieldSpec("available_from", "rental", 3, _RENT),
    FieldSpec("estimate_pln", "auction", 4, _AUCTION),
    FieldSpec("auction_date", "auction", 4, _AUCTION),
    FieldSpec("case_number", "auction", 3, _AUCTION),
)

assert set(ATTRIBUTE_COLUMNS) <= {spec.name for spec in FIELD_SPECS}


def _filled_sql(spec: FieldSpec) -> str:
    if spec.name in {"market", "price_kind"}:
        return f"nullif(trim({spec.name}), '') IS NOT NULL AND {spec.name} != 'unknown'"
    if spec.name == "images":
        return "nullif(trim(images), '') IS NOT NULL AND images != '[]'"
    return f"nullif(trim({spec.name}), '') IS NOT NULL"


def _coverage(db: Database, spec: FieldSpec, source_id: str | None) -> list[dict[str, Any]]:
    where = f"WHERE ({spec.applies_to})"
    params: list[Any] = []
    if source_id is not None:
        where += " AND source_id = ?"
        params.append(source_id)
    rows = db.conn.execute(
        f"""
        SELECT source_id,
               count(*) AS total,
               sum(CASE WHEN {spec.applies_to} THEN 1 ELSE 0 END) AS applicable,
               sum(CASE WHEN ({spec.applies_to}) AND ({_filled_sql(spec)}) THEN 1 ELSE 0 END) AS filled
        FROM listing
        {where}
        GROUP BY source_id
        """,
        params,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        applicable = int(row["applicable"] or 0)
        filled = int(row["filled"] or 0)
        result.append(
            {
                "source_id": str(row["source_id"]),
                "field": spec.name,
                "group": spec.group,
                "impact": spec.impact,
                "total": int(row["total"]),
                "applicable": applicable,
                "filled": filled,
                "missing": applicable - filled,
                "coverage": round(filled / applicable, 4) if applicable else None,
            }
        )
    return result


def _sample(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = str(value)
        return text[:160]
    return None


def _extra_inventory(db: Database, source_id: str | None) -> dict[str, Any]:
    where = "WHERE extra IS NOT NULL AND extra != '{}'"
    params: list[Any] = []
    if source_id is not None:
        where += " AND source_id = ?"
        params.append(source_id)
    counts: dict[str, Counter[str]] = {}
    examples: dict[str, dict[str, Any]] = {}
    for row in db.conn.execute(f"SELECT source_id, extra FROM listing {where}", params):
        source = str(row["source_id"])
        source_counts = counts.setdefault(source, Counter())
        source_examples = examples.setdefault(source, {})
        try:
            payload = json.loads(row["extra"])
        except TypeError, json.JSONDecodeError:
            continue
        stack: list[tuple[str, Any, int]] = [("", payload, 0)]
        while stack:
            path, value, depth = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else str(key)
                    source_counts[child_path] += 1
                    sample = _sample(child)
                    if sample is not None:
                        source_examples.setdefault(child_path, sample)
                    if depth < 3 and isinstance(child, (dict, list)):
                        stack.append((child_path, child, depth + 1))
            elif isinstance(value, list) and depth < 3:
                for child in value[:10]:
                    if isinstance(child, (dict, list)):
                        stack.append((path, child, depth + 1))

    inventory: dict[str, Any] = {}
    for source, source_counts in counts.items():
        ordered = sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
        inventory[source] = {
            "keys": [
                {"path": path, "count": count, "example": examples[source].get(path)}
                for path, count in ordered[:250]
            ],
            "truncated": len(ordered) > 250,
        }
    return inventory


def audit_fields(
    db: Database,
    *,
    source_id: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Measure field coverage per source and inventory raw attributes in ``extra``."""
    coverage = [entry for spec in FIELD_SPECS for entry in _coverage(db, spec, source_id)]
    by_source: dict[str, dict[str, Any]] = {}
    for entry in coverage:
        source = by_source.setdefault(
            entry["source_id"],
            {"source_id": entry["source_id"], "total": entry["total"], "fields": {}},
        )
        source["fields"][entry["field"]] = {
            key: value for key, value in entry.items() if key not in {"source_id", "field", "total"}
        }

    gaps = [entry for entry in coverage if entry["applicable"] and entry["missing"]]
    gaps.sort(
        key=lambda entry: (
            -(entry["missing"] * entry["impact"]),
            entry["source_id"],
            entry["field"],
        )
    )
    total = int(
        db.conn.execute(
            "SELECT count(*) FROM listing" + (" WHERE source_id = ?" if source_id else ""),
            (source_id,) if source_id else (),
        ).fetchone()[0]
    )
    report: dict[str, Any] = {
        "generated_at": int(time.time() * 1000),
        "database": str(db.path),
        "total": total,
        "field_groups": {
            group: [spec.name for spec in FIELD_SPECS if spec.group == group]
            for group in sorted({spec.group for spec in FIELD_SPECS})
        },
        "sources": sorted(by_source.values(), key=lambda item: (-item["total"], item["source_id"])),
        "gaps": gaps,
        "extra_keys": _extra_inventory(db, source_id),
    }
    target = output_path or default_cache_dir().parent / "out" / "field-audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    report["output"] = str(target)
    return report

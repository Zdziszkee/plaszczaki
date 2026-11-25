"""Periodic re-check of blocked and planned sources.

The source catalog records a one-off decision per portal. Sites change, so this
module refetches each parked source, fingerprints what it serves and reports
sources whose challenge, listing links or location signals have changed.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from plaszczaki_scraper.config import SourceConfig, load_sources_config
from plaszczaki_scraper.http import (
    FetchError,
    HttpBlocked,
    HttpClient,
    RobotsDenied,
    challenge_vendor,
)
from plaszczaki_scraper.robots import default_cache_dir

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
LINK_RE = re.compile(r'href="([^"]+)"')
LISTING_HINT_RE = re.compile(
    r"(oferta|ogloszenie|ogłoszenie|przetarg|licytacj|nieruchom|mieszkan|"
    r"dzialk|działk|lokal|sprzeda|wynaj)",
    re.I,
)
PROPERTY_RE = re.compile(
    r"(nieruchom|mieszkan|dzia[łl]k|lokal|grunt|sprzeda[żz]|wynaj)",
    re.I,
)
COORD_RE = re.compile(r'(data-lat|latitude|"lat"\s*:|geo:)', re.I)
STREET_RE = re.compile(
    r'(streetAddress|ul\.|ulica|al\.)\s*[^<\n"]{0,4}[A-ZŻŹĆĄŚĘŁÓŃ]',
)
POSTAL_RE = re.compile(r"\d{2}-\d{3}")
JSONLD_RE = re.compile(r"application/ld\+json", re.I)

DEFAULT_RECHECK_DAYS = 90
MAX_CRAWL_DELAY_SECONDS = 10.0


def fingerprint(response: Any) -> dict[str, Any]:
    """Describe what a source URL currently serves."""
    body = bytes(getattr(response, "body", b"") or b"")
    text = body.decode("utf-8", "ignore")
    links = {href for href in LINK_RE.findall(text) if LISTING_HINT_RE.search(href)}
    title_match = TITLE_RE.search(text)
    title = " ".join(title_match.group(1).split())[:160] if title_match else ""
    return {
        "status": int(getattr(response, "status", 0) or 0),
        "bytes": len(body),
        "challenge": challenge_vendor(response),
        "title": title,
        "listing_links": len(links),
        "json_ld": bool(JSONLD_RE.search(text)),
        "coordinates": bool(COORD_RE.search(text)),
        "street": bool(STREET_RE.search(text)),
        "postal": bool(POSTAL_RE.search(text)),
        "property_words": len(PROPERTY_RE.findall(text)),
    }


def _challenge_fingerprint(vendor: str) -> dict[str, Any]:
    return {
        "status": None,
        "bytes": 0,
        "challenge": vendor,
        "title": "",
        "listing_links": 0,
        "json_ld": False,
        "coordinates": False,
        "street": False,
        "postal": False,
        "property_words": 0,
    }


def compare_fingerprints(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> list[str]:
    """Return human-readable changes between two fingerprints."""
    if current is None:
        return ["fetch failed"]
    if previous is None:
        return ["first observation"]

    changes: list[str] = []
    if previous.get("status") != current.get("status"):
        changes.append(f"status {previous.get('status')} -> {current.get('status')}")

    old_challenge = previous.get("challenge")
    new_challenge = current.get("challenge")
    if old_challenge != new_challenge:
        if new_challenge is None:
            changes.append(f"challenge cleared ({old_challenge})")
        elif old_challenge is None:
            changes.append(f"challenge appeared ({new_challenge})")
        else:
            changes.append(f"challenge changed {old_challenge} -> {new_challenge}")

    old_links = int(previous.get("listing_links") or 0)
    new_links = int(current.get("listing_links") or 0)
    if old_links == 0 and new_links > 0:
        changes.append(f"listing links appeared ({new_links})")
    elif old_links > 0 and new_links > max(old_links * 2, old_links + 20):
        changes.append(f"listing links grew {old_links} -> {new_links}")

    for field, label in (
        ("coordinates", "coordinates"),
        ("street", "street address"),
        ("postal", "postal code"),
        ("json_ld", "JSON-LD"),
    ):
        if not previous.get(field) and current.get(field):
            changes.append(f"{label} appeared")

    if not (previous.get("property_words") or 0) and (current.get("property_words") or 0):
        changes.append("property vocabulary appeared")

    return changes


def _due(source: SourceConfig, today: date | None = None) -> bool:
    if source.last_verified is None:
        return True
    try:
        verified = date.fromisoformat(source.last_verified)
    except ValueError:
        return True
    today = today or date.today()
    return (today - verified).days >= source.recheck_days


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return {"sources": {}}
    return payload if isinstance(payload, dict) else {"sources": {}}


def review_sources(
    *,
    source_ids: list[str] | None = None,
    limit: int | None = None,
    rps: float = 0.2,
    due_only: bool = False,
    force: bool = False,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Fetch parked sources and report changes against the previous report."""
    catalog = load_sources_config()
    if source_ids:
        unknown = [source_id for source_id in source_ids if source_id not in catalog.sources]
        if unknown:
            raise ValueError(f"unknown sources: {unknown}")
        selected = [catalog.sources[source_id] for source_id in source_ids]
    else:
        selected = [
            source for source in catalog.sources.values() if source.status in {"blocked", "planned"}
        ]

    if due_only and not force:
        selected = [source for source in selected if _due(source)]
    if limit is not None:
        selected = selected[:limit]

    target = output_path or default_cache_dir().parent / "out" / "source-review.json"
    previous_report = _load_report(target)
    previous_entries = previous_report.get("sources") or {}

    client = HttpClient(rps=rps)
    entries: list[dict[str, Any]] = []
    changed = 0

    for source in selected:
        url = source.urls[0]
        crawl_delay = client.robots.crawl_delay(url)
        if crawl_delay is not None and crawl_delay > MAX_CRAWL_DELAY_SECONDS:
            entries.append(
                {
                    "source_id": source.id,
                    "url": url,
                    "status": source.status,
                    "error": f"crawl-delay {crawl_delay}s exceeds review cap",
                    "fingerprint": None,
                    "changes": [],
                },
            )
            continue

        error: str | None = None
        current: dict[str, Any] | None = None
        try:
            response = client.get(url)
            current = fingerprint(response)
        except RobotsDenied as exc:
            error = f"robots: {exc}"
        except HttpBlocked as exc:
            vendor = str(exc).split(" challenge for ")[0]
            error = f"blocked: {exc}"
            current = _challenge_fingerprint(vendor)
        except FetchError as exc:
            error = f"fetch: {exc}"

        previous = (previous_entries.get(source.id) or {}).get("fingerprint")
        changes = compare_fingerprints(previous, current)
        if any(change not in {"first observation", "fetch failed"} for change in changes):
            changed += 1
        entries.append(
            {
                "source_id": source.id,
                "url": url,
                "status": source.status,
                "error": error,
                "fingerprint": current,
                "changes": changes,
            },
        )

    report = {
        "generated_at": int(time.time() * 1000),
        "total": len(entries),
        "changed": changed,
        "sources": {entry["source_id"]: entry for entry in entries},
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)

    report["output"] = str(target)
    return report

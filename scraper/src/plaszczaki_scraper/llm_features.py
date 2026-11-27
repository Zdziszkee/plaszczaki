"""Free-model LLM enrichment and top-offer re-ranking.

Descriptions are public listing text. Extracted facts are cached in ``llm_job``
by input hash + prompt version + model, and merged into ``listing.extra`` under
``scoring_features`` for the deterministic signal extractor. This module never
trains a model and can run without an
API key (it then reports that enrichment is unavailable).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from plaszczaki_scraper.llm import (
    LLMConfigError,
    LLMResponse,
    api_key,
    chat,
    fetch_models,
    free_models,
)
from plaszczaki_scraper.signals import extract_signals_database

PROMPT_VERSION = "signals-v1"

FEATURE_SYSTEM_PROMPT = """You extract objective facts from a public Polish real-estate listing.
Return exactly one JSON object and nothing else. Use true only when explicitly
stated or unambiguously described; false only when explicitly excluded; null
when unknown. Never guess. Schema:
{
  "insulation": true|false|null,
  "new_windows": true|false|null,
  "charm": true|false|null,
  "airbnb_potential": true|false|null,
  "building_plot": true|false|null,
  "evidence": ["short Polish quote 1", "short Polish quote 2"]
}
Charm means original/old character: pre-war tenement, original details,
traditional wooden/brick house, or similar. High ceilings alone are not charm.
Do not mark charm merely
because the listing uses marketing words like 'premium' or 'modern'.
airbnb_potential means an old/characterful house or plot in a tourist/nature
setting that plausibly suits a small guesthouse. building_plot means MPZP/WZ,
building-land designation, utilities and a realistic right to build, not an
agricultural/forest/recreational plot. Evidence quotes must come from the text."""

RERANK_SYSTEM_PROMPT = """You are a Polish real-estate search assistant. Re-rank the supplied
candidates for this exact user profile:
- Most interested in characterful old tenements with new windows, insulation and
  district heating; strongly dislikes large-panel apartment blocks.
- For newer flats: price, location, surrounding amenities and commute matter most.
- Houses: two independent targets — old, charming houses suitable for atmospheric
  Airbnb, or functional houses close to a real train station for daily commuting.
- Plots: two independent targets — nature/tourism potential for Airbnb, or a
  confirmed building plot with a real train station within 3 km serving Kraków,
  Katowice, Warszawa or Wrocław. Planned rail is only a speculative bonus.
Return exactly one JSON object:
{"recommendations":[{"id":123,"why":"krótkie uzasadnienie po polsku","watch_out":"ryzyko lub null"}]}
Order by overall attractiveness. Do not invent facts. Use at most 20 candidates.
Prefer diverse property types if signal evidence is similarly strong. Never rank a large-panel block
above a characterful old building solely for a small price difference."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not an object")
    return parsed


def _response_content(response: LLMResponse) -> str:
    if response.status != 200 or not response.body:
        raise RuntimeError(f"LLM HTTP {response.status}: {response.text[:200]}")
    choices = response.body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("LLM response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("LLM response has no message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM response has no content")
    return content


def _cached_job(db: Any, input_hash: str) -> dict[str, Any] | None:
    row = db.conn.execute(
        """
        SELECT * FROM llm_job
        WHERE input_hash = ? AND kind = 'charm' AND prompt_version = ?
          AND status = 'done' AND response_json IS NOT NULL
        ORDER BY updated_at DESC LIMIT 1
        """,
        (input_hash, PROMPT_VERSION),
    ).fetchone()
    return dict(row) if row is not None else None


def _job_models() -> list[str]:
    return [model.id for model in free_models(fetch_models())[:4]]


def _save_job(
    db: Any,
    *,
    source_id: str,
    url: str,
    input_hash: str,
    model: str,
    status: str,
    response_json: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    now = _now_ms()
    with db.conn:
        db.conn.execute(
            """
            INSERT INTO llm_job (
                source_id, url, kind, input_hash, model, prompt_version,
                status, attempts, response_json, error, created_at, updated_at
            ) VALUES (?, ?, 'charm', ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(input_hash, prompt_version, model) DO UPDATE SET
                status = excluded.status,
                attempts = llm_job.attempts + 1,
                response_json = excluded.response_json,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                source_id,
                url,
                input_hash,
                model,
                PROMPT_VERSION,
                status,
                _json(response_json) if response_json is not None else None,
                error,
                now,
                now,
            ),
        )


def _validate_features(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("insulation", "new_windows", "charm", "airbnb_potential", "building_plot"):
        value = payload.get(key)
        result[key] = value if isinstance(value, bool) else None
    evidence = payload.get("evidence")
    result["evidence"] = (
        [str(item)[:240] for item in evidence[:6]] if isinstance(evidence, list) else []
    )
    return result


def enrich_listings(
    db: Any,
    *,
    limit: int = 50,
    dry_run: bool = False,
    property_types: tuple[str, ...] = ("flat", "house", "plot"),
) -> dict[str, Any]:
    """Extract objective signal facts from descriptions using an approved free model."""
    if not api_key():
        raise LLMConfigError("OPENCODE_API_KEY is not set; LLM enrichment is unavailable")
    models = _job_models()
    if not models:
        raise LLMConfigError("no approved -free models are available")
    rows = db.conn.execute(
        """
        SELECT * FROM listing
        WHERE property_type IN ({placeholders})
          AND length(coalesce(description, '')) >= 80
          AND coalesce(json_extract(extra, '$.scoring_features'), '') = ''
        ORDER BY first_seen_at DESC
        LIMIT ?
        """.format(placeholders=",".join("?" for _ in property_types)),
        (*property_types, limit),
    ).fetchall()
    report: dict[str, Any] = {
        "processed": 0,
        "enriched": 0,
        "failed": 0,
        "models": models,
        "dry_run": dry_run,
    }
    changed_ids: list[int] = []
    for row in rows:
        report["processed"] += 1
        source_id, url = str(row["source_id"]), str(row["url"])
        text = f"{row['title'] or ''}\n{row['description'] or ''}"[:24_000]
        input_hash = hashlib.sha256(f"{row['property_type']}\n{text}".encode()).hexdigest()
        cached = _cached_job(db, input_hash)
        payload: dict[str, Any] | None = None
        model = ""
        if cached is not None:
            payload = json.loads(cached["response_json"])
            model = str(cached["model"])
        else:
            if dry_run:
                continue
            errors: list[str] = []
            for candidate in models:
                try:
                    response = chat(
                        model=candidate,
                        messages=[
                            {"role": "system", "content": FEATURE_SYSTEM_PROMPT},
                            {"role": "user", "content": text},
                        ],
                        max_tokens=700,
                        timeout=90.0,
                    )
                    payload = _validate_features(_parse_object(_response_content(response)))
                    model = candidate
                    _save_job(
                        db,
                        source_id=source_id,
                        url=url,
                        input_hash=input_hash,
                        model=model,
                        status="done",
                        response_json=payload,
                    )
                    break
                except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                    errors.append(f"{candidate}: {exc}")
            if payload is None:
                report["failed"] += 1
                if models:
                    _save_job(
                        db,
                        source_id=source_id,
                        url=url,
                        input_hash=input_hash,
                        model=models[0],
                        status="failed",
                        error=" | ".join(errors)[:1000],
                    )
                continue
        if dry_run:
            continue
        extra = json.loads(row["extra"]) if row["extra"] else {}
        if not isinstance(extra, dict):
            extra = {}
        extra["scoring_features"] = payload
        extra["scoring_features_model"] = model
        with db.conn:
            db.conn.execute(
                "UPDATE listing SET extra = ? WHERE id = ?",
                (_json(extra), int(row["id"])),
            )
        changed_ids.append(int(row["id"]))
        report["enriched"] += 1
    if changed_ids and not dry_run:
        db.invalidate_signals(changed_ids)
        report["signals"] = _extract_signals(db)
    return report


def _extract_signals(db: Any) -> dict[str, Any]:
    return extract_signals_database(db, pending_only=True)


def rerank_top(
    db: Any,
    *,
    limit: int = 20,
    candidate_limit: int = 50,
    dry_run: bool = False,
) -> dict[str, Any]:
    """LLM re-rank recent signal-enriched offers; cache the result in llm_job."""
    if not api_key():
        raise LLMConfigError("OPENCODE_API_KEY is not set; LLM re-ranking is unavailable")
    models = _job_models()
    if not models:
        raise LLMConfigError("no approved -free models are available")
    rows = db.conn.execute(
        """
        SELECT id, title, description, property_type, score_signals,
               city, price_pln, area_m2, land_area_m2
        FROM listing
        WHERE score_signals IS NOT NULL AND score_signals_at IS NOT NULL
          AND offer_type IN ('sale', 'auction')
        ORDER BY first_seen_at DESC
        LIMIT ?
        """,
        (candidate_limit,),
    ).fetchall()
    candidates = [
        {
            "id": int(row["id"]),
            "title": row["title"],
            "description": str(row["description"] or "")[:1800],
            "type": row["property_type"],
            "signals": json.loads(row["score_signals"]),
            "city": row["city"],
            "price_pln": row["price_pln"],
            "area_m2": row["area_m2"],
            "land_area_m2": row["land_area_m2"],
        }
        for row in rows
    ]
    input_hash = hashlib.sha256(_json(candidates).encode()).hexdigest()
    if dry_run:
        return {"candidates": len(candidates), "input_hash": input_hash, "dry_run": True}
    errors: list[str] = []
    for model in models:
        try:
            response = chat(
                model=model,
                messages=[
                    {"role": "system", "content": RERANK_SYSTEM_PROMPT},
                    {"role": "user", "content": _json(candidates)},
                ],
                max_tokens=2500,
                timeout=120.0,
            )
            payload = _parse_object(_response_content(response))
            recommendations = payload.get("recommendations")
            if not isinstance(recommendations, list):
                raise ValueError("missing recommendations")
            valid_ids = {candidate["id"] for candidate in candidates}
            cleaned = [
                item
                for item in recommendations
                if isinstance(item, dict) and item.get("id") in valid_ids
            ][:limit]
            result = {"model": model, "recommendations": cleaned}
            now = _now_ms()
            with db.conn:
                db.conn.execute(
                    """
                    INSERT INTO llm_job (
                        source_id, url, kind, input_hash, model, prompt_version,
                        status, attempts, response_json, created_at, updated_at
                    ) VALUES ('llm', '', 'rerank', ?, ?, ?, 'done', 1, ?, ?, ?)
                    ON CONFLICT(input_hash, prompt_version, model) DO UPDATE SET
                        response_json = excluded.response_json, updated_at = excluded.updated_at
                    """,
                    (input_hash, model, PROMPT_VERSION, _json(result), now, now),
                )
            return {"candidates": len(candidates), **result}
        except (RuntimeError, ValueError, httpx.HTTPError) as exc:
            errors.append(f"{model}: {exc}")
    return {
        "candidates": len(candidates),
        "recommendations": [],
        "errors": errors,
    }

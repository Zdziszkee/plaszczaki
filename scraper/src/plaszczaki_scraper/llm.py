"""OpenCode inference preflight and free-model selection.

The LLM lane is deliberately separate from deterministic adapters. This module
only discovers models and checks whether they are reachable from this process.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from plaszczaki_scraper.robots import default_cache_dir

DEFAULT_BASE_URL = "https://opencode.ai/inference"
DEFAULT_TIMEOUT = 60.0

ZERO_RETENTION_FREE = {"hy4-flash-free", "deepseek-v4.1-flash-free"}
# User-approved OpenCode Go free models: model output can be retained according
# to the provider's terms, but extraction only uses public listing text.
NO_TRAINING_FREE = {"beam-free", "hungerg-free", "iceman-contributor-free"}

PREFERRED_FREE_ORDER = (
    "hungerg-free",
    "hy4-flash-free",
    "beam-free",
    "iceman-contributor-free",
    "deepseek-v4.1-flash-free",
    "flint-40b-free",
    "nemotron-3.5-lightning-free",
    "mimo-v2.6-flash-free",
    "iceman-free",
    "tidepool-alpha-free",
    "big-pickle",
)


class LLMConfigError(RuntimeError):
    """Raised when the LLM lane is missing configuration."""


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    name: str
    context: int | None
    structured_output: bool
    pricing: dict[str, Any] | None
    owned_by: str | None

    @property
    def privacy(self) -> str:
        if self.id in ZERO_RETENTION_FREE:
            return "zero-retention"
        if self.id in NO_TRAINING_FREE:
            return "30d-retention"
        return "unknown"

    @property
    def is_free(self) -> bool:
        return self.id.endswith("-free")


@dataclass(frozen=True, slots=True)
class LLMResponse:
    status: int
    body: dict[str, Any] | None
    text: str


def api_key() -> str | None:
    return os.environ.get("OPENCODE_API_KEY") or None


def base_url() -> str:
    return os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def allow_retention_models() -> bool:
    return os.environ.get("LLM_ALLOW_RETENTION_FREE_MODELS", "").lower() in {
        "1",
        "true",
        "yes",
    }


def allow_paid_fallback() -> bool:
    return os.environ.get("LLM_ALLOW_PAID_FALLBACK", "").lower() in {
        "1",
        "true",
        "yes",
    }


def configured_chain() -> list[str]:
    raw = os.environ.get("LLM_MODEL_CHAIN")
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(PREFERRED_FREE_ORDER)


def fetch_models(*, timeout: float = DEFAULT_TIMEOUT) -> list[ModelInfo]:
    headers = {"Accept": "application/json"}
    key = api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    response = httpx.get(
        f"{base_url()}/v1/models",
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise LLMConfigError("model list response is missing 'data'")

    models: list[ModelInfo] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        model_id = record.get("id")
        if not isinstance(model_id, str):
            continue
        if record.get("type") not in {None, "chat"}:
            continue
        capabilities = record.get("capabilities") or {}
        limit = record.get("limit") or {}
        models.append(
            ModelInfo(
                id=model_id,
                name=str(record.get("name") or model_id),
                context=limit.get("context") if isinstance(limit.get("context"), int) else None,
                structured_output=bool(capabilities.get("structured_output")),
                pricing=record.get("pricing") if isinstance(record.get("pricing"), dict) else None,
                owned_by=record.get("owned_by")
                if isinstance(record.get("owned_by"), str)
                else None,
            ),
        )
    return models


def free_models(models: list[ModelInfo]) -> list[ModelInfo]:
    """Return every available ``-free`` chat model in the preferred order.

    The user explicitly approved any OpenCode Go free model for public listing
    text. Provider retention terms still apply; ``privacy`` remains visible in
    reports, but unknown-retention free models are not silently excluded.
    """
    by_id = {model.id: model for model in models if model.is_free}
    ordered: list[ModelInfo] = []
    for model_id in configured_chain():
        model = by_id.pop(model_id, None)
        if model is not None:
            ordered.append(model)
    ordered.extend(sorted(by_id.values(), key=lambda model: model.id))
    return ordered


def chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = 256,
) -> LLMResponse:
    key = api_key()
    if not key:
        raise LLMConfigError("OPENCODE_API_KEY is not set")

    response = httpx.post(
        f"{base_url()}/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
        follow_redirects=True,
    )
    body: dict[str, Any] | None
    try:
        parsed = response.json()
        body = parsed if isinstance(parsed, dict) else None
    except ValueError:
        body = None
    return LLMResponse(status=response.status_code, body=body, text=response.text)


def check_models(
    *,
    limit: int = 10,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Try each free model with a tiny JSON extraction request."""
    models = fetch_models(timeout=timeout)
    candidates = free_models(models)[:limit]
    key = api_key()

    results: list[dict[str, Any]] = []
    for model in candidates:
        started = time.monotonic()
        if not key:
            results.append(
                {
                    "model": model.id,
                    "status": "missing-key",
                    "privacy": model.privacy,
                    "structured_output": model.structured_output,
                },
            )
            continue

        try:
            response = chat(
                model=model.id,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a JSON extractor. Return exactly one JSON object "
                            "and nothing else."
                        ),
                    },
                    {
                        "role": "user",
                        "content": 'Return exactly {"ok": true} for this test.',
                    },
                ],
                timeout=timeout,
                max_tokens=64,
            )
        except httpx.HTTPError as exc:
            results.append(
                {
                    "model": model.id,
                    "status": "transport-error",
                    "error": str(exc),
                    "privacy": model.privacy,
                    "structured_output": model.structured_output,
                    "latency_seconds": round(time.monotonic() - started, 2),
                },
            )
            continue

        status = "ok"
        parsed_ok = False
        content = ""
        if response.status == 200 and response.body:
            choices = response.body.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                content = str(message.get("content") or "") if isinstance(message, dict) else ""
                try:
                    parsed_ok = json.loads(content).get("ok") is True
                except json.JSONDecodeError, AttributeError:
                    parsed_ok = False
            if not parsed_ok:
                status = "invalid-json"
        elif response.status in {401, 403}:
            status = f"http-{response.status}"
        elif response.status == 429:
            status = "http-429"
        else:
            status = f"http-{response.status}"

        results.append(
            {
                "model": model.id,
                "status": status,
                "privacy": model.privacy,
                "structured_output": model.structured_output,
                "latency_seconds": round(time.monotonic() - started, 2),
                "content": content[:200],
                "error": None if response.body is None else response.body.get("error"),
            },
        )

    report = {
        "base_url": base_url(),
        "checked_at": int(time.time() * 1000),
        "key_present": bool(key),
        "models": results,
        "working": [result["model"] for result in results if result["status"] == "ok"],
    }
    _write_report("llm-check.json", report)
    return report


def models_report() -> dict[str, Any]:
    models = fetch_models()
    free = free_models(models)
    return {
        "base_url": base_url(),
        "key_present": bool(api_key()),
        "free_models": [
            {
                "id": model.id,
                "name": model.name,
                "context": model.context,
                "structured_output": model.structured_output,
                "privacy": model.privacy,
            }
            for model in free
        ],
        "paid_fallback_allowed": allow_paid_fallback(),
    }


def _write_report(name: str, report: dict[str, Any]) -> Path:
    target = default_cache_dir().parent / "out" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target

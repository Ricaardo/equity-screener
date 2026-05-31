"""Optional LLM opinion for the US pre-market report.

This layer is best-effort only: if the configured provider package or API key is
missing, it returns a structured skip payload and never raises. Keys are never
printed or included in the response.
"""

from __future__ import annotations

import json
from typing import Any

from us_screener.config import get_us_config


def _prompt(payload: dict[str, Any]) -> str:
    top = payload.get("top_candidates") or []
    summary = payload.get("macro_context") or {}
    compact = {
        "report_date": payload.get("report_date"),
        "macro_context": {
            "regime": summary.get("regime"),
            "market_score": summary.get("market_score"),
            "summary": summary.get("summary"),
        },
        "top_candidates": top[:8],
        "filtered_summary": payload.get("filtered_summary") or {},
    }
    return (
        "You are generating a concise US premarket screener opinion. "
        "Return strict JSON with keys: summary, stance, risks, actions. "
        "Keep summary under 120 words, stance one of bullish/neutral/cautious.\n\n"
        + json.dumps(compact, ensure_ascii=False)
    )


def generate_us_llm_opinion(payload: dict[str, Any]) -> dict[str, Any]:
    cfg = get_us_config()
    if cfg.llm_provider == "none" or not cfg.llm_api_key:
        return {
            "status": "skipped",
            "provider": cfg.llm_provider,
            "model": cfg.llm_model,
            "reason": "No LLM provider configured.",
        }

    prompt = _prompt(payload)
    try:
        if cfg.llm_provider == "anthropic":
            from anthropic import Anthropic

            client = Anthropic(api_key=cfg.llm_api_key)
            response = client.messages.create(
                model=cfg.llm_model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            ).strip()
        elif cfg.llm_provider == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=cfg.llm_api_key)
            response = client.responses.create(model=cfg.llm_model, input=prompt)
            text = getattr(response, "output_text", "").strip()
        else:
            return {
                "status": "skipped",
                "provider": cfg.llm_provider,
                "model": cfg.llm_model,
                "reason": "Unsupported LLM provider.",
            }
    except ImportError:
        return {
            "status": "skipped",
            "provider": cfg.llm_provider,
            "model": cfg.llm_model,
            "reason": "LLM package not installed.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "provider": cfg.llm_provider,
            "model": cfg.llm_model,
            "reason": str(exc),
        }

    if not text:
        return {
            "status": "skipped",
            "provider": cfg.llm_provider,
            "model": cfg.llm_model,
            "reason": "Empty LLM response.",
        }

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"summary": text, "stance": "neutral", "risks": [], "actions": []}
    if not isinstance(parsed, dict):
        parsed = {"summary": str(parsed), "stance": "neutral", "risks": [], "actions": []}
    parsed.setdefault("summary", "")
    parsed.setdefault("stance", "neutral")
    parsed.setdefault("risks", [])
    parsed.setdefault("actions", [])
    return {
        "status": "ok",
        "provider": cfg.llm_provider,
        "model": cfg.llm_model,
        **parsed,
    }

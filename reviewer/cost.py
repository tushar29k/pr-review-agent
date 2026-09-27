"""Per-review cost + latency logging to JSONL.

Why this exists: model backends cost real money per review, and the
deterministic checks are nearly free — this file records which part of a
review took how long and what it probably cost, so the tradeoffs stay
visible instead of buried in a cloud bill.

Token counts are ESTIMATES (chars/4 rule of thumb), not tokenizer output —
treat the cost column as order-of-magnitude, not an invoice.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# rough price defaults, USD per 1M tokens; override any with the env var shown
_PRICE_DEFAULTS = {
    # (input per 1M, output per 1M, flat per review, env prefix)
    "mock": (0.0, 0.0, 0.0, "MOCK"),  # no model call, nothing to bill
    "openai": (0.15, 0.60, 0.0, "OPENAI"),  # gpt-4o-mini-ish list prices
    "anthropic": (0.25, 1.25, 0.0, "ANTHROPIC"),  # haiku-ish list prices
    # local burns GPU/CPU time instead of API dollars — flat guess per review
    "local": (0.0, 0.0, 0.0005, "LOCAL"),
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def price_for(backend: str) -> tuple[float, float, float]:
    """(input $/1M, output $/1M, flat $/review) for a backend name."""
    key = (backend or "mock").strip().lower()
    default = _PRICE_DEFAULTS.get(key, _PRICE_DEFAULTS["mock"])
    pin, pout, flat, prefix = default
    return (
        _env_float(f"REVIEW_PRICE_{prefix}_INPUT_PER_1M", pin),
        _env_float(f"REVIEW_PRICE_{prefix}_OUTPUT_PER_1M", pout),
        _env_float(f"REVIEW_PRICE_{prefix}_FLAT", flat),
    )


def estimate_tokens(text: str) -> int:
    # chars/4 is the classic rule of thumb — good enough for a cost guess
    return max(1, len(text or "") // 4)


def cost_usd(backend: str, in_tokens: int, out_tokens: int) -> float:
    pin, pout, flat = price_for(backend)
    return flat + in_tokens / 1_000_000 * pin + out_tokens / 1_000_000 * pout


def build_record(diff_text: str, findings: list, stages: dict,
                 backend: str) -> dict:
    """One JSON-serializable record per review."""
    counts = {"critical": 0, "warning": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    from .diff_parser import parse_diff  # cheap; keeps imports lazy-friendly
    files = parse_diff(diff_text)
    in_tokens = estimate_tokens(diff_text)
    out_tokens = estimate_tokens(json.dumps(findings))
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backend": backend,
        "files_changed": len(files),
        "added_lines": sum(f.added_count for f in files),
        "removed_lines": sum(1 for f in files for h in f.hunks if h.kind == "del"),
        "diff_chars": len(diff_text),
        "findings_total": len(findings),
        "findings_by_severity": counts,
        "latency_ms": {k: round(v, 1) for k, v in stages.items()},
        "tokens_estimated": {"input": in_tokens, "output": out_tokens},
        "cost_usd": round(cost_usd(backend, in_tokens, out_tokens), 6),
    }


def append_record(log_path: str, record: dict) -> None:
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

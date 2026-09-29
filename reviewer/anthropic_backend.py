"""Anthropic reviewer backend: real model judgement over each file's diff.

Same one-method shape as MockBackend, so Reviewer needs no changes.
Needs the `anthropic` package and an ANTHROPIC_API_KEY env var for the real
model path. Without a key it falls back to the mock heuristics, so
`reviewer: anthropic` always runs end to end — handy for evals and CI
without spending tokens on every diff.
"""

from __future__ import annotations

import json
import os
import sys

from .config import confidence_to_severity
from .diff_parser import FileDiff
from .prompts import render_user, system_prompt
from .reviewer import MockBackend, ReviewBackend

_ENV_KEY = "ANTHROPIC_API_KEY"
_DEFAULT_MODEL = "claude-haiku-4-5"  # cheap enough for review duty

_SEVERITIES = {"critical", "warning", "info"}


def _strip_fences(text: str) -> str:
    # models love wrapping JSON in ```json fences even when told not to
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _confidence(item: dict) -> float | None:
    # a real 0-1 number remaps the model's severity through our calibration;
    # anything else falls back to whatever severity the model gave
    c = item.get("confidence")
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        return None
    return min(1.0, max(0.0, float(c)))


def _parse_findings(text: str, f: FileDiff) -> list[dict]:
    """Validate model JSON into the finding schema. Malformed output -> []."""
    try:
        data = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, ValueError):
        return []  # garbage in, empty out — never crash the review
    if not isinstance(data, list):
        return []
    added = {h.new_no for h in f.added_lines}
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        line = item.get("line")
        conf = _confidence(item)
        severity = (confidence_to_severity(conf) if conf is not None
                    else item.get("severity"))
        if severity not in _SEVERITIES or not message or not isinstance(message, str):
            continue  # drop malformed entries, keep the good ones
        if line is not None and (not isinstance(line, int) or line not in added):
            # model pointed at a non-added line — keep the note, drop the anchor
            line = None
        out.append({
            "severity": severity, "file": f.path, "line": line,
            "check": "llm-anthropic", "confidence": conf,
            "message": message.strip(),
        })
    return out


class AnthropicBackend(ReviewBackend):
    """Per-file diff review via the Anthropic Messages API. Fails soft."""

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 prompt_version: str | None = None):
        self._prompt_version = prompt_version  # None = REVIEWER_PROMPT_VERSION
        key = api_key or os.environ.get(_ENV_KEY)
        if not key:
            # no key: mock heuristics instead of a real model, so this config
            # still exercises the full pipeline in evals — no SDK needed either
            self._client = None
            self._fallback: ReviewBackend = MockBackend()
            print("anthropic backend: no ANTHROPIC_API_KEY — mock heuristics "
                  "instead (set ANTHROPIC_API_KEY to use the real model)",
                  file=sys.stderr)
            return
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "the anthropic package isn't installed — pip install anthropic "
                "to use reviewer: anthropic (the mock reviewer still works offline)"
            ) from exc
        self._client = Anthropic(api_key=key)
        self._model = model or os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
        self._fallback = None

    def review_file(self, f: FileDiff) -> list[dict]:
        if self._fallback is not None:
            return self._fallback.review_file(f)
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=1024,  # findings are short; cap the bill
                # no temperature: newer Claude models reject non-default sampling params
                system=system_prompt(self._prompt_version),
                messages=[{"role": "user",
                           "content": render_user(f, self._prompt_version)}],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text")
        except Exception as exc:  # network/auth hiccups shouldn't kill the review
            print(f"anthropic backend: model call failed ({exc}), skipping file",
                  file=sys.stderr)
            return []
        return _parse_findings(text, f)

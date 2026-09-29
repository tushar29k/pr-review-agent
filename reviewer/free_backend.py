"""Free-tier reviewer backend: real model judgement over each file's diff,
no bill.

Same one-method shape as MockBackend, so Reviewer needs no changes.
Uses llm_client (Gemini via Google AI Studio's free tier, or OpenRouter
with LLM_PROVIDER=openrouter and a :free model) — no SDK, no weights,
plain HTTPS. Needs LLM_API_KEY in the environment; without it,
construction raises a plain-English error. The mock stays the default
everywhere.

Fails soft like the other backends: a failed model call skips the file
instead of killing the review.
"""

from __future__ import annotations

import json
import os
import sys

from llm_client import FreeLLMClient, FreeLLMError

from .config import confidence_to_severity
from .diff_parser import FileDiff
from .prompts import render_user, system_prompt
from .reviewer import ReviewBackend

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
            "check": "llm-free", "confidence": conf,
            "message": message.strip(),
        })
    return out


class FreeBackend(ReviewBackend):
    """Per-file diff review via a free-tier LLM API. Fails soft."""

    def __init__(self, model: str | None = None,
                 prompt_version: str | None = None):
        self._prompt_version = prompt_version  # None = REVIEWER_PROMPT_VERSION
        client = FreeLLMClient.from_env()
        if client is None:
            raise RuntimeError(
                "set the LLM_API_KEY env var to use reviewer: free "
                "(the mock reviewer is the default and needs no key)"
            )
        if model:
            client.model = model
        self._client = client
        self.last_error = None  # last api failure, if any — on /info

    def review_file(self, f: FileDiff) -> list[dict]:
        try:
            text = self._client.generate(
                system_prompt(self._prompt_version) + "\n\n" +
                render_user(f, self._prompt_version),
                max_tokens=512, temperature=0)  # reviews shouldn't be creative
            self.last_error = None  # recovered
        except FreeLLMError as exc:  # network/auth hiccups skip the file
            self.last_error = str(exc)  # key-free — safe for /info
            print(f"free backend: model call failed ({exc}), skipping file",
                  file=sys.stderr)
            return []
        return _parse_findings(text or "", f)

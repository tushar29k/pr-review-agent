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

from .diff_parser import FileDiff
from .reviewer import MockBackend, ReviewBackend

_ENV_KEY = "ANTHROPIC_API_KEY"
_DEFAULT_MODEL = "claude-haiku-4-5"  # cheap enough for review duty

_SEVERITIES = {"critical", "warning", "info"}

# strict JSON-only output: no prose to strip, one schema to validate
_SYSTEM_PROMPT = (
    "You review code diffs. Reply with ONLY a JSON array of findings, no prose. "
    "Each finding: {\"severity\": \"critical\"|\"warning\"|\"info\", "
    "\"line\": <new-file line number or null>, \"message\": \"<why it matters>\"}. "
    "Only flag added lines. critical = exploitable bugs/leaked secrets, "
    "warning = real defects, info = minor. Empty array if nothing is wrong."
)


def _file_prompt(f: FileDiff) -> str:
    # one file per call: keeps prompts small and findings attributable
    lines = []
    for h in f.hunks:
        marker = {"add": "+", "del": "-", "ctx": " "}[h.kind]
        no = h.new_no if h.new_no is not None else h.old_no
        lines.append(f"{marker} {no}: {h.text}")
    new_flag = " (new file)" if f.is_new else ""
    return ("File: " + f.path + new_flag +
            "\nDiff (line numbers are new-file lines):\n" +
            "\n".join(lines))


def _strip_fences(text: str) -> str:
    # models love wrapping JSON in ```json fences even when told not to
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


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
        severity = item.get("severity")
        message = item.get("message")
        line = item.get("line")
        if severity not in _SEVERITIES or not message or not isinstance(message, str):
            continue  # drop malformed entries, keep the good ones
        if line is not None and (not isinstance(line, int) or line not in added):
            # model pointed at a non-added line — keep the note, drop the anchor
            line = None
        out.append({
            "severity": severity, "file": f.path, "line": line,
            "check": "llm-anthropic",
            "message": message.strip(),
        })
    return out


class AnthropicBackend(ReviewBackend):
    """Per-file diff review via the Anthropic Messages API. Fails soft."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
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
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _file_prompt(f)}],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text")
        except Exception as exc:  # network/auth hiccups shouldn't kill the review
            print(f"anthropic backend: model call failed ({exc}), skipping file",
                  file=sys.stderr)
            return []
        return _parse_findings(text, f)

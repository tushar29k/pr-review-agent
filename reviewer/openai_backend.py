"""OpenAI reviewer backend: real model judgement over each file's diff.

Same one-method shape as MockBackend, so Reviewer needs no changes.
Needs the `openai` package and an OPENAI_API_KEY env var — without either,
construction raises a plain-English error instead of a confusing
ImportError/KeyError later. The mock stays the default everywhere.
"""

from __future__ import annotations

import json
import os
import sys

from .diff_parser import FileDiff
from .reviewer import ReviewBackend

_ENV_KEY = "OPENAI_API_KEY"
_DEFAULT_MODEL = "gpt-4o-mini"  # cheap enough for review duty

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
            "check": "llm-openai",
            "message": message.strip(),
        })
    return out


class OpenAIBackend(ReviewBackend):
    """Per-file diff review via the OpenAI API. Fails soft, fails loud."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "the openai package isn't installed — pip install openai "
                "to use reviewer: openai (the mock reviewer still works offline)"
            ) from exc
        key = api_key or os.environ.get(_ENV_KEY)
        if not key:
            raise RuntimeError(
                "set the OPENAI_API_KEY env var to use reviewer: openai "
                "(the mock reviewer is the default and needs no key)"
            )
        self._client = OpenAI(api_key=key)
        self._model = model or os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)

    def review_file(self, f: FileDiff) -> list[dict]:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _file_prompt(f)},
                ],
                temperature=0,  # reviews shouldn't be creative
            )
            text = resp.choices[0].message.content or ""
        except Exception as exc:  # network/auth hiccups shouldn't kill the review
            print(f"openai backend: model call failed ({exc}), skipping file",
                  file=sys.stderr)
            return []
        return _parse_findings(text, f)

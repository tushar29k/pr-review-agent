"""The orchestrator: deterministic checks first, LLM judgement second.

Design decision worth knowing: the cheap, certain checks run first and the
model only gets asked about the rest. That way a leaked secret is flagged
even if the model call fails, times out, or just has an off day.
"""

from __future__ import annotations

import os

from . import checks
from .diff_parser import FileDiff, parse_diff


class ReviewBackend:
    """Interface a real model backend implements."""

    def review_file(self, f: FileDiff) -> list[dict]:
        """Return extra findings for one file. Empty list = nothing to add."""
        raise NotImplementedError


class MockBackend(ReviewBackend):
    """Stands in for a real model so the whole pipeline runs with zero keys.

    It only fires on obvious shapes (long functions being added, bare
    excepts) — enough to exercise the pipeline end to end. Swap this for
    an OpenAI/Anthropic/local-model backend in production; the interface
    is one method.
    """

    def review_file(self, f: FileDiff) -> list[dict]:
        out = []
        for h in f.added_lines:
            stripped = h.text.strip()
            if stripped == "except:" or stripped == "except Exception:":
                out.append({
                    "severity": "warning", "file": f.path, "line": h.new_no,
                    "check": "llm",
                    "message": "Bare except swallows everything including "
                               "KeyboardInterrupt and real bugs. Catch what you expect.",
                })
            if stripped.startswith("def ") and len(h.text) - len(h.text.lstrip()) == 0:
                pass  # top-level defs are fine; real model would judge complexity
        # one heuristic with actual signal: a function growing 40+ added lines
        # inside a single file is usually doing too much
        if f.added_count > 40 and not f.is_new:
            out.append({
                "severity": "info", "file": f.path, "line": None,
                "check": "llm",
                "message": f"{f.added_count} added lines in one file — "
                           "worth a skim for accidental complexity.",
            })
        return out


_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def make_backend(name: str | None = None) -> ReviewBackend:
    """Pick a reviewer backend by name. "mock" is the default and needs nothing."""
    name = (name or os.environ.get("REVIEWER_BACKEND", "mock")).strip().lower()
    if name == "mock":
        return MockBackend()
    if name == "openai":
        from .openai_backend import OpenAIBackend  # lazy: openai pkg is optional
        return OpenAIBackend()
    raise ValueError(f"unknown reviewer backend {name!r} — want 'mock' or 'openai'")


class Reviewer:
    def __init__(self, backend: ReviewBackend | None = None):
        self.backend = backend or MockBackend()

    def review(self, diff_text: str) -> list[dict]:
        files = parse_diff(diff_text)
        findings = checks.run_all(files)
        for f in files:
            if not f.is_binary:
                findings += self.backend.review_file(f)
        # deterministic order: severity first, then file, then line
        findings.sort(key=lambda d: (
            _SEVERITY_RANK.get(d["severity"], 9), d["file"], d["line"] or 0))
        return findings

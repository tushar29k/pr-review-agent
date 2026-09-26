"""The orchestrator: deterministic checks first, LLM judgement second.

Design decision worth knowing: the cheap, certain checks run first and the
model only gets asked about the rest. That way a leaked secret is flagged
even if the model call fails, times out, or just has an off day.
"""

from __future__ import annotations

import os

from . import checks
from .checks import TODO_RE
from .config import confidence_to_severity
from .dedupe import dedupe_findings
from .diff_parser import FileDiff, parse_diff


class ReviewBackend:
    """Interface a real model backend implements."""

    def review_file(self, f: FileDiff) -> list[dict]:
        """Return extra findings for one file. Empty list = nothing to add."""
        raise NotImplementedError


class MockBackend(ReviewBackend):
    """Stands in for a real model so the whole pipeline runs with zero keys.

    It only fires on obvious shapes (long functions being added, bare
    excepts) — enough to exercise the pipeline end to end. The heuristics
    carry a fixed confidence through the same calibration every real
    backend uses, so swapping in OpenAI/Anthropic/local changes the
    numbers, not the plumbing.
    """

    def review_file(self, f: FileDiff) -> list[dict]:
        out = []
        for h in f.added_lines:
            stripped = h.text.strip()
            if stripped == "except:" or stripped == "except Exception:":
                out.append({
                    "severity": confidence_to_severity(0.55),
                    "confidence": 0.55,  # shape is a decent but imperfect signal
                    "file": f.path, "line": h.new_no, "check": "llm",
                    "message": "Bare except swallows everything including "
                               "KeyboardInterrupt and real bugs. Catch what you expect.",
                })
            if stripped.startswith("def ") and len(h.text) - len(h.text.lstrip()) == 0:
                pass  # top-level defs are fine; real model would judge complexity
            # a real model would also judge TODOs left in the diff — flag them
            # so the dedupe pass has something real to merge with the todos check
            m = TODO_RE.search(h.text)
            if m:
                out.append({
                    "severity": confidence_to_severity(0.45),
                    "confidence": 0.45,  # a TODO is a nudge, not a verdict
                    "file": f.path, "line": h.new_no, "check": "llm",
                    "message": f"{m.group(1)} left in the diff: "
                               f"\"{m.group(2).strip()[:80]}\" — "
                               "track it somewhere or it's tech debt by default.",
                })
        # one heuristic with actual signal: a function growing 40+ added lines
        # inside a single file is usually doing too much
        if f.added_count > 40 and not f.is_new:
            out.append({
                "severity": confidence_to_severity(0.35),
                "confidence": 0.35,  # weak heuristic, lands as info by design
                "file": f.path, "line": None, "check": "llm",
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
    if name == "anthropic":
        from .anthropic_backend import AnthropicBackend  # lazy: anthropic pkg is optional
        return AnthropicBackend()
    if name == "local":
        from .local_backend import LocalBackend  # lazy: weights load on first use
        return LocalBackend()
    raise ValueError(f"unknown reviewer backend {name!r} — want 'mock', 'openai', 'anthropic', or 'local'")


class Reviewer:
    def __init__(self, backend: ReviewBackend | None = None):
        self.backend = backend or MockBackend()

    def review(self, diff_text: str) -> list[dict]:
        files = parse_diff(diff_text)
        findings = checks.run_all(files)
        for f in files:
            if not f.is_binary:
                findings += self.backend.review_file(f)
        findings = dedupe_findings(findings)  # one finding per spot, not per layer
        # deterministic order: severity first, then file, then line
        findings.sort(key=lambda d: (
            _SEVERITY_RANK.get(d["severity"], 9), d["file"], d["line"] or 0))
        return findings

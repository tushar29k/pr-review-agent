"""Deterministic checks: the stuff that's too important to leave to vibes.

Every check returns a list of findings. A finding is just a dict:
    {"severity": "critical"|"warning"|"info",
     "file": path, "line": new-file line number or None,
     "check": check name, "message": human-readable why}

The LLM backend adds judgement on top; these checks make sure the
non-negotiables (secrets, huge diffs) never slip through silently.
"""

from __future__ import annotations

import re

from .diff_parser import FileDiff

# (name, pattern) — kept deliberately small; extend as you learn new shapes.
SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret", re.compile(r"(?i)aws_secret[^a-z0-9]{0,10}[\"']?[A-Za-z0-9/+=]{30,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic_token", re.compile(r"(?i)(?:api[_-]?key|secret|token)\s*[:=]\s*[\"'][^\"']{12,}[\"']")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
]

DEBUG_PATTERNS = [
    ("print_statement", re.compile(r"^\s*print\s*\(")),
    ("console_log", re.compile(r"^\s*console\.(log|debug|info)\s*\(")),
    ("debugger", re.compile(r"^\s*debugger\s*;?\s*$")),
    ("pdb", re.compile(r"^\s*(?:import pdb|pdb\.set_trace\(\))")),
]

TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b\s*:?\s*(.*)")

# files that smell like source code (vs docs/config)
SOURCE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb"}


def _finding(severity, file, line, check, message):
    # deterministic checks are certain by design — confidence 1.0, so the
    # calibration layer never demotes them
    return {"severity": severity, "file": file, "line": line,
            "check": check, "confidence": 1.0, "message": message}


def check_secrets(f: FileDiff) -> list[dict]:
    """Secrets in a diff are a merge-blocking emergency, full stop."""
    out = []
    for h in f.added_lines:
        for name, pat in SECRET_PATTERNS:
            if pat.search(h.text):
                out.append(_finding(
                    "critical", f.path, h.new_no, "secrets",
                    f"Possible {name.replace('_', ' ')} committed. "
                    "Rotate it and pull it out of history before merging."))
                break  # one finding per line is plenty
    return out


def check_debug_leftovers(f: FileDiff) -> list[dict]:
    out = []
    for h in f.added_lines:
        for name, pat in DEBUG_PATTERNS:
            if pat.search(h.text):
                out.append(_finding(
                    "warning", f.path, h.new_no, "debug_leftovers",
                    f"Debug leftover ({name.replace('_', ' ')}) — "
                    "fine locally, noisy in production logs."))
                break
    return out


def check_todos(f: FileDiff) -> list[dict]:
    """TODOs aren't evil, but they deserve to be tracked, not buried."""
    out = []
    for h in f.added_lines:
        m = TODO_RE.search(h.text)
        if m:
            note = m.group(2).strip()[:80]
            out.append(_finding(
                "info", f.path, h.new_no, "todos",
                f"New {m.group(1)}: \"{note}\" — "
                "worth a ticket if it isn't one already."))
    return out


def check_binary(f: FileDiff) -> list[dict]:
    if f.is_binary:
        return [_finding("warning", f.path, None, "binary",
                         "Binary file in the diff — can't review the contents, "
                         "make sure it belongs in the repo.")]
    return []


def check_diff_size(files: list[FileDiff], limit: int = 500) -> list[dict]:
    """Big diffs get skimmed, not reviewed. Say so out loud."""
    total = sum(f.added_count for f in files)
    if total > limit:
        return [_finding("warning", "<all files>", None, "diff_size",
                          f"Large diff: {total} added lines (limit {limit}). "
                          "Consider splitting — reviewers miss things past this size.")]
    return []


def check_tests_for_new_files(files: list[FileDiff]) -> list[dict]:
    """New source file with no new test file alongside it. Nudge, don't block."""
    out = []
    new_sources = [f for f in files
                   if f.is_new and any(f.path.endswith(e) for e in SOURCE_EXTS)]
    touched_tests = {f.path for f in files
                     if "test" in f.path.lower()}
    for f in new_sources:
        stem = f.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if not any(stem in t or t.startswith("test_" + stem) for t in touched_tests):
            out.append(_finding(
                "info", f.path, None, "missing_tests",
                "New source file with no test touched. "
                "Even a smoke test beats nothing."))
    return out


def run_all(files: list[FileDiff]) -> list[dict]:
    findings: list[dict] = []
    for f in files:
        findings += check_secrets(f)
        findings += check_debug_leftovers(f)
        findings += check_todos(f)
        findings += check_binary(f)
    findings += check_diff_size(files)
    findings += check_tests_for_new_files(files)
    return findings

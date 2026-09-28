#!/usr/bin/env python3
"""Evals: the sample PR has known issues planted in it — make sure we catch them.

Run:  python evals/run_eval.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewer.comments import findings_to_markdown  # noqa: E402
from reviewer.github import parse_pr_url  # noqa: E402
from reviewer.reviewer import Reviewer, make_backend  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# (description, predicate over the findings list)
EXPECTATIONS = [
    ("flags the AWS key as critical",
     lambda fs: any(f["severity"] == "critical" and "aws" in f["message"].lower()
                    for f in fs)),
    ("flags the debug print",
     lambda fs: any(f["check"] == "debug_leftovers" for f in fs)),
    ("flags the bare except",
     lambda fs: any("Bare except" in f["message"] for f in fs)),
    ("surfaces the TODO",
     lambda fs: any(f["check"] == "todos" for f in fs)),
    ("notes the new file has no tests",
     lambda fs: any(f["check"] == "missing_tests" for f in fs)),
]

# clean diff: one comment added to an existing file — nothing to flag
CLEAN_DIFF = ("diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
              "@@ -1,3 +1,4 @@\n x = 1\n+# just a comment\n y = 2\n")

RANK = {"critical": 0, "warning": 1, "info": 2}


def url_parses_ok() -> bool:
    try:
        return parse_pr_url("https://github.com/tushar29k/rag-service/pull/42") == (
            "tushar29k", "rag-service", 42)
    except ValueError:
        return False


def pulls_variant_ok() -> bool:
    # some people type /pulls/ instead of /pull/ — be lenient
    try:
        return parse_pr_url("https://github.com/o/r/pulls/7") == ("o", "r", 7)
    except ValueError:
        return False


def rejects_garbage() -> bool:
    for bad in ["not a url", "https://github.com/owner/repo",
                "https://github.com/owner/repo/issues/3", ""]:
        try:
            parse_pr_url(bad)
        except ValueError:
            continue
        return False
    return True


def findings_have_shape(fs) -> bool:
    needed = {"severity", "file", "check", "message"}
    return bool(fs) and all(needed <= set(f) and f["severity"] in RANK for f in fs)


def findings_sorted_by_severity(fs) -> bool:
    ranks = [RANK[f["severity"]] for f in fs]
    return ranks == sorted(ranks)


def clean_diff_stays_clean() -> bool:
    fs = Reviewer().review(CLEAN_DIFF)
    return not fs and "No issues found" in findings_to_markdown(fs)


def sample_pr_expectations(label: str, reviewer: Reviewer) -> tuple[int, list, int]:
    """The planted issues must be caught under every reviewer config."""
    with open(os.path.join(HERE, "sample_pr.diff")) as fh:
        findings = reviewer.review(fh.read())
    passed = 0
    for desc, pred in EXPECTATIONS:
        ok = pred(findings)
        print(f"{'PASS' if ok else 'FAIL'}  [{label}] {desc}")
        passed += ok
    return passed, findings, len(findings)


def _with_env(**vars) -> dict:
    # set env vars for a block, remembering what to restore
    old = {k: os.environ.get(k) for k in vars}
    for k, v in vars.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return old


def _restore_env(old: dict) -> None:
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def local_backend_runs_offline() -> bool:
    # the roadmap item's done-when: `reviewer: local` reviews the sample PR
    # with zero network (LOCAL_OFFLINE=1, weights served from the HF cache).
    # _load() must return True — that proves the real model loaded, not the
    # mock fallback.
    old = _with_env(LOCAL_OFFLINE="1", LOCAL_MODEL=None)
    try:
        backend = make_backend("local")
        if not backend._load():
            return False
        with open(os.path.join(HERE, "sample_pr.diff")) as fh:
            findings = Reviewer(backend).review(fh.read())
        return findings_have_shape(findings) and findings_sorted_by_severity(findings)
    except Exception:
        return False
    finally:
        _restore_env(old)


def local_backend_degrades_gracefully() -> bool:
    # unknown model + offline: no weights to load, so it must fall back to
    # the mock heuristics instead of raising — same spirit as keyless anthropic
    old = _with_env(LOCAL_OFFLINE="1", LOCAL_MODEL="no/such-model-here")
    try:
        with open(os.path.join(HERE, "sample_pr.diff")) as fh:
            findings = Reviewer(make_backend("local")).review(fh.read())
        return findings_have_shape(findings) and findings_sorted_by_severity(findings)
    except Exception:
        return False
    finally:
        _restore_env(old)


def load_samples() -> tuple[list, str]:
    # 5 sample PRs with planted issues + ground truth, from sample_prs.json
    with open(os.path.join(HERE, "sample_prs.json")) as fh:
        data = json.load(fh)
    return data["samples"], data.get("matching_rule", "")


def _check_matches_finding(gt: dict, finding: dict) -> bool:
    # same file, same check, same line region. a planted "llm" issue matches
    # any model-layer check (llm, llm-openai, llm-anthropic, llm-local)
    # because each backend names its own judgement layer differently.
    if gt["file"] != finding.get("file"):
        return False
    if gt["check"] == "llm":
        fc = finding.get("check", "")
        if not (fc == "llm" or fc.startswith("llm-")):
            return False
    elif gt["check"] != finding.get("check"):
        return False
    gl, fl = gt["line"], finding.get("line")
    if gl is None or fl is None:
        return gl is None and fl is None  # file-level notes match only each other
    return abs(gl - fl) <= 1  # line numbers drift by a line or two across edits


def score_findings(expected: list, findings: list) -> tuple[int, int, int]:
    # (true positives, false positives, false negatives) — each planted
    # issue consumes at most one finding, so dupes count as noise
    used = set()
    tp = 0
    for gt in expected:
        hit = next((i for i, f in enumerate(findings)
                    if i not in used and _check_matches_finding(gt, f)), None)
        if hit is not None:
            used.add(hit)
            tp += 1
    fp = len(findings) - len(used)
    return tp, fp, len(expected) - tp


def _fmt(score: float) -> str:
    return f"{score:.2f}"


def offline_backends() -> list[tuple[str, "Reviewer"]]:
    """Every backend that can run here, keyed or not.

    openai/free need real API keys — they sit this out offline and say so.
    anthropic without a key falls back to the mock heuristics (same as the
    plain mock run, but it exercises the backend plumbing end to end).
    local loads cached weights offline, else degrades to the mock fallback.
    """
    out = [("mock", Reviewer())]
    out.append(("anthropic (mock fallback, no key)", Reviewer(make_backend("anthropic"))))
    old = _with_env(LOCAL_OFFLINE="1", LOCAL_MODEL=None)
    try:
        from reviewer.local_backend import LocalBackend  # noqa: E402
        local = LocalBackend()
        try:
            real = bool(local._load())  # True = real weights, no mock anywhere
        except Exception:
            real = False
        label = "local (real weights)" if real else "local (mock fallback)"
        out.append((label, Reviewer(local)))
    finally:
        _restore_env(old)
    for name, env_key in (("openai", "OPENAI_API_KEY"), ("free", "LLM_API_KEY")):
        if os.environ.get(env_key):
            out.append((name, Reviewer(make_backend(name))))
        else:
            print(f"  (skipping backend '{name}': no {env_key} in this environment)")
    return out


def per_backend_scores() -> bool:
    """Run all 5 sample PRs through every offline-capable backend and print
    a precision/recall table per backend."""
    samples, rule = load_samples()
    backends = offline_backends()
    print(f"\nmatching rule: {rule}")
    print(f"\n{'backend':36s} {'sample':5s} {'tp':>3s} {'fp':>3s} {'fn':>3s} "
          f"{'precision':>9s} {'recall':>7s}")
    totals: dict[str, list[int]] = {}
    for label, reviewer in backends:
        acc = [0, 0, 0]
        for s in samples:
            with open(os.path.join(HERE, s["diff"])) as fh:
                findings = reviewer.review(fh.read())
            tp, fp, fn = score_findings(s["expected"], findings)
            acc[0] += tp
            acc[1] += fp
            acc[2] += fn
            p = tp / (tp + fp) if tp + fp else 1.0
            r = tp / (tp + fn) if tp + fn else 1.0
            print(f"{label:36s} {s['id']:5s} {tp:>3d} {fp:>3d} {fn:>3d} "
                  f"{_fmt(p):>9s} {_fmt(r):>7s}")
        totals[label] = acc
    print()
    for label, (tp, fp, fn) in totals.items():
        p = tp / (tp + fp) if tp + fp else 1.0
        r = tp / (tp + fn) if tp + fn else 1.0
        print(f"{'TOTAL ' + label:36s} {tp:>3d} {fp:>3d} {fn:>3d} "
              f"{_fmt(p):>9s} {_fmt(r):>7s}")
    # the table is the deliverable; informational only, never gates the run
    return True


def main() -> int:
    # keyless anthropic degrades to the mock heuristics, so the same
    # expectations hold under both configs
    mock_passed, findings, n_findings = sample_pr_expectations(
        "reviewer: mock", Reviewer())
    anth_passed, _, _ = sample_pr_expectations(
        "reviewer: anthropic", Reviewer(make_backend("anthropic")))
    passed = mock_passed + anth_passed

    extra = [
        ("parses a canonical PR link", url_parses_ok()),
        ("tolerates /pulls/ variant", pulls_variant_ok()),
        ("rejects non-PR links", rejects_garbage()),
        ("every finding has severity/file/check/message", findings_have_shape(findings)),
        ("findings sorted critical -> warning -> info", findings_sorted_by_severity(findings)),
        ("a clean diff stays clean", clean_diff_stays_clean()),
        ("local backend reviews the sample PR fully offline", local_backend_runs_offline()),
        ("local backend degrades gracefully without cached weights", local_backend_degrades_gracefully()),
    ]
    for desc, ok in extra:
        print(f"{'PASS' if ok else 'FAIL'}  {desc}")
        passed += ok

    total = 2 * len(EXPECTATIONS) + len(extra)
    print(f"\n{passed}/{total} expectations met "
          f"({n_findings} total findings)")
    if passed != total:
        return 1
    # the new eval set: per-backend precision/recall over 5 sample PRs
    per_backend_scores()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

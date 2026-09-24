#!/usr/bin/env python3
"""Evals: the sample PR has known issues planted in it — make sure we catch them.

Run:  python evals/run_eval.py
"""

import os
import sys

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
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

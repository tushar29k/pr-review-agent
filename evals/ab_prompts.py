#!/usr/bin/env python3
"""A/B the versioned reviewer prompts on the eval set.

For every prompt version in prompts/ and every offline-capable backend,
runs the 5 sample PRs, scores precision/recall, and declares a winner.

Winner = best mean F1 across backends. Tie-breaks: fewest false
positives, then the incumbent (v1) keeps the crown — the challenger has
to strictly beat it to dethrone it.

Run:  python evals/ab_prompts.py

Offline note: the mock backend is prompt-independent, so with no API keys
both versions score identically and v1 wins by incumbency. Run with
OPENAI_API_KEY or LLM_API_KEY set (or cached local weights) for a real A/B.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewer.diff_parser import parse_diff  # noqa: E402
from reviewer.prompts import (  # noqa: E402
    current_version,
    render_user,
    system_prompt,
    versions,
)

HERE = os.path.dirname(os.path.abspath(__file__))

# import the eval machinery instead of duplicating it
sys.path.insert(0, HERE)
from run_eval import (  # noqa: E402
    _restore_env,
    _with_env,
    load_samples,
    offline_backends,
    score_findings,
)

# the exact prompt the four backends used to carry as identical hardcoded
# copies, before prompts/ existed. v1 must render byte-identical to this —
# if someone edits prompts/v1/, this test fails loudly instead of letting
# the baseline silently drift.
_LEGACY_SYSTEM = (
    "You review code diffs. Reply with ONLY a JSON array of findings, no prose. "
    "Each finding: {\"severity\": \"critical\"|\"warning\"|\"info\", "
    "\"line\": <new-file line number or null>, "
    "\"confidence\": <0.0-1.0, how sure you are this is a real problem>, "
    "\"message\": \"<why it matters>\"}. "
    "Only flag added lines. critical = exploitable bugs/leaked secrets, "
    "warning = real defects, info = minor. Empty array if nothing is wrong. "
    "Report confidence honestly — don't inflate it to get attention."
)
_LEGACY_USER = ("File: x.py\n"
                "Diff (line numbers are new-file lines):\n"
                "  1: a = 1\n"
                "+ 2: b = 2\n"
                "  3: c = 3")
_TINY_DIFF = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
              "@@ -1,2 +1,3 @@\n a = 1\n+b = 2\n c = 3\n")


def v1_matches_legacy() -> bool:
    # the refactor moved the prompt, it must not have changed it
    if system_prompt("v1") != _LEGACY_SYSTEM:
        print("FAIL  v1 system prompt drifted from the legacy hardcoded prompt")
        return False
    f = parse_diff(_TINY_DIFF)[0]
    if render_user(f, "v1") != _LEGACY_USER:
        print("FAIL  v1 user prompt drifted from the legacy hardcoded format")
        return False
    print("PASS  v1 renders byte-identical to the legacy hardcoded prompt")
    return True


def _f1(tp: int, fp: int, fn: int) -> float:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return 2 * p * r / (p + r) if p + r else 0.0


def run_ab() -> tuple[list, bool]:
    """(rows, any_prompt_sensitive_backend) — rows are
    (version, backend label, tp, fp, fn).

    Backends are constructed once: they resolve the prompt version from the
    environment on every review, so switching versions is just an env flip —
    the local model's weights load exactly once, not once per version."""
    samples, _ = load_samples()
    backends = offline_backends()
    sensitive = any("mock" not in label for label, _ in backends)
    rows = []
    for v in versions():
        old = _with_env(REVIEWER_PROMPT_VERSION=v)
        try:
            for label, reviewer in backends:
                tp = fp = fn = 0
                for s in samples:
                    with open(os.path.join(HERE, s["diff"])) as fh:
                        findings = reviewer.review(fh.read())
                    a, b, c = score_findings(s["expected"], findings)
                    tp += a
                    fp += b
                    fn += c
                rows.append((v, label, tp, fp, fn))
        finally:
            _restore_env(old)
    return rows, sensitive


def declare_winner(rows: list) -> str:
    per_version: dict[str, dict] = {}
    for v, _label, tp, fp, fn in rows:
        d = per_version.setdefault(v, {"f1s": [], "fp": 0})
        d["f1s"].append(_f1(tp, fp, fn))
        d["fp"] += fp
    ranked = sorted(
        per_version.items(),
        key=lambda kv: (-sum(kv[1]["f1s"]) / len(kv[1]["f1s"]),
                        kv[1]["fp"],
                        kv[0] != "v1"),  # exact ties: the incumbent keeps it
    )
    return ranked[0][0]


def main() -> int:
    if not v1_matches_legacy():
        return 1
    vs = versions()
    if not vs:
        print("no prompt versions found in prompts/ — nothing to A/B")
        return 1
    print(f"\nA/B over prompt versions: {', '.join(vs)} "
          f"(incumbent: {current_version()})")

    rows, sensitive = run_ab()
    print(f"\n{'version':9s} {'backend':36s} {'tp':>3s} {'fp':>3s} {'fn':>3s} "
          f"{'prec':>5s} {'rec':>5s} {'f1':>5s}")
    for v, label, tp, fp, fn in rows:
        p = tp / (tp + fp) if tp + fp else 1.0
        r = tp / (tp + fn) if tp + fn else 1.0
        print(f"{v:9s} {label:36s} {tp:>3d} {fp:>3d} {fn:>3d} "
              f"{p:>5.2f} {r:>5.2f} {_f1(tp, fp, fn):>5.2f}")

    winner = declare_winner(rows)
    print(f"\nWINNER: prompt {winner}")
    if not sensitive:
        print("note: only the prompt-independent mock ran here, so both "
              "versions scored identically and the incumbent kept the crown "
              "by tie-break. set OPENAI_API_KEY or LLM_API_KEY (or cache "
              "local weights) for a real A/B.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

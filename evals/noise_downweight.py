#!/usr/bin/env python3
"""Replay eval: noisy checks get suppressed, everything else is untouched.

noise_stats.py learned which checks each repo dismisses; downweight.py
closes the loop. This replays the 5 sample PR diffs (fully offline, mock
backend) once plain and once with the dismissal history for one repo,
and proves:

  1. the feedback actually flags a noisy check (else the eval is vacuous),
  2. at least one finding from a noisy check is fully suppressed in the
     weighted run,
  3. every surviving noisy-check finding is tagged downweighted=True,
  4. findings from every other check are byte-identical between runs.

Run:  python evals/noise_downweight.py [--log PATH] [--repo OWNER/REPO]
Exit 0 when the noisy check is suppressed and nothing else moved.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewer.downweight import noisy_checks  # noqa: E402
from reviewer.noise_stats import load_events  # noqa: E402
from reviewer.reviewer import MockBackend, Reviewer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_SAMPLE = os.path.join(HERE, "sample_feedback.jsonl")
DIFFS = [os.path.join(HERE, f"sample_pr{d}.diff") for d in ("", "2", "3", "4", "5")]


def key(f: dict) -> tuple:
    return (f["file"], f.get("line"), f["check"], f["severity"], f["message"])


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="replay eval for noise down-weighting")
    ap.add_argument("--log", default=os.environ.get("FEEDBACK_LOG_PATH",
                                                    "feedback.jsonl"))
    ap.add_argument("--repo", default="tushar29k/pr-review-agent")
    args = ap.parse_args(argv)

    events = load_events(args.log)
    sample = False
    if not events:
        events = load_events(_SAMPLE)
        sample = True
    if not events:
        print("no feedback events anywhere — nothing to replay against")
        return 1
    if sample:
        print("SAMPLE DATA — no real feedback.jsonl found; replaying "
              "against evals/sample_feedback.jsonl.\n")

    noisy = noisy_checks(events, args.repo)
    print(f"repo: {args.repo}")
    print(f"noisy checks: {noisy or 'none'}")
    if not noisy:
        print("FAIL: nothing flagged noisy — the eval would prove nothing")
        return 1

    plain, weighted = Reviewer(MockBackend()), Reviewer(
        MockBackend(), noise_weights=noisy)
    ok = True
    total_suppressed = 0
    header = (f"{'diff':<18}{'check':<16}{'noisy':>8}{'baseline':>10}"
              f"{'weighted':>10}{'suppressed':>12}  verdict")
    print("\n" + header)
    print("-" * len(header))
    for diff_path in DIFFS:
        with open(diff_path) as fh:
            diff = fh.read()
        base = plain.review(diff)
        w = weighted.review(diff)
        wsupp = weighted.suppressed_last
        total_suppressed += sum(1 for f in wsupp if f.get("check") in noisy)
        for check in sorted({f["check"] for f in base} | set(noisy)):
            b = [f for f in base if f["check"] == check]
            k = [f for f in w if f["check"] == check]
            s = [f for f in wsupp if f.get("check") == check]
            tag = ""
            if check in noisy:
                # every surviving noisy finding must carry the tag; the
                # "something actually got suppressed" proof is the global
                # count at the bottom, not a per-row demand
                untagged = [f for f in k if not f.get("downweighted")]
                tag = ("OK" if not untagged
                       else f"FAIL ({len(untagged)} untagged survivors)")
                if tag.startswith("FAIL"):
                    ok = False
            else:
                # non-noisy checks: identical findings, nothing moved
                same = Counter(key(f) for f in b) == Counter(key(f) for f in k)
                tag = "OK" if same else "FAIL (findings changed!)"
                if not same:
                    ok = False
            noisy_s = f"{noisy[check]:.0%}" if check in noisy else ""
            name = os.path.basename(diff_path)
            print(f"{name:<18}{check:<16}{noisy_s:>8}{len(b):>10}"
                  f"{len(k):>10}{len(s):>12}  {tag}")

    # the headline assertion: a noisy check actually got suppressed
    print(f"\nnoisy-check findings suppressed across the replay: "
          f"{total_suppressed}")
    if not total_suppressed:
        print("FAIL: no noisy-check finding was suppressed")
        ok = False
    print("PASS: noisy check suppressed, everything else untouched"
          if ok else "FAIL: see rows above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

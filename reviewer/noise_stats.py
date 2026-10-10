"""Per-repo noise stats: dismissal rate per check from the feedback log.

Reads the JSONL written by `reviewer/feedback.py` (reactions on our review
comments, ingested via POST /feedback/ingest) and prints one table per repo:
for each check, endorsements (+1 etc), dismissals (-1), neutral, and the
dismissal rate = dismissals / (dismissals + endorsements). Checks crossing
the noise threshold with enough samples get flagged NOISY — those are the
candidates for down-weighting in tomorrow's roadmap item.

No real log yet? Falls back to evals/sample_feedback.jsonl and says so out
loud, so the table always prints and the flag logic is exercised.

Run:  python3 -m reviewer.noise_stats [--log PATH] [--threshold 0.5] [--min-n 3]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "evals", "sample_feedback.jsonl")


def load_events(path: str) -> list[dict]:
    """Read a feedback JSONL file, skipping blanks and bad rows."""
    events = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # one corrupt row shouldn't kill the table
    except OSError:
        pass  # missing log = no feedback yet, not an error
    return events


def compute_stats(events: list[dict]) -> dict[tuple[str, str], dict]:
    """Group by (repo, check): count endorsements/dismissals/neutrals."""
    stats: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"positive": 0, "negative": 0, "neutral": 0})
    for ev in events:
        repo = ev.get("repo") or "(unknown repo)"
        finding = ev.get("finding") or {}
        check = finding.get("check") or "(summary)"  # summary comments have no check
        sent = ev.get("sentiment") or "neutral"
        if sent not in stats[(repo, check)]:
            sent = "neutral"  # unknown sentiment reads as no verdict
        stats[(repo, check)][sent] += 1
    return stats


def dismissal_rate(pos: int, neg: int) -> float | None:
    """👎 share of reactions with a verdict. None when nobody voted."""
    total = pos + neg
    return neg / total if total else None


def print_table(stats: dict[tuple[str, str], dict],
                threshold: float, min_n: int, sample: bool) -> None:
    """One ASCII table per repo: check, counts, dismissal rate, noise flag."""
    by_repo: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for (repo, check), counts in stats.items():
        by_repo[repo].append((check, counts))

    if sample:
        print("SAMPLE DATA — no real feedback.jsonl found; table below is "
              "over evals/sample_feedback.jsonl.\n"
              "Real rows from POST /feedback/ingest will replace it.")

    for repo in sorted(by_repo):
        print(f"\nrepo: {repo}")
        print(f"{'check':<16}{'👍':>5}{'👎':>5}{'neutral':>9}"
              f"{'dismissals/total':>18}{'rate':>7}  flag")
        print("-" * 76)
        rows = sorted(by_repo[repo], key=lambda rc: rc[0])
        for check, c in rows:
            pos, neg, neu = c["positive"], c["negative"], c["neutral"]
            rate = dismissal_rate(pos, neg)
            rate_s = f"{rate:.2f}" if rate is not None else "n/a"
            frac = f"{neg}/{pos + neg}"
            # noisy = dismissal rate at/above threshold on enough samples
            flag = ("NOISY" if rate is not None and rate >= threshold
                    and (pos + neg) >= min_n else "")
            print(f"{check:<16}{pos:>5}{neg:>5}{neu:>9}"
                  f"{frac:>18}{rate_s:>7}  {flag}")
    print(f"\n{len(stats)} check/repo pairs from {sum(c['positive'] + c['negative'] + c['neutral'] for c in stats.values())} "
          f"reactions | noisy = dismissal rate >= {threshold} on >= {min_n} verdicts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print per-repo, per-check dismissal rates from the "
                    "feedback log.")
    parser.add_argument("--log", default=os.environ.get("FEEDBACK_LOG_PATH",
                                                        "feedback.jsonl"),
                        help="feedback JSONL (default: $FEEDBACK_LOG_PATH or "
                             "feedback.jsonl)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="dismissal rate at/above which a check is noisy")
    parser.add_argument("--min-n", type=int, default=3,
                        help="minimum verdicts before a flag counts")
    args = parser.parse_args(argv)

    events = load_events(args.log)
    sample = False
    if not events:
        events = load_events(_SAMPLE)
        sample = True
    if not events:
        print("no feedback events anywhere — nothing to tabulate")
        return 0
    print_table(compute_stats(events), args.threshold, args.min_n, sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Review a diff file and print the markdown review comment.

Usage:
    python cli.py --diff evals/sample_pr.diff
"""

import argparse
import sys

from reviewer.comments import findings_to_markdown
from reviewer.reviewer import Reviewer


def main() -> int:
    ap = argparse.ArgumentParser(description="Review a unified diff.")
    ap.add_argument("--diff", required=True, help="Path to a unified diff file")
    args = ap.parse_args()

    try:
        with open(args.diff) as fh:
            diff_text = fh.read()
    except OSError as exc:
        print(f"can't read {args.diff}: {exc}", file=sys.stderr)
        return 1

    findings = Reviewer().review(diff_text)
    print(findings_to_markdown(findings))
    # exit 1 if anything critical turned up — handy for CI gates
    return 1 if any(f["severity"] == "critical" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

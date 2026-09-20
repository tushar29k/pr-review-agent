#!/usr/bin/env python3
"""Evals: the sample PR has known issues planted in it — make sure we catch them.

Run:  python evals/run_eval.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewer.reviewer import Reviewer  # noqa: E402

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


def main() -> int:
    with open(os.path.join(HERE, "sample_pr.diff")) as fh:
        findings = Reviewer().review(fh.read())

    passed = 0
    for desc, pred in EXPECTATIONS:
        ok = pred(findings)
        print(f"{'PASS' if ok else 'FAIL'}  {desc}")
        passed += ok

    print(f"\n{passed}/{len(EXPECTATIONS)} expectations met "
          f"({len(findings)} total findings)")
    return 0 if passed == len(EXPECTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

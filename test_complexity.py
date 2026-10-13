#!/usr/bin/env python3
"""Complexity-smell checks test: long functions and deep nesting in Python.

Proves the done-when for "Complexity smells":
  1. a function over the line limit is flagged (and a short one isn't)
  2. a function nesting past the depth limit is flagged (a flat one isn't)
  3. the limits come from config: env vars and .pr-review.yaml both move them
  4. non-Python files stay quiet, and modified files are skipped (half a
     function tells you nothing — the check refuses to guess)

Run:  python3 test_complexity.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer.tree_sitter_checks import check_complexity  # noqa: E402
from reviewer.diff_parser import parse_diff  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def new_file_diff(path, body):
    lines = "".join(f"+{ln}\n" for ln in body.splitlines())
    n = len(body.splitlines())
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{n} @@\n{lines}")


def one_file(diff):
    return parse_diff(diff)[0]


LONG_BODY = "\n".join(f"    step_{i} = {i} * 2" for i in range(55))
LONG_FN = f"def marathon():\n{LONG_BODY}\n    return step_54\n"
SHORT_FN = "def sprint():\n    return 1\n"
# if > for > while > if = 4 nesting levels
NESTED_FN = ("def deep():\n"
             "    if True:\n"
             "        for i in range(3):\n"
             "            while i:\n"
             "                if i > 1:\n"
             "                    return i\n"
             "    return 0\n")
FLAT_FN = ("def flat():\n"
           "    if True:\n"
           "        return 1\n"
           "    return 0\n")


def findings(diff, **kw):
    return check_complexity(one_file(diff), **kw)


def main():
    msgs = [f["message"] for f in findings(new_file_diff("a.py", LONG_FN))]
    check("long function flagged", any("Long function" in m for m in msgs))
    check("long message names the function and the count",
          any("marathon" in m and "56" in m for m in msgs))

    check("short function stays quiet",
          findings(new_file_diff("a.py", SHORT_FN)) == [])

    msgs = [f["message"] for f in findings(new_file_diff("a.py", NESTED_FN))]
    check("deep nesting flagged", any("Deep nesting" in m for m in msgs))
    check("nesting message names the depth",
          any("deep" in m and "4 levels" in m for m in msgs))

    check("flat function stays quiet",
          findings(new_file_diff("a.py", FLAT_FN)) == [])

    check("non-python file skipped",
          findings(new_file_diff("a.js", LONG_FN)) == [])

    # a genuinely modified file (not a new file): the check must stay off
    mod = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
           "@@ -1,2 +1,60 @@\n x = 1\n" +
           "".join(f"+{ln}\n" for ln in LONG_FN.splitlines()))
    check("modified file skipped (half a function tells you nothing)",
          findings(mod) == [])

    # thresholds move: a 56-line function passes a 60-line limit
    check("max_lines override respected",
          findings(new_file_diff("a.py", LONG_FN), max_lines=60) == [])
    check("max_depth override respected",
          findings(new_file_diff("a.py", NESTED_FN), max_depth=5) == [])
    check("tighter limit flags more",
          len(findings(new_file_diff(
              "a.py", "def two():\n    x = 1\n    return x\n"),
              max_lines=1)) == 1)

    # env-var knobs (config.py) move the defaults
    os.environ["COMPLEXITY_MAX_LINES"] = "60"
    try:
        import importlib
        import reviewer.config as cfg
        importlib.reload(cfg)
        check("env COMPLEXITY_MAX_LINES respected",
              findings(new_file_diff("a.py", LONG_FN)) == [])
    finally:
        del os.environ["COMPLEXITY_MAX_LINES"]
        importlib.reload(cfg)

    # .pr-review.yaml complexity: section parses, bad values don't crash
    from reviewer.repo_config import parse_config_text
    rc = parse_config_text("complexity:\n  max_function_lines: 60\n"
                           "  max_nesting_depth: 5\n")
    check("yaml complexity limits parsed", rc.complexity_limits() == (60, 5))
    rc = parse_config_text("complexity:\n  max_function_lines: nope\n")
    check("bad yaml value falls back to default",
          rc.complexity_limits() == (50, 3))
    check("absent section uses defaults",
          parse_config_text("").complexity_limits() == (50, 3))

    # repo limits flow through run_all
    from reviewer import checks
    fs = checks.run_all([one_file(new_file_diff("a.py", LONG_FN))],
                        complexity_limits=(60, 3))
    check("run_all honors repo limits",
          all(f["check"] != "complexity" for f in fs))
    fs = checks.run_all([one_file(new_file_diff("a.py", LONG_FN))],
                        enabled_checks=frozenset({"secrets"}))
    check("disabled check stays off",
          all(f["check"] != "complexity" for f in fs))

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

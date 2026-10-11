#!/usr/bin/env python3
"""Noise down-weighting tests: learn from dismissal history, soften noise.

No network needed: hand-built feedback events exercise the noisy-check
detection, the demote-one-level rule, and the Reviewer wiring end to
end on the sample diff. The headline guarantee: info findings from a
noisy check vanish, criticals never fully hide, and with no weights the
review is byte-identical to before.

Run:  python3 test_downweight.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer import downweight  # noqa: E402
from reviewer.reviewer import Reviewer  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def ev(repo, check_name, sentiment):
    return {"repo": repo, "sentiment": sentiment,
            "finding": {"check": check_name, "severity": "info"}}


def noisy_events():
    # todos: 3 dismissals, 1 endorsement -> 0.75 on 4 verdicts: noisy
    out = [ev("o/r", "todos", "negative") for _ in range(3)]
    out += [ev("o/r", "todos", "positive")]
    # secrets: 1 dismissal, 3 endorsements -> 0.25: not noisy
    out += [ev("o/r", "secrets", "negative")]
    out += [ev("o/r", "secrets", "positive") for _ in range(3)]
    # debug_leftovers: 2 dismissals only -> min_n not met: not noisy
    out += [ev("o/r", "debug_leftovers", "negative") for _ in range(2)]
    # other repo's noisy todos must not leak into o/r
    out += [ev("other/repo", "todos", "negative") for _ in range(5)]
    return out


# --- noisy detection ---------------------------------------------------------
noisy = downweight.noisy_checks(noisy_events(), "o/r")
check("todos flagged noisy", "todos" in noisy)
check("rate is 0.75", abs(noisy.get("todos", 0) - 0.75) < 1e-9)
check("endorsed check not noisy", "secrets" not in noisy)
check("too-few-verdicts not noisy", "debug_leftovers" not in noisy)
check("other repo's noise doesn't leak", len(noisy) == 1)
check("unknown repo -> no weights",
      downweight.noisy_checks(noisy_events(), "nobody/here") == {})
check("empty events -> no weights", downweight.noisy_checks([], "o/r") == {})
check("stricter threshold drops it",
      downweight.noisy_checks(noisy_events(), "o/r", threshold=0.9) == {})
check("higher min_n drops it",
      downweight.noisy_checks(noisy_events(), "o/r", min_n=5) == {})

# --- demotion rule -----------------------------------------------------------
def finding(check_name, severity):
    return {"severity": severity, "file": "a.py", "line": 1,
            "check": check_name, "message": "m"}

kept, supp = downweight.apply_downweight(
    [finding("todos", "info"), finding("todos", "warning"),
     finding("todos", "critical"), finding("secrets", "info")],
    {"todos": 0.75})
check("info from noisy check suppressed", len(supp) == 1
      and supp[0]["check"] == "todos" and supp[0]["severity"] == "info")
check("suppressed finding tagged", supp[0].get("downweighted")
      and abs(supp[0].get("dismissal_rate", 0) - 0.75) < 1e-9)
check("warning demoted to info", any(f["severity"] == "info"
      and f["check"] == "todos" and f.get("downweighted") for f in kept))
check("critical softened to warning, never dropped",
      any(f["severity"] == "warning" and f["check"] == "todos"
          and f.get("downweighted") for f in kept)
      and not any(f["check"] == "todos" and f["severity"] == "critical"
                  for f in kept))
check("non-noisy check untouched",
      any(f["check"] == "secrets" and f["severity"] == "info"
          and "downweighted" not in f for f in kept))
check("no weights -> identity", downweight.apply_downweight(
    [finding("todos", "info")], {}) == ([finding("todos", "info")], []))
check("unknown severity not dropped",
      downweight.apply_downweight(
          [finding("todos", "mystery")], {"todos": 0.75})[0]
      == [finding("todos", "mystery")])
check("missing check key kept",
      downweight.apply_downweight(
          [{"severity": "info"}], {"todos": 0.75})[0] == [{"severity": "info"}])

orig = finding("todos", "warning")
downweight.apply_downweight([orig], {"todos": 0.75})
check("originals not mutated", orig["severity"] == "warning"
      and "downweighted" not in orig)

# --- log loading -------------------------------------------------------------
check("missing log -> {}",
      downweight.load_noisy_weights("/no/such/file.jsonl", "o/r") == {})
with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
    for e in noisy_events():
        fh.write(json.dumps(e) + "\n")
    tmp = fh.name
try:
    check("weights load from a real log",
          downweight.load_noisy_weights(tmp, "o/r") == {"todos": 0.75})
finally:
    os.unlink(tmp)

# --- Reviewer wiring ---------------------------------------------------------
with open("evals/sample_pr.diff") as fh:
    sample = fh.read()
base = Reviewer().review(sample)
w = Reviewer(noise_weights={"todos": 0.75})
weighted = w.review(sample)
check("plain Reviewer unchanged (no weights)",
      base == Reviewer().review(sample) and w.suppressed_last is not None)
check("todos suppressed in the weighted run",
      not any(f["check"] == "todos" for f in weighted)
      and any(f.get("check") == "todos" for f in w.suppressed_last))
check("other checks identical",
      [f for f in base if f["check"] != "todos"]
      == [f for f in weighted if f["check"] != "todos"])
check("suppressed_last empty without weights",
      Reviewer().suppressed_last == [])

# secrets noisy: demoted, never dropped
sec = Reviewer(noise_weights={"secrets": 0.9}).review(sample)
sec_base = [f for f in base if f["check"] == "secrets"]
sec_w = [f for f in sec if f["check"] == "secrets"]
check("noisy secrets demoted to warning, still present",
      len(sec_w) == len(sec_base) > 0
      and all(f["severity"] == "warning" for f in sec_w))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

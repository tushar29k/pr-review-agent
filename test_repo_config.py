#!/usr/bin/env python3
"""Repo config test: a .pr-review.yaml must change review behaviour.

Proves the done-when for config file support:
  1. same sample diff reviewed with a config that disables one check ->
     that check's findings disappear, the rest stay
  2. a severity_thresholds override remaps model confidences onto new
     severities (deterministic findings keep their fixed severities)
  3. discovery order: explicit --config path > repo root > defaults
  4. the cli picks up .pr-review.yaml from the cwd and honors --config

Run:  python3 test_repo_config.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer.repo_config import (  # noqa: E402
    RepoConfig, discover_config, parse_config_text)
from reviewer.reviewer import Reviewer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DIFF = open(os.path.join(HERE, "evals", "sample_pr.diff")).read()

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def fresh_repo(config_text):
    # a "test repo": a directory with a .pr-review.yaml in its root
    d = tempfile.mkdtemp(prefix="prreview-testrepo-")
    with open(os.path.join(d, ".pr-review.yaml"), "w") as fh:
        fh.write(config_text)
    shutil.copy(os.path.join(HERE, "evals", "sample_pr.diff"),
                os.path.join(d, "sample_pr.diff"))
    return d


print("== baseline: no config, all checks run ==")
base = Reviewer().review(DIFF)
base_checks = {f["check"] for f in base}
check("baseline has the planted findings",
      {"secrets", "debug_leftovers", "todos", "llm",
       "missing_tests"} <= base_checks)
bare = [f for f in base if f["check"] == "llm"][0]
check("bare except is warning at default thresholds (0.55 < 0.80)",
      bare["severity"] == "warning")

print("== enabled_checks: disabling one check removes its findings ==")
d = fresh_repo("enabled_checks:\n  - secrets\n  - debug_leftovers\n  - todos\n"
               "  - binary\n  - diff_size\n  - missing_tests\n")
# note: "llm" deliberately left out -> the model pass is skipped too
cfg = discover_config(repo_root=d)
check("repo root config discovered", cfg.enabled_checks is not None
      and "llm" not in cfg.enabled_checks)
fs = Reviewer(config=cfg).review(DIFF)
checks = {f["check"] for f in fs}
check("disabled llm check produced no findings", "llm" not in checks)
check("other checks still ran",
      {"secrets", "debug_leftovers", "todos"} <= checks)

d2 = fresh_repo("enabled_checks:\n  - secrets\n")
fs2 = Reviewer(config=discover_config(repo_root=d2)).review(DIFF)
check("only secrets ran when only secrets enabled",
      {f["check"] for f in fs2} == {"secrets"} and len(fs2) == 2)
check("the two secret findings are still critical (deterministic "
      "severities are fixed)", all(f["severity"] == "critical" for f in fs2))

print("== severity_thresholds: overrides remap model confidences ==")
d3 = fresh_repo("severity_thresholds:\n  critical: 0.50\n")
fs3 = Reviewer(config=discover_config(repo_root=d3)).review(DIFF)
bare3 = [f for f in fs3 if f["check"] == "llm"][0]
check("lowered critical threshold promotes the 0.55 bare except to critical",
      bare3["severity"] == "critical")
check("deterministic severities untouched by the threshold override",
      all(f["severity"] == "critical" for f in fs3
          if f["check"] == "secrets"))

d4 = fresh_repo("severity_thresholds:\n  warning: 0.60\n")
fs4 = Reviewer(config=discover_config(repo_root=d4)).review(DIFF)
bare4 = [f for f in fs4 if f["check"] == "llm"][0]
check("raised warning threshold demotes the 0.55 bare except to info",
      bare4["severity"] == "info")

print("== discovery order: explicit path beats repo root beats defaults ==")
d5 = fresh_repo("enabled_checks:\n  - secrets\n")
explicit = os.path.join(d5, "other.yaml")
with open(explicit, "w") as fh:
    fh.write("enabled_checks:\n  - todos\n")
cfg5 = discover_config(config_path=explicit, repo_root=d5)
check("explicit --config wins over the repo root file",
      cfg5.enabled_checks == frozenset({"todos"}))
cfg6 = discover_config(repo_root="/nonexistent-dir-xyz")
check("missing config everywhere falls back to defaults",
      cfg6 == RepoConfig())

print("== parser robustness ==")
cfg7 = parse_config_text(
    "# a comment\n"
    "enabled_checks:\n"
    "  - secrets\n"
    "  - bogus_check  # unknown, should warn and be skipped\n"
    "severity_thresholds:\n"
    "  critical: not-a-number\n"
    "  warning: 0.7\n")
check("unknown check names are dropped, valid ones kept",
      cfg7.enabled_checks == frozenset({"secrets"}))
check("bad threshold ignored, good one kept",
      cfg7.critical_threshold is None and cfg7.warning_threshold == 0.7)
check("thresholds clamp to 0-1",
      parse_config_text(
          "severity_thresholds:\n  critical: 5\n").critical_threshold == 1.0)

print("== cli end to end in the test repo ==")
d8 = fresh_repo("enabled_checks:\n  - secrets\n  - todos\n  - binary\n"
               "  - diff_size\n  - missing_tests\n  - llm\n")
# debug_leftovers left out -> its finding must vanish from cli output
r = subprocess.run(
    [sys.executable, os.path.join(HERE, "cli.py"),
     "--diff", "sample_pr.diff", "--reviewer", "mock"],
    cwd=d8, capture_output=True, text=True)
check("cli auto-discovers .pr-review.yaml in the cwd",
      r.returncode in (0, 1) and "Debug leftover" not in r.stdout
      and "aws access key" in r.stdout)
r2 = subprocess.run(
    [sys.executable, os.path.join(HERE, "cli.py"),
     "--diff", "sample_pr.diff", "--reviewer", "mock",
     "--config", explicit],
    cwd=d8, capture_output=True, text=True)
check("cli --config overrides the cwd file",
      "Debug leftover" not in r2.stdout and "aws access key" not in r2.stdout
      and "TODO" in r2.stdout)

print("== github: fetch_repo_config degrades gracefully ==")
import base64 as _b64  # noqa: E402
import json as _json  # noqa: E402
import reviewer.github as _gh  # noqa: E402

_real_get = _gh._get


def _fake_contents_ok(path, accept, token=None):
    payload = {"content": _b64.b64encode(
        b"enabled_checks:\n  - secrets\n").decode(),
        "encoding": "base64"}
    return _json.dumps(payload).encode(), 200


_gh._get = _fake_contents_ok
try:
    text = _gh.fetch_repo_config("some", "repo", ref="abc123")
finally:
    _gh._get = _real_get
check("config file fetched and base64-decoded through the contents api",
      text is not None and "enabled_checks" in text
      and parse_config_text(text).enabled_checks == frozenset({"secrets"}))


def _fake_404(path, accept, token=None):
    raise _gh.PRFetchError("PR not found")


_gh._get = _fake_404
try:
    missing = _gh.fetch_repo_config("some", "repo")
finally:
    _gh._get = _real_get
check("missing config file degrades to None (defaults apply)",
      missing is None)

for d_ in (d, d2, d3, d4, d5, d8):
    shutil.rmtree(d_, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

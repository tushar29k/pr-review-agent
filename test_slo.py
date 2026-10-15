#!/usr/bin/env python3
"""Time-to-first-review metric + SLO alert threshold tests.

The metric: ms from review start until the first model findings are ready
(first completed backend.review_file call), or until the deterministic
checks finished when no model pass runs. Every per-review JSONL record
carries it as first_review_ms alongside slo_ms and slo_breach; a breach
also emits a warning log line so it's visible while tailing output.

Run:  python3 test_slo.py
"""

import json
import logging
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer.reviewer import ReviewBackend, Reviewer  # noqa: E402
from reviewer.repo_config import RepoConfig  # noqa: E402

_DIFF = """diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,4 @@
 def ok():
     pass
+def new():
+    pass
"""


class SlowBackend(ReviewBackend):
    """Pretends the model needs real time: 50ms per file."""

    def review_file(self, f):
        time.sleep(0.05)
        return []


passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"ok   {name}")
    else:
        failed += 1
        print(f"FAIL {name}")


def run_logged(backend=None, config=None):
    r = Reviewer(backend=backend, config=config)
    tmp = tempfile.NamedTemporaryFile("r", suffix=".jsonl", delete=False)
    tmp.close()
    r.review_and_log(_DIFF, log_path=tmp.name)
    with open(tmp.name, encoding="utf-8") as fh:
        return json.loads(fh.readline())


# 1. metric is logged per review
rec = run_logged()
check("first_review_ms present in record", rec.get("first_review_ms") is not None)
check("first_review_ms non-negative", rec["first_review_ms"] >= 0)
check("first_review_ms inside latency_ms too",
      rec["latency_ms"]["first_review_ms"] == rec["first_review_ms"])
check("first_review_ms <= total_ms",
      rec["first_review_ms"] <= rec["latency_ms"]["total_ms"])
check("default budget 30000ms", rec["slo_ms"] == 30000.0)

# 2. slow backend: metric actually reflects the model wait
rec = run_logged(backend=SlowBackend())
check("slow backend shows in metric", rec["first_review_ms"] >= 40)
check("no breach under default budget", rec["slo_breach"] is False)

# 3. env override + breach flags the record
os.environ["REVIEW_SLO_MS"] = "1"
try:
    rec = run_logged(backend=SlowBackend())
    check("tiny budget records slo_ms", rec["slo_ms"] == 1.0)
    check("breach flagged in record", rec["slo_breach"] is True)
finally:
    del os.environ["REVIEW_SLO_MS"]

# 4. breach also emits a log line
os.environ["REVIEW_SLO_MS"] = "1"
records = []
handler = logging.Handler()
handler.emit = lambda r: records.append(r.getMessage())
logger = logging.getLogger("pr-review-agent.reviewer")
logger.addHandler(handler)
try:
    run_logged(backend=SlowBackend())
finally:
    logger.removeHandler(handler)
    del os.environ["REVIEW_SLO_MS"]
check("breach emits warning log line",
      any("SLO breach" in m for m in records))

# 5. llm check disabled: deterministic findings are the first review content
rec = run_logged(config=RepoConfig(enabled_checks={"secrets", "todos"}))
check("fallback metric logged without llm", rec.get("first_review_ms") is not None)
check("fallback ≈ parse+checks",
      abs(rec["first_review_ms"]
          - (rec["latency_ms"]["parse_ms"] + rec["latency_ms"]["checks_ms"])) < 1.0)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""Feedback capture test: reaction ingestion into JSONL.

No network needed for the core: a simulated reactions API list (the shape
GitHub returns for comment reactions) must land in the JSONL with the right
sentiment, dedupe, and finding context. The live-fetch wiring is tested
against a stubbed urlopen so no token or connection is required.

Run:  python3 test_feedback.py
"""

import io
import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer import feedback  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


# --- sentiment mapping -------------------------------------------------------
check("thumbs up is positive",
      feedback.sentiment_for("+1") == "positive")
check("celebrations are positive",
      all(feedback.sentiment_for(c) == "positive"
          for c in ("hooray", "rocket", "heart", "laugh", "eyes")))
check("thumbs down is negative",
      feedback.sentiment_for("-1") == "negative")
check("confused stays neutral (unclear != wrong)",
      feedback.sentiment_for("confused") == "neutral")
check("unknown content degrades to neutral, never crashes",
      feedback.sentiment_for("party_parrot") == "neutral")
check("whitespace around content is tolerated",
      feedback.sentiment_for("  +1 ") == "positive")

# --- dedupe ------------------------------------------------------------------
dupes = [
    {"content": "+1", "user": {"login": "amy"},
     "created_at": "2026-10-08T10:00:00Z"},
    {"content": "+1", "user": {"login": "amy"},
     "created_at": "2026-10-08T10:00:00Z"},  # retry double-post, same second
    {"content": "-1", "user": {"login": "amy"},
     "created_at": "2026-10-08T11:00:00Z"},
    {"content": "+1", "user": {"login": "bob"},
     "created_at": "2026-10-08T09:00:00Z"},
    {"content": "+1", "user": {}, "created_at": ""},  # malformed, dropped
]
norm = feedback.normalize_reactions(dupes)
check("one event per (user, content): 5 rows -> 3",
      len(norm) == 3)
check("different contents from the same user are distinct",
      sorted((r["user"], r["content"]) for r in norm)
      == [("amy", "+1"), ("amy", "-1"), ("bob", "+1")])

# --- inline body -> finding context ------------------------------------------
check("inline header parses to severity + check",
      feedback.finding_from_inline_body("**warning** `no_debug`\n\nx = 1")
      == {"severity": "warning", "check": "no_debug"})
check("foreign comment bodies yield no finding context",
      feedback.finding_from_inline_body("lgtm, thanks!") is None)

# --- offline ingest: the done-when path --------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    log = os.path.join(tmp, "feedback.jsonl")
    api_list = [
        {"content": "+1", "user": {"login": "amy"},
         "created_at": "2026-10-08T10:00:00Z"},
        {"content": "-1", "user": {"login": "bob"},
         "created_at": "2026-10-08T12:00:00Z"},
        {"content": "+1", "user": {"login": "amy"},
         "created_at": "2026-10-08T10:00:00Z"},  # dupe
    ]
    events = feedback.ingest_reactions(
        "psf/requests", 7616, 123456, "inline", api_list, log,
        finding={"severity": "warning", "check": "no_debug"})

    check("ingest returns one event per unique reaction",
          len(events) == 2)
    lines = [json.loads(l) for l in open(log)]
    check("both events landed in the JSONL file", len(lines) == 2)

    amy = next(e for e in lines if e["user"] == "amy")
    check("event carries repo/pr/comment identity",
          (amy["repo"], amy["pr_number"], amy["comment_id"],
           amy["comment_kind"])
          == ("psf/requests", 7616, 123456, "inline"))
    check("reaction and sentiment ride along",
          (amy["reaction"], amy["sentiment"]) == ("+1", "positive"))
    check("reacted_at keeps the API timestamp",
          amy["reacted_at"] == "2026-10-08T10:00:00Z")
    check("finding context is attached when known",
          amy.get("finding") == {"severity": "warning", "check": "no_debug"})

    bob = next(e for e in lines if e["user"] == "bob")
    check("a thumbs down ingests as a negative event",
          bob["sentiment"] == "negative")

    # summary comment without finding context
    feedback.ingest_reactions("psf/requests", 7616, 999, "summary",
                              api_list[:1], log)
    lines = [json.loads(l) for l in open(log)]
    check("summary events ingest too", len(lines) == 3)
    check("summary events carry no finding when unknown",
          "finding" not in lines[-1])

# --- live-fetch wiring, stubbed ----------------------------------------------
_canned = [{"content": "hooray", "user": {"login": "ci-bot"},
            "created_at": "2026-10-09T07:00:00Z"}]


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_seen = []


def _fake_urlopen(req, timeout=None):
    _seen.append(req.full_url)
    return _FakeResp(_canned)


_orig = urllib.request.urlopen
urllib.request.urlopen = _fake_urlopen
try:
    got = feedback.fetch_reactions("o", "r", 42, "inline", token="tok")
    check("fetch parses the reactions list",
          got == _canned)
    check("inline reactions hit the pulls comments endpoint",
          _seen[-1].endswith("/repos/o/r/pulls/comments/42/reactions"))
    got = feedback.fetch_reactions("o", "r", 7, "summary", token="tok")
    check("summary reactions hit the issues comments endpoint",
          _seen[-1].endswith("/repos/o/r/issues/comments/7/reactions"))
finally:
    urllib.request.urlopen = _orig

# --- dry-run writes nothing ---------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    log = os.path.join(tmp, "feedback.jsonl")
    urllib.request.urlopen = _fake_urlopen
    try:
        events = feedback.ingest_live("o", "r", 1, 42, "inline", "tok",
                                      log, dry_run=True)
    finally:
        urllib.request.urlopen = _orig
    check("dry-run returns the events", len(events) == 1
          and events[0]["reaction"] == "hooray")
    check("dry-run writes no JSONL", not os.path.exists(log))

# --- posted-comment registry round trip ---------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    reg = os.path.join(tmp, "posted.jsonl")
    feedback.record_posted_comment(
        reg, {"repo": "o/r", "pr_number": 5, "kind": "summary",
              "comment_id": 111})
    feedback.record_posted_comment(
        reg, {"repo": "o/r", "pr_number": 5, "kind": "inline_review",
              "review_id": 222})
    feedback.record_posted_comment(
        reg, {"repo": "o/other", "pr_number": 5, "kind": "summary",
              "comment_id": 333})
    got = feedback.posted_comments_for_pr(reg, "o/r", 5)
    check("registry returns only this PR's entries", len(got) == 2)
    check("registry keeps the comment id",
          got[0]["comment_id"] == 111 and got[1]["review_id"] == 222)
    check("missing registry file reads as empty",
          feedback.posted_comments_for_pr(reg + ".nope", "o/r", 5) == [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

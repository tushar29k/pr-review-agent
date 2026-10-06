#!/usr/bin/env python3
"""Webhook receiver test: a fixture pull_request payload (real shape, built
from the live GitHub API) must yield a non-empty diff end-to-end through the
receiver, and signature verification must fail closed when a secret is set.

Run:  python3 test_webhook.py
"""

import hashlib
import hmac
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer.webhook import handle_event, verify_signature  # noqa: E402
from reviewer.github import (PRFetchError, post_comment,  # noqa: E402
                             post_review_comments)

# a tiny, stable public PR: psf/requests#7616 (a ruff pre-commit version bump)
_FIXTURE_REPO = "psf/requests"
_FIXTURE_PR = 7616

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def api_get(path, accept="application/vnd.github+json"):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"User-Agent": "pr-review-agent-demo", "Accept": accept})
    return urllib.request.urlopen(req, timeout=20).read()


print("== signature verification ==")
secret = "test-secret"
body = b'{"action":"opened"}'
good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
check("valid signature passes", verify_signature(body, good, secret))
check("tampered payload rejected", not verify_signature(b'{"action":"closed"}', good, secret))
check("missing header rejected when secret set", not verify_signature(body, None, secret))
check("wrong secret rejected", not verify_signature(body, good, "other-secret"))
check("unset secret warns and skips verify",
      verify_signature(body, "sha256=garbage", None))

print("== fixture pull_request payload (real shape, from the live API) ==")
pr = json.loads(api_get(f"/repos/{_FIXTURE_REPO}/pulls/{_FIXTURE_PR}"))
# github's webhook body wraps the pr under "pull_request" next to "repository"
payload = {"action": "opened",
           "pull_request": pr,
           "repository": pr["base"]["repo"]}
check("fixture has a real pr number and repo full name",
      payload["pull_request"]["number"] == _FIXTURE_PR
      and payload["repository"]["full_name"] == _FIXTURE_REPO)

print("== end to end: pull_request(opened) -> diff ==")
result = handle_event("pull_request", "opened", payload)
check("event handled", result["handled"] is True)
check("repo and number came through",
      result.get("repo") == _FIXTURE_REPO and result.get("number") == _FIXTURE_PR)
diff = result.get("diff") or ""
check("diff is a non-empty string",
      isinstance(diff, str) and len(diff.strip()) > 0)
print(f"  (diff: {len(diff)} chars)")

sync_payload = {**payload, "action": "synchronize"}
result = handle_event("pull_request", "synchronize", sync_payload)
check("synchronize also yields a diff",
      result["handled"] is True and len((result.get("diff") or "").strip()) > 0)

print("== ignored events ==")
r = handle_event("pull_request", "closed", payload)
check("pull_request/closed is ignored, not an error", r["handled"] is False)
r = handle_event("issues", "opened", payload)
check("issues events are ignored", r["handled"] is False)
r = handle_event("pull_request", "labeled", payload)
check("pull_request/labeled is ignored", r["handled"] is False)

print("== post_comment: dry-run against the test PR ==")
md = "## 🤖 PR review\n\nNo issues found."
res = post_comment("psf", "requests", _FIXTURE_PR, md, dry_run=True)
check("dry-run reports not posted", res["posted"] is False
      and res["dry_run"] is True)
check("dry-run targets the issues comments endpoint",
      res["url"] == "https://api.github.com/repos/psf/requests/issues/7616/comments")
check("dry-run payload carries the markdown body",
      res["payload"] == {"body": md})

# dry-run must never reach the network, even if something is misconfigured
def _boom(req, timeout=None):
    raise AssertionError("dry-run must not open a connection")
_orig = urllib.request.urlopen
urllib.request.urlopen = _boom
try:
    res = post_comment("psf", "requests", _FIXTURE_PR, md, dry_run=True)
    check("dry-run makes zero http calls", res["posted"] is False)
finally:
    urllib.request.urlopen = _orig

print("== post_comment: live payload shape ==")
captured = {}


class _FakeResp:
    def read(self):
        return b'{"id": 42, "html_url": "https://github.com/x/y/pull/1#issuecomment-42"}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(req, timeout=None):
    captured["method"] = req.get_method()
    captured["url"] = req.full_url
    captured["headers"] = dict(req.header_items())
    captured["body"] = json.loads(req.data.decode("utf-8"))
    return _FakeResp()


urllib.request.urlopen = _capture
try:
    live = post_comment("psf", "requests", _FIXTURE_PR, md,
                        dry_run=False, token="tok123")
finally:
    urllib.request.urlopen = _orig
check("live mode posts to the same comments endpoint",
      captured.get("method") == "POST"
      and captured.get("url") == "https://api.github.com/repos/psf/requests/issues/7616/comments")
check("live request sends the markdown as the body field",
      captured.get("body") == {"body": md})
check("live request carries the token as bearer auth",
      captured.get("headers", {}).get("Authorization") == "Bearer tok123")
check("live result reports the posted comment",
      live["posted"] is True and live["comment_id"] == 42)

try:
    post_comment("psf", "requests", _FIXTURE_PR, md,
                 dry_run=False, token=None)
    check("live posting without a token refuses", False)
except ValueError:
    check("live posting without a token refuses", True)

print("== map_findings_to_positions on the sample PR fixtures ==")
from reviewer.inline import map_findings_to_positions  # noqa: E402
from reviewer.reviewer import Reviewer  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ground = json.load(open(os.path.join(_HERE, "evals", "sample_prs.json")))
_mock = Reviewer()  # mock backend: deterministic planted findings, no keys
planted_checked = 0
for _sample in _ground["samples"]:
    _diff = open(os.path.join(_HERE, "evals", _sample["diff"])).read()
    _findings = _mock.review(_diff)
    _mapped = map_findings_to_positions(_diff, _findings)
    _pos = {(c["path"], c["line"]) for c in _mapped}
    # same ±1 rule the eval suite uses: a planted issue matches a mapped
    # comment when the file is the same and the line is within ±1
    for _e in _sample["expected"]:
        if _e["line"] is None:
            continue  # file-level findings have no anchor by design
        planted_checked += 1
        _hit = any(p[0] == _e["file"] and abs(p[1] - _e["line"]) <= 1
                   for p in _pos)
        check(f"{_sample['id']}: planted '{_e['check']}' "
              f"{_e['file']}:{_e['line']} anchors to a diff line", _hit)
    check(f"{_sample['id']}: every inline comment has path/line/side/body",
          all(c.get("path") and c.get("line") is not None
              and c.get("side") == "RIGHT" and c.get("body") for c in _mapped))

# a context (unchanged) line inside a hunk still anchors a comment —
# GitHub allows RIGHT-side comments on context lines
_ctx = map_findings_to_positions(
    open(os.path.join(_HERE, "evals", "sample_pr5.diff")).read(),
    [{"file": "api.py", "line": 2, "severity": "info",
      "check": "style", "message": "ctx line check"}])
check("context lines inside a hunk anchor with side=RIGHT",
      len(_ctx) == 1 and _ctx[0]["side"] == "RIGHT"
      and _ctx[0]["path"] == "api.py" and _ctx[0]["line"] == 2)

# unanchored findings are skipped, not forced onto the wire
_junk_diff = open(os.path.join(_HERE, "evals", "sample_pr.diff")).read()
_junk = [
    {"file": "payments.py", "line": 999, "severity": "warning",
     "check": "x", "message": "line past the end of the diff"},
    {"file": "not-a-file.py", "line": 1, "severity": "warning",
     "check": "x", "message": "file not in the diff"},
    {"file": "payments.py", "line": None, "severity": "info",
     "check": "missing_tests", "message": "file-level finding"},
    # pr1's README.md changed too, but no finding points at it here —
    # a finding on an untouched line of a touched file is still skipped
    {"file": "payments.py", "line": 100, "severity": "info",
     "check": "x", "message": "untouched line"},
]
check("findings on unchanged lines, unknown files, or no line are skipped",
      map_findings_to_positions(_junk_diff, _junk) == [])

# the comment body says where it came from at a glance
_body_mapped = map_findings_to_positions(
    _junk_diff,
    [{"file": "payments.py", "line": 4, "severity": "critical",
      "check": "secrets", "message": "possible key committed"}])
check("inline body carries severity, check, and message",
      len(_body_mapped) == 1
      and "**critical**" in _body_mapped[0]["body"]
      and "`secrets`" in _body_mapped[0]["body"]
      and "possible key committed" in _body_mapped[0]["body"])
print(f"  ({planted_checked} planted findings checked across "
      f"{len(_ground['samples'])} sample PRs)")

print("== post_review_comments: dry-run payload ==")
_sample_comments = [
    {"path": "payments.py", "line": 4, "side": "RIGHT",
     "body": "**critical** `secrets`\n\npossible key", "severity": "critical",
     "check": "secrets"},  # extras must not reach the wire
]
_res = post_review_comments("psf", "requests", _FIXTURE_PR, _sample_comments)
check("dry-run reports not posted", _res["posted"] is False
      and _res["dry_run"] is True)
check("dry-run targets the pulls reviews endpoint",
      _res["url"] == "https://api.github.com/repos/psf/requests/pulls/7616/reviews")
check("dry-run payload is event COMMENT with wire comments only",
      _res["payload"] == {
          "event": "COMMENT",
          "comments": [{"path": "payments.py", "line": 4, "side": "RIGHT",
                        "body": "**critical** `secrets`\n\npossible key"}]})

urllib.request.urlopen = _boom
try:
    _res = post_review_comments("psf", "requests", _FIXTURE_PR,
                                _sample_comments, dry_run=True)
    check("dry-run makes zero http calls", _res["posted"] is False)
finally:
    urllib.request.urlopen = _orig

_with_sha = post_review_comments("psf", "requests", _FIXTURE_PR,
                                 _sample_comments, commit_id="abc123")
check("commit_id rides along when given",
      _with_sha["payload"].get("commit_id") == "abc123")

print("== post_review_comments: live payload shape ==")
_captured = {}


def _capture2(req, timeout=None):
    _captured["method"] = req.get_method()
    _captured["url"] = req.full_url
    _captured["headers"] = dict(req.header_items())
    _captured["body"] = json.loads(req.data.decode("utf-8"))
    return _FakeResp()


urllib.request.urlopen = _capture2
try:
    _live = post_review_comments("psf", "requests", _FIXTURE_PR,
                                 _sample_comments, dry_run=False,
                                 token="tok123")
finally:
    urllib.request.urlopen = _orig
check("live mode posts to the reviews endpoint",
      _captured.get("method") == "POST"
      and _captured.get("url")
      == "https://api.github.com/repos/psf/requests/pulls/7616/reviews")
check("live request sends the review payload",
      _captured.get("body", {}).get("event") == "COMMENT"
      and _captured.get("body", {}).get("comments") == [
          {"path": "payments.py", "line": 4, "side": "RIGHT",
           "body": "**critical** `secrets`\n\npossible key"}])
check("live request carries the token as bearer auth",
      _captured.get("headers", {}).get("Authorization") == "Bearer tok123")
check("live result reports the posted review",
      _live["posted"] is True)

try:
    post_review_comments("psf", "requests", _FIXTURE_PR, _sample_comments,
                         dry_run=False, token=None)
    check("live posting without a token refuses", False)
except ValueError:
    check("live posting without a token refuses", True)

print("== post_check_run: critical findings block the check ==")
from reviewer.github import post_check_run  # noqa: E402

_crit_findings = [
    {"file": "payments.py", "line": 4, "severity": "critical",
     "check": "secrets", "message": "possible key committed"},
    {"file": "payments.py", "line": 9, "severity": "warning",
     "check": "debug_leftovers", "message": "print() left in"},
]
_cr = post_check_run("psf", "requests", "deadbeef", _crit_findings)
check("dry-run reports not posted", _cr["posted"] is False
      and _cr["dry_run"] is True)
check("a critical finding makes the conclusion failure",
      _cr["conclusion"] == "failure")
check("dry-run targets the check-runs endpoint",
      _cr["url"] == "https://api.github.com/repos/psf/requests/check-runs")
check("payload carries the head sha and completed status",
      _cr["payload"]["head_sha"] == "deadbeef"
      and _cr["payload"]["status"] == "completed")
check("payload names the check and the blocking conclusion",
      _cr["payload"]["name"] == "pr-review-agent"
      and _cr["payload"]["conclusion"] == "failure"
      and "1 critical" in _cr["payload"]["output"]["title"])
check("output summary carries the severity counts",
      "critical 1" in _cr["payload"]["output"]["summary"]
      and "warning 1" in _cr["payload"]["output"]["summary"])
check("output text names the blocking finding",
      "payments.py:4" in _cr["payload"]["output"]["text"]
      and "secrets" in _cr["payload"]["output"]["text"])

urllib.request.urlopen = _boom
try:
    _cr2 = post_check_run("psf", "requests", "deadbeef", _crit_findings,
                          dry_run=True)
    check("dry-run makes zero http calls", _cr2["posted"] is False)
finally:
    urllib.request.urlopen = _orig

_clean = post_check_run(
    "psf", "requests", "deadbeef",
    [{"file": "x.py", "line": 2, "severity": "warning",
      "check": "style", "message": "nit"}])
check("warnings alone don't block: conclusion is success",
      _clean["conclusion"] == "success")
check("clean output title says no critical findings",
      "no critical findings" in _clean["payload"]["output"]["title"])

_clear = post_check_run("psf", "requests", "deadbeef", [])
check("zero findings also passes", _clear["conclusion"] == "success")

print("== post_check_run: live payload shape ==")
_captured3 = {}


def _capture3(req, timeout=None):
    _captured3["method"] = req.get_method()
    _captured3["url"] = req.full_url
    _captured3["headers"] = dict(req.header_items())
    _captured3["body"] = json.loads(req.data.decode("utf-8"))
    return _FakeResp()


urllib.request.urlopen = _capture3
try:
    _live3 = post_check_run("psf", "requests", "deadbeef", _crit_findings,
                            dry_run=False, token="tok123")
finally:
    urllib.request.urlopen = _orig
check("live mode posts to the check-runs endpoint",
      _captured3.get("method") == "POST"
      and _captured3.get("url")
      == "https://api.github.com/repos/psf/requests/check-runs")
check("live request sends the blocking conclusion",
      _captured3.get("body", {}).get("conclusion") == "failure"
      and _captured3.get("body", {}).get("head_sha") == "deadbeef"
      and _captured3.get("body", {}).get("output", {}).get("summary"))
check("live request carries the token as bearer auth",
      _captured3.get("headers", {}).get("Authorization") == "Bearer tok123")
check("live result reports the check run conclusion",
      _live3["posted"] is True and _live3["conclusion"] == "failure")

try:
    post_check_run("psf", "requests", "deadbeef", _crit_findings,
                   dry_run=False, token=None)
    check("live posting without a token refuses", False)
except ValueError:
    check("live posting without a token refuses", True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

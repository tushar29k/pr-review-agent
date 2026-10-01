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
from reviewer.github import PRFetchError, post_comment  # noqa: E402

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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

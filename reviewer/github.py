"""Fetch a PR's diff straight from GitHub so people can review any link.

Stdlib only on purpose — no new dependencies for a demo service.
Unauthenticated GitHub API calls are rate-limited (~60/hr per IP), so
errors are translated into plain-language messages instead of raw codes.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

_API = "https://api.github.com"
_UA = {"User-Agent": "pr-review-agent-demo"}
_TIMEOUT = 15

# github.com/owner/repo/pull/123  — tolerate /pulls/ too, some folks type it
_PR_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/"
    r"(?:pull|pulls)/(?P<number>\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)


class PRFetchError(Exception):
    """Something went wrong talking to GitHub. .detail is human-readable."""


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Split a GitHub PR link into (owner, repo, number)."""
    m = _PR_RE.match((url or "").strip().rstrip("/"))
    if not m:
        raise ValueError(
            "That doesn't look like a GitHub PR link — "
            "expected https://github.com/owner/repo/pull/123")
    return m.group("owner"), m.group("repo"), int(m.group("number"))


def _get(path: str, accept: str) -> tuple[bytes, int]:
    # returns raw body + status; raises PRFetchError with a friendly detail
    req = urllib.request.Request(
        _API + path, headers={**_UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PRFetchError(
                "PR not found — private repo or a typo in the link?") from exc
        if exc.code in (403, 429):
            raise PRFetchError(
                "GitHub rate limit hit — wait a few minutes and retry") from exc
        raise PRFetchError(
            f"GitHub answered with {exc.code} — try again in a bit") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PRFetchError("Couldn't reach GitHub — check your connection") from exc


def fetch_pr_diff(owner: str, repo: str, number: int) -> tuple[str, dict]:
    """Returns (diff_text, meta). meta has the bits the UI header needs."""
    meta_path = f"/repos/{owner}/{repo}/pulls/{number}"
    body, _ = _get(meta_path, "application/vnd.github+json")
    try:
        pr = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PRFetchError("GitHub sent back something unexpected") from exc

    user = pr.get("user") or {}
    meta = {
        "title": pr.get("title") or "",
        "repo": f"{owner}/{repo}",
        "number": number,
        "url": pr.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{number}",
        "author": user.get("login") or "",
        "author_avatar": user.get("avatar_url") or "",
        "additions": pr.get("additions") or 0,
        "deletions": pr.get("deletions") or 0,
        "changed_files": pr.get("changed_files") or 0,
        "state": pr.get("state") or "",
    }

    diff, _ = _get(meta_path, "application/vnd.github.v3.diff")
    text = diff.decode("utf-8", errors="replace")
    if not text.strip():
        raise PRFetchError("That PR has no diff to review — maybe it's empty?")
    return text, meta

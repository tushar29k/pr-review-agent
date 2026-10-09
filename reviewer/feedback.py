"""Feedback capture: 👍/👎 reactions on review comments -> JSONL.

Why this exists: reactions are the cheapest signal we get about whether a
review comment was useful — a 👍 on an inline comment means the finding
landed, a 👎 means noise. This module turns those reactions into a JSONL
event log that tomorrow's noise-stats work can aggregate per check.

GitHub sends NO webhook events for reactions, so this is a polling path:
for comments we posted live, we GET the reactions on demand and ingest
them. stdlib only, same as reviewer/github.py. Reaction contents follow
the GitHub API vocabulary: +1, -1, laugh, confused, heart, hooray, rocket,
eyes.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from reviewer.github import PRFetchError

_API = "https://api.github.com"
_UA = {"User-Agent": "pr-review-agent-demo"}
_TIMEOUT = 15

# reaction -> how we read it: thumbs up and its celebratory cousins count as
# "this comment was useful"; -1 is the only explicit "this was noise"
_SENTIMENTS = {
    "+1": "positive",
    "hooray": "positive",
    "rocket": "positive",
    "heart": "positive",
    "laugh": "positive",
    "eyes": "positive",   # eyes ≈ "looking into it", a soft positive
    "-1": "negative",
    "confused": "neutral",  # confused means unclear, not necessarily wrong
}

# inline bodies are "**severity** `check`\n\nmessage" — the check name lets
# tomorrow's per-check noise stats know which check a reaction belongs to
_CHECK_RE = re.compile(r"^\*\*(\w+)\*\*\s+`([^`]+)`")


def sentiment_for(content: str) -> str:
    """Map one GitHub reaction content to positive/negative/neutral."""
    return _SENTIMENTS.get((content or "").strip(), "neutral")


def normalize_reactions(reactions: list[dict]) -> list[dict]:
    """Dedupe a reactions API list to one entry per (user, content).

    The API returns one row per reaction, and a user can only have one of
    each content per comment — dedupe defensively anyway (retries, mocks)
    and keep the earliest created_at so re-runs are stable.
    """
    seen: dict[tuple[str, str], dict] = {}
    for r in reactions or []:
        user = ((r.get("user") or {}).get("login") or "").strip()
        content = (r.get("content") or "").strip()
        if not user or not content:
            continue  # malformed rows carry no signal
        key = (user, content)
        created = r.get("created_at") or ""
        if key not in seen or created < seen[key]["created_at"]:
            seen[key] = {"user": user, "content": content,
                         "created_at": created}
    return sorted(seen.values(),
                  key=lambda r: (r["created_at"], r["user"], r["content"]))


def finding_from_inline_body(body: str) -> dict | None:
    """Pull {severity, check} out of an inline comment's header, if ours.

    Our inline bodies start with "**severity** `check`"; foreign comments
    just return None and the event carries no finding context.
    """
    m = _CHECK_RE.match((body or "").strip())
    if not m:
        return None
    return {"severity": m.group(1), "check": m.group(2)}


def build_event(repo: str, pr_number: int, comment_id: int,
                comment_kind: str, user: str, content: str,
                created_at: str | None,
                finding: dict | None = None) -> dict:
    """One JSON-serializable reaction event for the feedback log."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "pr_number": pr_number,
        "comment_id": comment_id,
        # summary (the markdown review) or inline (a line-level finding)
        "comment_kind": comment_kind,
        "reaction": content,
        "sentiment": sentiment_for(content),
        "user": user,
        "reacted_at": created_at or "",
    }
    if finding:
        event["finding"] = finding  # {"severity", "check"} when known
    return event


def append_event(log_path: str, event: dict) -> None:
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _get(path: str, token: str) -> list | dict:
    # reactions endpoints need auth — anonymous calls 404 on private repos
    # and burn the 60/hr unauthenticated budget on public ones
    headers = {**_UA, "Accept": "application/vnd.github+json",
               "Authorization": f"Bearer {token}"}
    req = urllib.request.Request(_API + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise PRFetchError(
            f"GitHub answered with {exc.code} while reading reactions") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PRFetchError(
            "Couldn't reach GitHub — check your connection") from exc


def fetch_reactions(owner: str, repo: str, comment_id: int,
                    comment_kind: str, token: str) -> list[dict]:
    """GET the raw reactions on one comment we posted.

    comment_kind "summary" = the issue comment holding the markdown review;
    "inline" = a PR review comment from the line-level review. token is
    required — GitHub won't serve reactions to anonymous callers reliably.
    """
    if comment_kind == "inline":
        path = f"/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions"
    else:
        path = f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
    return _get(path, token)  # type: ignore[return-value]


def fetch_review_comments(owner: str, repo: str, number: int,
                          review_id: int, token: str) -> list[dict]:
    """List the inline comments attached to one posted review.

    Live review posts only return the review id, not per-comment ids — this
    resolves them so ingest can poll each comment's reactions.
    """
    path = (f"/repos/{owner}/{repo}/pulls/{number}/reviews/"
            f"{review_id}/comments")
    return _get(path, token)  # type: ignore[return-value]


def ingest_reactions(repo: str, pr_number: int, comment_id: int,
                     comment_kind: str, reactions: list[dict],
                     log_path: str,
                     finding: dict | None = None) -> list[dict]:
    """Turn a reactions API list into JSONL events. The offline core.

    Pure except for the file append: given what GitHub would return for a
    comment, normalize, build, and append one event per reaction. Returns
    the events (handy for dry-run previews and tests).
    """
    events = [build_event(repo, pr_number, comment_id, comment_kind,
                          r["user"], r["content"], r["created_at"],
                          finding=finding)
              for r in normalize_reactions(reactions)]
    for event in events:
        append_event(log_path, event)
    return events


def ingest_live(owner: str, repo: str, pr_number: int, comment_id: int,
                comment_kind: str, token: str, log_path: str,
                dry_run: bool = True,
                finding: dict | None = None) -> list[dict]:
    """Fetch a comment's reactions and ingest them.

    dry_run (the default) builds the events without writing anything — the
    same convention as the comment-posting path. Live mode needs a token
    and refuses to write nothing-by-accident: it writes only what it fetched.
    """
    reactions = fetch_reactions(owner, repo, comment_id, comment_kind, token)
    events = [build_event(f"{owner}/{repo}", pr_number, comment_id,
                          comment_kind, r["user"], r["content"],
                          r["created_at"], finding=finding)
              for r in normalize_reactions(reactions)]
    if not dry_run:
        for event in events:
            append_event(log_path, event)
    return events


def record_posted_comment(registry_path: str, entry: dict) -> None:
    """Remember a comment/review we posted live so ingest can find it later.

    entry is {"repo", "pr_number", "kind"} where kind is "summary" (with
    comment_id) or "inline_review" (with review_id). Called only on live
    posts — dry-runs post nothing, so there's nothing to poll.
    """
    rec = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    append_event(registry_path, rec)


def posted_comments_for_pr(registry_path: str, repo: str,
                           pr_number: int) -> list[dict]:
    """All registry entries for one PR, oldest first. Missing file = []."""
    out = []
    try:
        with open(registry_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("repo") == repo and rec.get("pr_number") == pr_number:
                    out.append(rec)
    except (OSError, json.JSONDecodeError):
        pass  # no registry yet — nothing posted live on this box
    return out

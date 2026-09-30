"""GitHub webhook receiver: pull_request events -> fetch the PR diff.

stdlib only, same as reviewer/github.py. Dry-run stage: we fetch and return
the diff but never post anything back to GitHub (posting is a later item).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from reviewer.github import fetch_pr_diff

log = logging.getLogger("pr-review-agent.webhook")

# a fresh review is only worth it on open and on new pushes
_REVIEW_ACTIONS = {"opened", "synchronize"}


def verify_signature(payload: bytes, header: str | None, secret: str | None) -> bool:
    # no secret configured -> nothing to verify against; say so and allow
    if not secret:
        log.warning("WEBHOOK_SECRET unset — skipping signature verification")
        return True
    # secret set -> fail closed on anything that isn't a real sha256= digest
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


def handle_event(event: str, action: str | None, payload: dict,
                 token: str | None = None) -> dict:
    """Route one webhook delivery. Returns a small result dict; the diff lives
    in result["diff"] when handled. Anything we don't care about (non-PR
    events, closed/labeled actions) is reported as ignored, not an error."""
    if event != "pull_request":
        return {"handled": False, "reason": f"ignoring event {event!r}"}
    if action not in _REVIEW_ACTIONS:
        return {"handled": False,
                "reason": f"ignoring pull_request action {action!r}"}
    pr = payload.get("pull_request") or {}
    repo = (payload.get("repository") or {}).get("full_name") or ""
    number = pr.get("number")
    if "/" not in repo or not isinstance(number, int):
        raise ValueError(
            "payload is missing repository.full_name or pull_request.number")
    owner, name = repo.split("/", 1)
    # token from the app install when we have one; public PRs fetch fine without
    diff, meta = fetch_pr_diff(owner, name, number, token=token)
    return {"handled": True, "repo": repo, "number": number,
            "diff": diff, "meta": meta}

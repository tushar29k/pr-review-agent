"""HTTP service: POST a diff, get back a review comment.

Run:  uvicorn service:app --port 8000
Try:   curl -X POST localhost:8000/review \
         -H 'content-type: application/json' \
         -d '{"diff": "<unified diff text>"}'
"""

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import json
import os

from reviewer.comments import findings_to_markdown
from reviewer.feedback import (fetch_review_comments,
                               finding_from_inline_body, ingest_live,
                               posted_comments_for_pr, record_posted_comment)
from reviewer.github import (PRFetchError, fetch_pr_diff, fetch_repo_config,
                             parse_pr_url, post_check_run, post_comment,
                             post_review_comments)
from reviewer.inline import map_findings_to_positions
from reviewer.prompts import current_version
from reviewer.repo_config import RepoConfig, parse_config_text
from reviewer.reviewer import Reviewer, make_backend
from reviewer.webhook import handle_event, verify_signature

app = FastAPI(title="pr-review-agent")
# REVIEWER_BACKEND=openai on the host enables the model backend; mock otherwise
_reviewer = Reviewer(make_backend())  # one shared instance; backends should be thread-safe
# REVIEW_LOG_PATH set on the host turns on per-review cost/latency JSONL logging
_log_path = os.environ.get("REVIEW_LOG_PATH")
# WEBHOOK_SECRET set on the host turns on delivery signature verification
_webhook_secret = os.environ.get("WEBHOOK_SECRET")
# GITHUB_TOKEN set on the host (app install token / PAT) raises the api rate
# limit and unlocks private repos for webhook diff fetches
_github_token = os.environ.get("GITHUB_TOKEN")
# WEBHOOK_DRY_RUN=0 turns live comment posting on; default stays dry-run so
# no webhook delivery can ever surprise-post on someone's PR
_webhook_dry_run = os.environ.get("WEBHOOK_DRY_RUN", "1").lower() not in (
    "0", "false", "no")
# feedback capture: FEEDBACK_LOG_PATH is where 👍/👎 reaction events land;
# the registry remembers comments/reviews posted live so the ingest
# endpoint can find them again (GitHub sends no webhooks for reactions)
_feedback_log_path = os.environ.get("FEEDBACK_LOG_PATH", "feedback.jsonl")
_feedback_registry_path = os.environ.get("FEEDBACK_REGISTRY_PATH",
                                         "feedback_comments.jsonl")
# latest webhook result (dry-run stage: fetched diffs, no comments posted yet)
_last_webhook: dict = {}


class ReviewRequest(BaseModel):
    diff: str


class ReviewResponse(BaseModel):
    markdown: str
    finding_count: int
    has_critical: bool
    findings: list[dict] = []  # the raw findings, so the UI can style each one
    skipped_files: list[str] = []  # vendored/generated paths the review skipped


class PRReviewRequest(BaseModel):
    pr_url: str


class PRMeta(BaseModel):
    title: str
    repo: str
    number: int
    url: str
    author: str
    author_avatar: str
    additions: int
    deletions: int
    changed_files: int
    state: str


class PRReviewResponse(ReviewResponse):
    pr: PRMeta


def _reviewer_for_repo_config(owner: str, repo: str,
                              head_sha: str | None) -> Reviewer:
    # the PR's own .pr-review.yaml tunes the review; missing file or any
    # failure just means the built-in defaults. shares the service backend.
    cfg_text = fetch_repo_config(owner, repo, ref=head_sha)
    config = parse_config_text(cfg_text) if cfg_text and cfg_text.strip() \
        else RepoConfig()
    return Reviewer(_reviewer.backend, config=config)


@app.post("/review", response_model=ReviewResponse)
def review(req: ReviewRequest) -> ReviewResponse:
    findings, skipped = _reviewer.review_and_log(req.diff, log_path=_log_path)
    return ReviewResponse(
        markdown=findings_to_markdown(findings, skipped),
        finding_count=len(findings),
        has_critical=any(f["severity"] == "critical" for f in findings),
        findings=findings,
        skipped_files=skipped,
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/info")
def info() -> dict:
    # honest backend report for the demo badge: real model or offline mock
    backend = _reviewer.backend
    client = getattr(backend, "_client", None)
    real = type(backend).__name__ == "FreeBackend" and client is not None
    return {"real_llm": real,
            "provider": client.provider if real else None,
            "model": client.model if real else None,
            "last_error": getattr(backend, "last_error", None),
            "backend": type(backend).__name__,
            "prompt_version": current_version()}


@app.post("/review-pr", response_model=PRReviewResponse)
def review_pr(req: PRReviewRequest) -> PRReviewResponse:
    """Review a live GitHub PR straight from its link.

    Fetches the diff from the GitHub API, then runs the same pipeline as
    /review. Two failure modes: a malformed link (422) and GitHub being
    unreachable / the PR not existing (friendly message from PRFetchError).
    """
    try:
        owner, repo, number = parse_pr_url(req.pr_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        diff_text, meta = fetch_pr_diff(owner, repo, number)
    except PRFetchError as exc:
        # 404 vs 502: not found is a client-ish problem, the rest is ours
        status = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    head_sha = meta.pop("head_sha", None)  # fetch helper detail, not a PRMeta field

    findings, skipped = _reviewer_for_repo_config(
        owner, repo, head_sha).review_and_log(diff_text, log_path=_log_path)
    return PRReviewResponse(
        markdown=findings_to_markdown(findings, skipped),
        finding_count=len(findings),
        has_critical=any(f["severity"] == "critical" for f in findings),
        findings=findings,
        skipped_files=skipped,
        pr=PRMeta(**meta),
    )


class FeedbackIngestRequest(BaseModel):
    owner: str
    repo: str
    pr_number: int
    # a specific comment to ingest, or None to sweep everything we posted
    # live on this PR (from the posted-comment registry)
    comment_id: int | None = None
    comment_kind: str = "summary"  # summary (markdown review) or inline
    dry_run: bool = True  # pass dry_run=False to actually write the JSONL


@app.post("/feedback/ingest")
def ingest_feedback(req: FeedbackIngestRequest) -> dict:
    """Ingest 👍/👎 reactions on comments we posted into the feedback JSONL.

    GitHub sends no webhook events for reactions, so this is the polling
    path: given a comment (or every comment we posted live on a PR), fetch
    its reactions and append one event per reaction to FEEDBACK_LOG_PATH.
    dry_run defaults on, mirroring the posting path — pass dry_run=False to
    actually write. Always needs GITHUB_TOKEN: the reactions endpoints don't
    serve anonymous callers reliably.
    """
    if not _github_token:
        raise HTTPException(status_code=422,
                            detail="GITHUB_TOKEN is needed to read reactions")
    dry_run = req.dry_run
    repo = f"{req.owner}/{req.repo}"
    written = not dry_run
    all_events: list[dict] = []

    def _one(comment_id: int, kind: str,
             finding: dict | None = None) -> list[dict]:
        try:
            return ingest_live(req.owner, req.repo, req.pr_number,
                               comment_id, kind, _github_token,
                               _feedback_log_path,
                               dry_run=dry_run, finding=finding)
        except PRFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if req.comment_id is not None:
        all_events = _one(req.comment_id, req.comment_kind)
    else:
        # sweep the registry: every comment/review we posted live on this PR
        for rec in posted_comments_for_pr(_feedback_registry_path,
                                          repo, req.pr_number):
            kind = rec.get("kind")
            if kind == "summary" and rec.get("comment_id"):
                all_events += _one(rec["comment_id"], "summary")
            elif kind == "inline_review" and rec.get("review_id"):
                try:
                    comments = fetch_review_comments(
                        req.owner, req.repo, req.pr_number,
                        rec["review_id"], _github_token)
                except PRFetchError as exc:
                    raise HTTPException(status_code=502,
                                        detail=str(exc)) from exc
                for c in comments:
                    # our inline bodies carry the check name in the header —
                    # that's the per-check signal the noise stats will use
                    all_events += _one(c["id"], "inline",
                                       finding_from_inline_body(
                                           c.get("body") or ""))
    reply = {"dry_run": dry_run, "events": all_events,
             "event_count": len(all_events)}
    if written:
        reply["log_path"] = _feedback_log_path
    return reply

# -- demo ui -----------------------------------------------------------------
# open / in a browser to click through the api instead of curling it.
import os as _os
from fastapi.responses import FileResponse as _FileResponse


@app.post("/webhooks/github")
async def github_webhook(request: Request) -> dict:
    """GitHub App webhook receiver.

    Verifies X-Hub-Signature-256 against WEBHOOK_SECRET when set (fails closed;
    skips with a warning when unset), then on pull_request opened/synchronize
    fetches the PR diff, reviews it, and posts the markdown review as a PR
    comment. Dry-run by default (nothing posted); set WEBHOOK_DRY_RUN=0 with
    a GITHUB_TOKEN to post live.
    """
    body = await request.body()  # signature needs the exact bytes
    if not verify_signature(body, request.headers.get("x-hub-signature-256"),
                            _webhook_secret):
        raise HTTPException(status_code=401, detail="bad webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="not a JSON payload") from exc
    try:
        result = handle_event(request.headers.get("x-github-event") or "",
                              payload.get("action"), payload,
                              token=_github_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PRFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # keep the diff around for the dry-run stage; shrink what we send back
    global _last_webhook
    _last_webhook = result
    reply = {k: v for k, v in result.items() if k != "diff"}
    if result.get("handled"):
        diff = result["diff"]
        reply["diff_chars"] = len(diff)
        reply["diff_preview"] = diff[:500]
        # same review pipeline as /review, then post it back on the PR
        owner, name = result["repo"].split("/", 1)
        cfg = result.get("repo_config") or RepoConfig()
        reviewer = Reviewer(_reviewer.backend, config=cfg)
        findings, skipped = reviewer.review_and_log(diff, log_path=_log_path)
        markdown = findings_to_markdown(findings, skipped)
        try:
            posted = post_comment(owner, name, result["number"], markdown,
                                  dry_run=_webhook_dry_run, token=_github_token)
        except PRFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        reply["review"] = {"finding_count": len(findings),
                           "markdown_preview": markdown[:300]}
        # live posts are remembered so /feedback/ingest can poll their
        # reactions later (GitHub never sends webhooks for reactions)
        if posted.get("posted") and posted.get("comment_id"):
            record_posted_comment(
                _feedback_registry_path,
                {"repo": result["repo"], "pr_number": result["number"],
                 "kind": "summary", "comment_id": posted["comment_id"]})
        reply["comment"] = {k: v for k, v in posted.items()
                            if k != "payload"}
        if posted.get("dry_run"):
            reply["comment"]["payload"] = posted["payload"]
        # inline line-level comments alongside the summary: findings map to
        # diff positions; the ones on unchanged lines have no anchor and
        # are skipped by the mapper, so they only appear in the summary
        inline = map_findings_to_positions(diff, findings)
        if inline:
            inline_posted = post_review_comments(
                owner, name, result["number"], inline,
                dry_run=_webhook_dry_run, token=_github_token)
            if inline_posted.get("posted") and inline_posted.get("review_id"):
                record_posted_comment(
                    _feedback_registry_path,
                    {"repo": result["repo"], "pr_number": result["number"],
                     "kind": "inline_review",
                     "review_id": inline_posted["review_id"]})
            reply["inline_comments"] = {
                "count": len(inline),
                "positions": [(c["path"], c["line"]) for c in inline],
                "posted": inline_posted["posted"],
                "dry_run": inline_posted.get("dry_run"),
            }
        # check-run status on the PR head: a critical finding blocks the
        # check (the red X on the PR); a clean review passes it. dry-run
        # default, like the comments — nothing reaches GitHub without a token
        head_sha = (result.get("meta") or {}).get("head_sha")
        if head_sha:
            try:
                check_run = post_check_run(owner, name, head_sha, findings,
                                           dry_run=_webhook_dry_run,
                                           token=_github_token)
            except PRFetchError as exc:
                raise HTTPException(status_code=502,
                                    detail=str(exc)) from exc
            reply["check_run"] = {"conclusion": check_run["conclusion"],
                                  "posted": check_run["posted"],
                                  "dry_run": check_run.get("dry_run")}
            if check_run.get("dry_run"):
                reply["check_run"]["payload"] = check_run["payload"]
    return reply

_UI_INDEX = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ui", "index.html")


@app.get("/", include_in_schema=False)
def _demo_ui():
    return _FileResponse(_UI_INDEX)


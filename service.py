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
from reviewer.github import PRFetchError, fetch_pr_diff, parse_pr_url
from reviewer.prompts import current_version
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
# latest webhook result (dry-run stage: fetched diffs, no comments posted yet)
_last_webhook: dict = {}


class ReviewRequest(BaseModel):
    diff: str


class ReviewResponse(BaseModel):
    markdown: str
    finding_count: int
    has_critical: bool
    findings: list[dict] = []  # the raw findings, so the UI can style each one


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


@app.post("/review", response_model=ReviewResponse)
def review(req: ReviewRequest) -> ReviewResponse:
    findings = _reviewer.review_and_log(req.diff, log_path=_log_path)
    return ReviewResponse(
        markdown=findings_to_markdown(findings),
        finding_count=len(findings),
        has_critical=any(f["severity"] == "critical" for f in findings),
        findings=findings,
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

    findings = _reviewer.review_and_log(diff_text, log_path=_log_path)
    return PRReviewResponse(
        markdown=findings_to_markdown(findings),
        finding_count=len(findings),
        has_critical=any(f["severity"] == "critical" for f in findings),
        findings=findings,
        pr=PRMeta(**meta),
    )

# -- demo ui -----------------------------------------------------------------
# open / in a browser to click through the api instead of curling it.
import os as _os
from fastapi.responses import FileResponse as _FileResponse


@app.post("/webhooks/github")
async def github_webhook(request: Request) -> dict:
    """GitHub App webhook receiver (dry run).

    Verifies X-Hub-Signature-256 against WEBHOOK_SECRET when set (fails closed;
    skips with a warning when unset), then on pull_request opened/synchronize
    fetches the PR diff via the GitHub API. Fetches the diff but posts nothing
    back — posting is a later roadmap item.
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
    return reply

_UI_INDEX = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ui", "index.html")


@app.get("/", include_in_schema=False)
def _demo_ui():
    return _FileResponse(_UI_INDEX)


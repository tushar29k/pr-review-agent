"""HTTP service: POST a diff, get back a review comment.

Run:  uvicorn service:app --port 8000
Try:   curl -X POST localhost:8000/review \
         -H 'content-type: application/json' \
         -d '{"diff": "<unified diff text>"}'
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from reviewer.comments import findings_to_markdown
from reviewer.github import PRFetchError, fetch_pr_diff, parse_pr_url
from reviewer.reviewer import Reviewer, make_backend

app = FastAPI(title="pr-review-agent")
# REVIEWER_BACKEND=openai on the host enables the model backend; mock otherwise
_reviewer = Reviewer(make_backend())  # one shared instance; backends should be thread-safe


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
    findings = _reviewer.review(req.diff)
    return ReviewResponse(
        markdown=findings_to_markdown(findings),
        finding_count=len(findings),
        has_critical=any(f["severity"] == "critical" for f in findings),
        findings=findings,
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


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

    findings = _reviewer.review(diff_text)
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

_UI_INDEX = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ui", "index.html")


@app.get("/", include_in_schema=False)
def _demo_ui():
    return _FileResponse(_UI_INDEX)


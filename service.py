"""HTTP service: POST a diff, get back a review comment.

Run:  uvicorn service:app --port 8000
Try:   curl -X POST localhost:8000/review \
         -H 'content-type: application/json' \
         -d '{"diff": "<unified diff text>"}'
"""

from fastapi import FastAPI
from pydantic import BaseModel

from reviewer.comments import findings_to_markdown
from reviewer.reviewer import Reviewer

app = FastAPI(title="pr-review-agent")
_reviewer = Reviewer()  # one shared instance; backends should be thread-safe


class ReviewRequest(BaseModel):
    diff: str


class ReviewResponse(BaseModel):
    markdown: str
    finding_count: int
    has_critical: bool


@app.post("/review", response_model=ReviewResponse)
def review(req: ReviewRequest) -> ReviewResponse:
    findings = _reviewer.review(req.diff)
    return ReviewResponse(
        markdown=findings_to_markdown(findings),
        finding_count=len(findings),
        has_critical=any(f["severity"] == "critical" for f in findings),
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}

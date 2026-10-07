#!/usr/bin/env python3
"""Evals: replay 10 real public-repo PRs through the offline reviewer.

Picks 2 recently merged, small-to-medium PRs from each of 5 well-known
repos, fetches their real diffs from GitHub (PR meta via the gh CLI with
stored auth, diff bytes via patch-diff), runs them
through the mock-backed Reviewer (the same offline path the eval suite
uses), and records findings per PR to pr_replay_results.md + .jsonl.

Run:  python evals/replay_prs.py
Re-run is fine — it overwrites both result files with a fresh run.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewer.checks import SOURCE_EXTS  # noqa: E402
from reviewer.diff_parser import parse_diff  # noqa: E402
from reviewer.reviewer import Reviewer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GH = os.environ.get("GH_BIN", "/home/hatch/workspace/skills/github/bin/gh")

# 2 PRs each from 5 repos the reviewer is meant to handle well — 10 total
REPOS = [
    ("psf", "requests"),
    ("pallets", "flask"),
    ("pytest-dev", "pytest"),
    ("encode", "httpx"),
    ("django", "django"),
]
PER_REPO = 2

# keep replay fast and the findings comparable: no one-line nits, no rewrites
MIN_ADDS, MAX_ADDS = 3, 250
MAX_FILES = 10


def gh_api(path: str) -> object:
    # the gh wrapper is `gh api METHOD PATH` (query strings go in PATH) and
    # prints {"status": code, "data": payload}; it authenticates for us
    proc = subprocess.run([GH, "api", "GET", path], capture_output=True,
                          text=True, timeout=60)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(proc.stderr.strip() or "gh api printed no JSON")
    if not 200 <= result.get("status", 0) < 300:
        raise RuntimeError(f"GitHub API status {result.get('status')}: "
                           f"{json.dumps(result.get('data'))[:200]}")
    return result["data"]


def pick_candidates(owner: str, repo: str, want: int) -> list[dict]:
    # the gh wrapper truncates responses past 200KB, and the PR list payload
    # is too fat for even a short page — so take numbers from the (small)
    # search-issues objects, then fetch each PR's meta individually.
    # busy repos (requests) can still blow the 200KB cap, so shrink
    # per_page until the search response fits
    search: dict | None = None
    for per_page in (30, 15, 8):
        data = gh_api("/search/issues?q=repo:{}/{}+is:pr+is:merged"
                      "+sort:updated-desc&per_page={}".format(owner, repo, per_page))
        if isinstance(data, dict) and "items" in data:
            search = data
            break
    if search is None:
        raise RuntimeError("search response truncated even at per_page=8")
    picks: list[dict] = []
    for item in search.get("items", []):
        if "pull_request" not in item:
            continue
        number = item["number"]
        pr = gh_api(f"/repos/{owner}/{repo}/pulls/{number}")
        adds, dels = pr.get("additions") or 0, pr.get("deletions") or 0
        files = pr.get("changed_files") or 0
        if MIN_ADDS <= adds <= MAX_ADDS and dels <= MAX_ADDS and files <= MAX_FILES:
            picks.append(pr)
        if len(picks) == want:
            break
    return picks


def fetch_diff(owner: str, repo: str, number: int) -> str:
    # patch-diff serves the same bytes the PR page's .diff link does —
    # the real diff, straight from GitHub, no auth needed
    url = (f"https://patch-diff.githubusercontent.com/raw/"
           f"{owner}/{repo}/pull/{number}.diff")
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "pr-review-agent-demo"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt == 1:
                raise
            time.sleep(3)  # one retry — patch-diff hiccups happen
    raise RuntimeError("unreachable")


def source_files(diff_text: str) -> list[str]:
    return [f.path for f in parse_diff(diff_text)
            if any(f.path.endswith(e) for e in SOURCE_EXTS)]


def review_pr(diff_text: str, reviewer: Reviewer) -> list[dict]:
    return reviewer.review(diff_text)


def spot_note(pr: dict, findings: list[dict], src: list[str]) -> str:
    # honest "notable misses" column: no ground truth here, so the note
    # flags the cases a human should eyeball, not claims we found bugs
    crits = [f for f in findings if f["severity"] == "critical"]
    if crits:
        return "critical finding on real code — check if it's true positive"
    if not findings and src:
        return "no findings on a code change (possible miss — spot-check by hand)"
    if not src:
        return "docs/config-only change; any findings are likely noise"
    infos = [f for f in findings if f["severity"] == "info"]
    if findings and len(infos) == len(findings):
        return "only low-severity findings — nothing actionable surfaced"
    return ""


def severity_counts(findings: list[dict]) -> dict[str, int]:
    counts = {"critical": 0, "warning": 0, "info": 0}
    for f in findings:
        if f["severity"] in counts:
            counts[f["severity"]] += 1
    return counts


def checks_fired(findings: list[dict]) -> list[str]:
    return sorted({f["check"] for f in findings})


def esc(cell: object) -> str:
    return str(cell).replace("|", "\\|").replace("\n", " ")


def write_results(rows: list[dict], run_date: str) -> None:
    lines = [
        "# PR replay results",
        "",
        f"Run: {run_date} — backend: mock (fully offline, same path as the "
        "eval suite). PRs are real, recently merged, fetched live via gh.",
        "",
        "No ground truth exists for these PRs — the \"notable misses\" "
        "column is a spot-check flag, not a claim of bugs found or missed.",
        "",
        "| PR | repo | diff (+/-) | findings (c/w/i) | checks fired | notable misses |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        c = r["counts"]
        checks = ", ".join(r["checks"]) or "—"
        lines.append(
            f"| [{r['number']}]({r['url']}) | {r['repo']} | "
            f"+{r['additions']}/-{r['deletions']} ({r['changed_files']} files) | "
            f"{c['critical']}/{c['warning']}/{c['info']} | "
            f"{esc(checks)} | {esc(r['note'])} |")
    lines += ["", f"Total: {len(rows)} PRs replayed, "
              f"{sum(r['total'] for r in rows)} findings recorded.", ""]
    with open(os.path.join(HERE, "pr_replay_results.md"), "w") as fh:
        fh.write("\n".join(lines))

    with open(os.path.join(HERE, "pr_replay_results.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def main() -> int:
    reviewer = Reviewer()  # mock backend — offline by construction
    rows: list[dict] = []
    problems: list[str] = []

    for owner, repo in REPOS:
        try:
            candidates = pick_candidates(owner, repo, PER_REPO)
        except Exception as exc:  # repo listing failed — note it, keep going
            problems.append(f"{owner}/{repo}: listing failed ({exc})")
            continue
        for pr in candidates:
            number = pr["number"]
            try:
                diff = fetch_diff(owner, repo, number)
                if not diff.strip():
                    problems.append(f"{owner}/{repo}#{number}: empty diff, skipped")
                    continue
                findings = review_pr(diff, reviewer)
                src = source_files(diff)
                rows.append({
                    "repo": f"{owner}/{repo}",
                    "number": number,
                    "url": pr.get("html_url")
                           or f"https://github.com/{owner}/{repo}/pull/{number}",
                    "title": pr.get("title") or "",
                    "additions": pr.get("additions") or 0,
                    "deletions": pr.get("deletions") or 0,
                    "changed_files": pr.get("changed_files") or 0,
                    "source_files_changed": len(src),
                    "counts": severity_counts(findings),
                    "total": len(findings),
                    "checks": checks_fired(findings),
                    "note": spot_note(pr, findings, src),
                    "findings": findings,
                })
                c = rows[-1]["counts"]
                print(f"ok  {owner}/{repo}#{number}: "
                      f"+{rows[-1]['additions']}/-{rows[-1]['deletions']} "
                      f"findings c{c['critical']}/w{c['warning']}/i{c['info']}")
            except Exception as exc:
                problems.append(f"{owner}/{repo}#{number}: {exc}")
                print(f"skip {owner}/{repo}#{number}: {exc}")

    run_date = datetime.date.today().isoformat()
    write_results(rows, run_date)
    print(f"\n{len(rows)} PRs replayed — results in "
          "evals/pr_replay_results.md + pr_replay_results.jsonl")
    for p in problems:
        print(f"note: {p}")
    if len(rows) < PER_REPO * len(REPOS):
        # short of 10: say so plainly, never pad with fakes
        print(f"WARNING: only {len(rows)} PRs replayed — "
              "results file reflects exactly what ran")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

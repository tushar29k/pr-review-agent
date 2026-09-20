# pr-review-agent

An automated code reviewer for pull requests: deterministic checks catch the non-negotiables (leaked secrets, debug leftovers), and a swappable reviewer backend adds judgement on top. Runs as a CLI or an HTTP service. Zero API keys needed to try it.

## The idea

Every team says "we review every PR", and every team has PRs that get a 👀 and a merge. The boring-but-important stuff — a secret pasted into a config, a `print` left in, a 900-line diff nobody actually read — is exactly what machines are good at catching, every single time, without getting tired.

This project is a reviewer with two layers:

1. **Deterministic checks** — regex and structural rules for things that are never a matter of opinion: secrets, debug statements, TODOs, binary files, oversized diffs, new code without tests.
2. **Reviewer backend** — a one-method interface where an LLM (or heuristics, for now) gives deeper feedback: swallowed exceptions, complexity smells, suspicious patterns.

The checks run first so the critical stuff is flagged even if the model call fails. That's the whole design philosophy: never let the clever layer be a single point of failure for the obvious layer.

## How it works

```
unified diff
    │
    ▼
diff_parser  →  FileDiff per file (added/removed lines with line numbers)
    │
    ▼
checks.run_all()  →  secrets, debug leftovers, TODOs, binaries,
                      diff size, missing tests          (deterministic)
    │
    ▼
backend.review_file()  →  judgement calls               (mock LLM for now)
    │
    ▼
comments.findings_to_markdown()  →  the review comment
```

Findings carry a severity (`critical` / `warning` / `info`), file, line, and a human-readable message, sorted so the merge-blockers come first.

## How to run

Prerequisites: Python 3.10+.

```bash
pip install -r requirements.txt

# 1. Review a diff from the command line
python cli.py --diff evals/sample_pr.diff
# exit code 1 if anything critical was found — handy as a CI gate

# 2. Run the evals (the sample PR has known issues planted in it)
python evals/run_eval.py
# expect: 5/5 expectations met

# 3. Start the HTTP service
uvicorn service:app --port 8000
curl -X POST localhost:8000/review \
  -H 'content-type: application/json' \
  -d '{"diff": "<paste a unified diff here>"}'
```

To review a real PR locally: `git diff main...HEAD > my.diff`, then `python cli.py --diff my.diff`.

## Project layout

```
reviewer/
  diff_parser.py   parse unified diffs into FileDiff structures
  checks.py        deterministic checks (secrets, debug, TODOs, size, tests)
  reviewer.py      Reviewer orchestrator + ReviewBackend interface + MockBackend
  comments.py      render findings as a markdown PR comment
cli.py             CLI: review a diff file, exit 1 on critical findings
service.py         FastAPI service: POST /review, GET /health
evals/
  sample_pr.diff   sample PR with planted issues (secret, print, bare except…)
  run_eval.py      asserts every planted issue is caught
ROADMAP.md         where this goes next
```

## Evals

`evals/run_eval.py` runs the reviewer over a sample PR with five deliberately planted problems and asserts each one is caught: the AWS key flagged critical, the debug `print`, the bare `except:`, the TODO surfaced, and the new-file-without-tests nudge. 5/5 passing means the pipeline works end to end. Add your own diffs to `evals/` as the check set grows.

## Honest notes

- The reviewer backend is a **mock** — it fires on a few obvious shapes (bare excepts, big single-file additions) so the pipeline runs offline. The `ReviewBackend` interface is one method; plugging in OpenAI/Anthropic/a local model is the obvious next step (see ROADMAP.md).
- Secret patterns are a starter set, not exhaustive — real secret scanning (Gitleaks, GitHub secret scanning) should still run in CI.
- This reviews the *diff*, not the full codebase, so it won't catch architectural issues or cross-file inconsistencies. That's what the LLM backend is for.

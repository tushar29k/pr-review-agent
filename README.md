# pr-review-agent

An automated code reviewer for pull requests: deterministic checks catch the non-negotiables (leaked secrets, debug leftovers), and a swappable reviewer backend adds judgement on top. Runs as a CLI or an HTTP service. Zero API keys needed to try it.

## Live demo

**[https://tushar29k-pr-review-agent.onrender.com](https://tushar29k-pr-review-agent.onrender.com)** — paste a GitHub PR link or a raw diff and get a styled review: PR header with author and diff stats, severity-colored finding cards, and one-click markdown copy.
> Hosted on Render's free tier — the first visit after a while can take ~30s while the instance wakes up.

## Review any GitHub PR

Two ways to review a live PR — no API key needed:

1. **Open the demo** and go to the **GitHub PR** tab. Paste any PR link (`https://github.com/owner/repo/pull/123`) and hit **Review PR** — the service pulls the diff from the GitHub API and reviews it.
2. **Find your own PRs**: in the same tab, enter your GitHub username and hit **Find PRs**. Your recent public PRs appear as a clickable list — click one and the review starts immediately.

Prefer curl? The endpoint is `POST /review-pr`:

```bash
curl -X POST localhost:8000/review-pr \
  -H 'content-type: application/json' \
  -d '{"pr_url": "https://github.com/owner/repo/pull/123"}'
# → { markdown, finding_count, has_critical, findings, pr: { title, repo, number,
#     url, author, author_avatar, additions, deletions, changed_files, state } }
```

Bad links get a 422 with a plain-language explanation; unreachable or private PRs get a friendly error instead of a stack trace. Unauthenticated GitHub calls are rate-limited (~60/hr per IP), so the errors say so honestly.

## GitHub webhook receiver (dry run)

Point a GitHub App's webhook at `POST /webhooks/github`. On `pull_request`
`opened`/`synchronize` events the service fetches the PR diff via the GitHub
API, reviews it, and posts the rendered markdown review as a PR comment via
`POST /repos/{owner}/{repo}/issues/{number}/comments`. Delivery signatures
(`X-Hub-Signature-256`) are verified against `WEBHOOK_SECRET` when set —
fail closed; when unset, verification is skipped with a warning. `GITHUB_TOKEN`
(app install token or PAT) raises the rate limit and unlocks private repos;
public PRs fetch fine without it. Dry-run by default: the reply shows exactly
what would be posted, and nothing goes to GitHub. Set `WEBHOOK_DRY_RUN=0`
(with `GITHUB_TOKEN`) to post live — the dry-run default means a webhook
delivery can never surprise-post on someone's PR.

Alongside the summary comment the receiver also builds inline review
comments: each finding is mapped to a `(path, line, side)` position inside
the diff (`reviewer/inline.py`), and the positions become a single
`POST /repos/{owner}/{repo}/pulls/{number}/reviews` review with
`event: "COMMENT"` — so the finding reads right next to its code. Findings
on unchanged lines (or with no line at all, like `missing_tests`) have no
diff anchor and are skipped; they only appear in the summary. Dry-run is
the default here too: nothing reaches GitHub without a token.

The receiver also reports the review as a check run on the PR's head
commit (`POST /repos/{owner}/{repo}/check-runs`): any critical finding
blocks the check with conclusion `failure` — the red X on the PR —
while warnings and infos never block (`success`). The output names the
blocking findings with their severity counts, so the PR author sees why
the check is red at a glance. Same dry-run default and token rule as
the comment posting.

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

## Severity calibration

Model backends report a `confidence` (0.0–1.0) per finding; `reviewer/config.py` maps it onto severity with one shared scale so every backend calibrates the same way:

| confidence | severity |
|---|---|
| ≥ 0.80 | critical |
| ≥ 0.50 | warning |
| < 0.50 | info |

Overrides without touching code: `SEVERITY_CRITICAL` and `SEVERITY_WARNING` env vars (clamped to 0–1, invalid values fall back to the defaults above). Deterministic checks bypass calibration — they're certain by design (`confidence: 1.0`), so a leaked secret is always critical no matter what a model thinks.

## Repo config: `.pr-review.yaml`

Drop a `.pr-review.yaml` in your repo root and reviews of that repo pick it up automatically — the CLI discovers it in the current directory (`--config` points at one explicitly), and the GitHub App reads it from the PR head, so a config added in the PR tunes its own review. Discovery order: `--config` flag → repo root → built-in defaults.

```yaml
enabled_checks:        # only these checks run; anything else is skipped
  - secrets
  - debug_leftovers
  - todos
  - binary
  - diff_size
  - missing_tests
  - llm                # the model pass (all backends)
severity_thresholds:   # remap model confidences; deterministic checks
  critical: 0.90       # keep their fixed severities regardless
  warning: 0.60
ignore:                # fnmatch patterns for vendored/generated files
  - vendor/*           # skipped files are named in the review output,
  - "*.min.js"         # never silently dropped; replaces the built-in
                       # defaults when present (an empty list disables
                       # ignoring). Files with generated-code markers
                       # (@generated, "DO NOT EDIT") are always skipped.
                       # CLI flag --ignore-pattern layers on top.
```

Example: a repo drowning in TODO noise sets `enabled_checks` without `todos`, or raises `warning:` to `0.70` so only confident model findings become warnings. Unknown check names and bad threshold values are ignored with a stderr note — a broken config never breaks a review.

## How to run

Prerequisites: Python 3.10+.

```bash
pip install -r requirements.txt

# 1. Review a diff from the command line
python cli.py --diff evals/sample_pr.diff
# exit code 1 if anything critical was found — handy as a CI gate

# 2. Run the evals (the sample PR has known issues planted in it,
#    plus URL-parsing and output-shape checks)
python evals/run_eval.py
# expect: 11/11 expectations met

# 3. Start the HTTP service
uvicorn service:app --port 8000
curl -X POST localhost:8000/review \
  -H 'content-type: application/json' \
  -d '{"diff": "<paste a unified diff here>"}'
```

To review a real PR locally: `git diff main...HEAD > my.diff`, then `python cli.py --diff my.diff`.

### Use your own key

The live demo above runs on the author's key. To point your own copy at a
real model for the review judgement:

1. **Get a free key.** Go to `aistudio.google.com/api-keys` and click
   **Create API key** — pick "Create API key in new project" (no Cloud
   project and no credit card needed). Alternative: an OpenRouter key
   (`openrouter.ai`) used with a `:free` model slug.
2. **Local run:** `export LLM_API_KEY=your-key-here` before starting the
   server — or put it in a `.env` file you never commit.
3. **Render deploy:** dashboard → your service → Environment → add
   `LLM_API_KEY` → Save. Render redeploys automatically and the fresh
   build reads the key at startup (the backend is chosen once at import,
   so a restart is required — there is no hot-swap).
4. **Confirm it's live:** the stamp in the demo header turns green
   (`● live LLM · gemini-3.8-flash`), or `GET /info` returns
   `"real_llm": true`.
5. **Keep the key safe:** keys live in environment variables or a secret
   manager only — never in code, never in a commit.

## Project layout

```
reviewer/
  diff_parser.py   parse unified diffs into FileDiff structures
  checks.py        deterministic checks (secrets, debug, TODOs, size, tests)
  ignore.py        vendored/generated file ignore patterns + marker sniffing
  config.py        severity calibration: confidence → critical/warning/info
  repo_config.py   .pr-review.yaml loader: enabled_checks, severity_thresholds,
                   ignore patterns (vendor/*, *.min.js, lockfiles, …)
  prompts.py       versioned reviewer prompts (REVIEWER_PROMPT_VERSION, default v1)
  reviewer.py      Reviewer orchestrator + ReviewBackend interface + MockBackend
  cost.py          per-review cost/latency logging to JSONL (token + price table)
  comments.py      render findings as a markdown PR comment
  github.py        parse PR links + fetch diffs and .pr-review.yaml (stdlib only)
cli.py             CLI: review a diff file, exit 1 on critical findings
                   (--config for an explicit .pr-review.yaml; cwd auto-discovered)
service.py         FastAPI service: POST /review, POST /review-pr, GET /health
prompts/
  v1/              the original reviewer prompt (system.txt + user.txt)
  v2/              reworked challenger variant (explicit no-fences, line discipline)
evals/
  sample_pr.diff   sample PR with planted issues (secret, print, bare except…)
  run_eval.py      asserts every planted issue is caught (18/18)
  ab_prompts.py    A/B every prompt version on the eval set, declares a winner
  cost_table.py    print a cost/latency table from a review-log JSONL file
  sample_reviews.jsonl  10 real mock-backend review runs feeding cost_table.py
ROADMAP.md         where this goes next
```

## Evals

`evals/run_eval.py` runs the reviewer over a sample PR with five deliberately planted problems and asserts each one is caught: the AWS key flagged critical, the debug `print`, the bare `except:`, the TODO surfaced, and the new-file-without-tests nudge. The planted-issue checks run twice — once under `reviewer: mock` and once under `reviewer: anthropic` (keyless Anthropic degrades to the mock heuristics, so the same expectations hold). Six more expectations cover the PR-link flow: parsing canonical and `/pulls/` URLs, rejecting non-PR links, finding shape, severity ordering, and a clean diff rendering "No issues found". Two more cover the local-model backend: `reviewer: local` reviews the sample PR fully offline (real weights, zero network) and degrades gracefully to the mock heuristics when no weights are cached. 18/18 passing means the pipeline works end to end. Add your own diffs to `evals/` as the check set grows.

## Prompt versioning

The instruction text the model backends send lives in `prompts/<version>/`
(`system.txt` + `user.txt`), not in the backend code — the four model
backends used to carry identical copies of it. `v1` is that original prompt;
`v2` is a reworked challenger (explicit no-markdown-fences rule, line-number
discipline, "when unsure lower the confidence" calibration guidance).

Switch versions with `REVIEWER_PROMPT_VERSION` (default `v1`), the
`--prompt-version` CLI flag, or the `prompt_version` constructor arg on the
model backends. An unknown version falls back to `v1` with a stderr note —
a typo never silently reviews with the wrong instructions.

`python evals/ab_prompts.py` runs the 5-sample eval set against every
version on disk and declares a winner by mean F1 across backends
(tie-breaks: fewest false positives, then the incumbent keeps the crown).
With no API keys only the prompt-independent mock runs, so the A/B is
informational until a real backend is keyed — the script says so when
that's the case.

## Cost & latency logging

Every review can append one JSONL record — timestamp, backend, diff size,
finding counts by severity, wall-clock ms per stage (parse / checks / model /
dedupe), estimated tokens, and estimated cost:

```bash
python cli.py --diff evals/sample_pr.diff --log reviews.jsonl
python evals/cost_table.py reviews.jsonl   # the cost table
# 10 real mock runs are already logged: python evals/cost_table.py evals/sample_reviews.jsonl
```

Token counts are estimates (chars/4), not tokenizer output — the cost column
is order-of-magnitude. Prices default to small-model list prices per backend
(`reviewer/cost.py`), with `REVIEW_PRICE_<BACKEND>_INPUT_PER_1M`,
`REVIEW_PRICE_<BACKEND>_OUTPUT_PER_1M`, and `REVIEW_PRICE_<BACKEND>_FLAT`
env overrides; `mock` is $0 by definition, `local` charges a flat per-review
GPU-time guess. The HTTP service logs too when `REVIEW_LOG_PATH` is set.

## Honest notes

- The reviewer backend is a **mock** by default — it fires on a few obvious shapes (bare excepts, big single-file additions) so the pipeline runs offline. Swap in a real model with `reviewer: openai` (`REVIEWER_BACKEND=openai`, `OPENAI_API_KEY` set, `openai` package installed), `reviewer: anthropic` (`REVIEWER_BACKEND=anthropic`, `ANTHROPIC_API_KEY` set, `anthropic` package installed, model via `ANTHROPIC_MODEL`, default `claude-haiku-4-5`), `reviewer: local` (a small HF instruct model, default `Qwen/Qwen2-0.5B-Instruct` via `LOCAL_MODEL`, `transformers`+`torch` installed — no key, no network needed once weights are cached; `LOCAL_OFFLINE=1` forces the cache), or `reviewer: free` — real model judgement over a free HTTPS API (Gemini via Google AI Studio's free tier, no card; or OpenRouter with `LLM_PROVIDER=openrouter` and a `:free` model like `openai/gpt-oss-20b:free`, `LLM_MODEL` to override): no SDK, no weights, no extra deps. With `LLM_API_KEY` set and no explicit `REVIEWER_BACKEND`, `free` is auto-selected — the live Render demo just needs the env var. A failed model call skips that file instead of killing the review. Each file gets a per-file diff prompt with surrounding context and the JSON findings are validated into the same finding schema. Malformed model output never crashes the review. Without a key, the anthropic backend falls back to the mock heuristics instead of failing, so the config always runs.
- Secret patterns are a starter set, not exhaustive — real secret scanning (Gitleaks, GitHub secret scanning) should still run in CI.
- This reviews the *diff*, not the full codebase, so it won't catch architectural issues or cross-file inconsistencies. That's what the LLM backend is for.

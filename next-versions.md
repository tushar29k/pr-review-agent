# pr-review-agent — next versions plan

Current state (v0): deterministic checks (secrets, debug leftovers, TODOs,
binaries, diff size, missing tests) + mock reviewer backend, CLI + HTTP
service, evals at 5/5 on a planted sample PR. The vision roadmap (ROADMAP.md)
says v1 = real reviewer backend, v2 = GitHub App, v3 = learning loop. This
file breaks that into one-commit-per-day increments. Implement each, keep
evals green, commit with a human-style message.

## v1 — a real reviewer backend

- [ ] OpenAI reviewer backend: per-file diff hunk + surrounding context prompt, JSON findings — done when `reviewer: openai` reviews the sample PR with a key set
- [ ] Anthropic reviewer backend variant behind the same ReviewBackend interface — done when `reviewer: anthropic` passes the sample eval
- [ ] Local-model reviewer backend (small instruct model via HF) — done when it runs fully offline on the sample PR
- [ ] Severity calibration: map model confidence to critical/warning/info with documented thresholds — done when thresholds live in config
- [ ] Dedupe: merge deterministic + model findings landing on the same lines — done when the sample review shows no duplicates
- [ ] Cost/latency logging per review to JSONL — done when 10 reviews produce a cost table
- [ ] Evals: expand to 5 sample PRs, measure precision/recall of findings per backend — done when evals report per-backend scores
- [ ] Prompt versioning for reviewer prompts + A/B on the eval set — done when the runner declares a winning prompt

## v2 — the GitHub App

- [ ] Webhook receiver: pull_request events → fetch the diff via the GitHub API — done when a test webhook payload yields a diff
- [ ] Post the markdown review as a PR comment — done when a dry-run posts to a test PR
- [ ] Inline line-level comments per finding instead of one summary — done when findings map to diff positions
- [ ] Config file support (.pr-review.yaml: enabled checks, severity thresholds) — done when a test repo's config changes behaviour
- [ ] Ignore patterns for generated/vendored files — done when vendor/ diffs are skipped
- [ ] Check-run status: pass/fail on the PR based on critical findings — done when a critical finding blocks the check
- [ ] Evals: replay 10 real PRs (public repos), record findings per PR — done when replay results are committed
- [ ] Deploy docs: webhook secret handling + hosting (fly.io/railway) — done when docs/deploy.md is copy-paste usable

## v3 — the learning loop

- [ ] Feedback capture: 👍/👎 reactions on review comments → JSONL — done when reactions are ingested
- [ ] Per-repo noise stats: dismissal rate per check — done when the stats script prints the table
- [ ] Auto down-weight noisy checks per repo based on dismissal history — done when a noisy check is suppressed in the replay eval
- [ ] Tree-sitter checks: unused imports, dead code (Python) — done when the sample PR gains tree-sitter findings
- [ ] Complexity smells: long functions, deep nesting — done when thresholds in config flag the sample
- [ ] History-aware check: mine git log, scrutinise files that break often — done when a historically-buggy file gets a note
- [ ] Review-latency SLO: time-to-first-review metric + alert threshold — done when the metric is logged per review

## v4 — ship it

- [ ] Dockerfile + compose for the service + webhook receiver — done when the container reviews a diff end to end
- [ ] CI: evals on push, fail if planted issues are missed — done when the workflow is green
- [ ] ADRs: two-layer design (deterministic first), GitHub App vs Action — done when docs/adr/ has 2+ files
- [ ] GitHub Action wrapper version (action.yml) as an alternative to the App — done when the action runs on a test repo
- [ ] Release notes v1.0 in README with eval precision/recall + cost per review — done when README leads with them

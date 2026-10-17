# ADR 0001 — two-layer design: deterministic checks first, model judgement second

date: 2026-10-17
status: accepted

## context

A code reviewer has two jobs and they don't fail the same way. Some findings
are non-negotiable — a leaked AWS key, a committed private key — and they
must fire every time, even when the model is down, slow, or having an off
day. Others need judgement — is this function too long, is this TODO worth
chasing — where a model beats any rulebook. The question was the order:
ask the model first and sprinkle checks on top, or run the checks first
and let the model add what it sees?

The pipeline had to work with zero keys (the mock backend is the default,
and the evals assert on it), had to never silently drop the cheap findings,
and had to give a repo a way to say "skip the model entirely" without
breaking the review.

## decision

Deterministic checks always run first, the model runs second, and the two
layers merge before anything is reported. `reviewer.py` spells it out in
its module docstring: "the cheap, certain checks run first and the model
only gets asked about the rest."

The stage order in `Reviewer.review_timed()` is: parse the diff, filter
vendored/generated files (`reviewer/ignore.py`), then `checks.run_all()`
over everything in `KNOWN_CHECKS` (in `reviewer/repo_config.py`): secrets,
debug_leftovers, todos, binary, diff_size, missing_tests, unused_imports,
dead_code, complexity, history. Only then, if `check_enabled("llm")`,
each file goes through `backend.review_file()`.

Two things keep the layers honest. First, deterministic findings are built
by `checks._finding()` with `confidence: 1.0` — the calibration layer in
`_recalibrate()` only remaps findings carrying a model confidence, so a
secret can never be demoted by a repo's threshold settings ("a secret is
critical, no vote"). Second, `dedupe_findings()` merges deterministic and
model findings landing on the same lines into one finding: the model's
message wins, the severity is the higher of the two, provenance lands in
`sources`.

The deterministic layer also carries its own honesty rules, earned the
hard way: `check_unused_imports` and `check_complexity` only run on new
files (on a modified file the import could be used by code outside the
hunks — half a function tells you nothing), and `check_dead_code` needs
terminator and dead lines both added with no hidden lines between them, so
dropped hunk context can't fake the block nesting.

## consequences

- A leaked secret is flagged critical with no keys configured and no
  network — the v0 evals (5/5 on the planted sample PR) already proved
  this, and the CI gate (`evals/run_eval.py`) fails the build if a
  planted AWS key ever goes unmissed.
- Setting `llm: false` in `.pr-review.yaml` leaves a working reviewer: the
  deterministic layer reports on its own. The model layer is additive,
  never load-bearing.
- The learning loop respects the layering: `reviewer/downweight.py`
  demotes info findings from checks a repo keeps dismissing, and
  suppresses them outright — but criticals are never fully dropped.
- Cost and latency concentrate in one place: the deterministic layer is
  free, offline, and instant; the model pass is the only line item in
  the per-review cost record (`reviewer/cost.py`).
- Trade-off, stated plainly: the deterministic layer is dumb. Regexes
  miss intent and can't judge design. The two-layer design doesn't fix
  that — it just makes sure the dumb-but-certain layer can't be voted
  down by the smart-but-flaky one.

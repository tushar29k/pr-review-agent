# pr-review-agent as a GitHub Action

`action.yml` is the no-server alternative to the GitHub App (see
[ADR 0002](adr/0002-github-app-vs-action.md)). Add it to any repo's
workflow and the deterministic checks run on every PR — secrets, debug
leftovers, TODOs, binaries, diff size, missing tests, unused imports,
dead code, complexity smells, history hotspots — with no webhook, no
hosting, and no reviewer API keys. It uses the `mock` backend, so there
is nothing to bill and nothing to leak.

## minimal workflow

```yaml
name: pr-review
on: pull_request
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # the action diffs base...head; shallow clones break this
      - uses: tushar29k/pr-review-agent@main
```

## inputs

| input | default | what it does |
|---|---|---|
| `fail-on-critical` | `'false'` | `'true'` fails the step when any critical-severity finding (e.g. a committed secret) turns up; `'false'` just reports |
| `checks` | `''` (all) | comma-separated check names to run, e.g. `'secrets,debug_leftovers'` — maps onto `cli.py --checks` |

No outputs are set; the rendered markdown review goes to the run's
step summary (`$GITHUB_STEP_SUMMARY`) and is echoed to the step log.

## App vs Action, in practice

- **App** (`POST /webhooks/github`): one install reviews every repo you
  add it to; posts a summary comment, inline comments per finding, and a
  check-run verdict on the PR itself; aggregates 👍/👎 feedback across
  repos into the noise-learning loop. Needs a hosted service.
- **Action**: zero infrastructure — the checks run inside your own
  runner and vanish. Reviews only the repo it lives in, reports to the
  step summary (not a PR comment), and skips the feedback loop and
  model backends. If you later want PR comments or learning, switch to
  the App; the check pipeline underneath is the same `cli.py`.

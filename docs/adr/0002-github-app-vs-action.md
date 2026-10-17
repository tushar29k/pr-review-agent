# ADR 0002 — GitHub App (webhook service) vs GitHub Action

date: 2026-10-17
status: accepted

## context

Once the reviewer had real backends, it needed a delivery mechanism: how
does a review actually show up on a pull request? Two shapes were on the
table. A GitHub App: one service, one webhook receiver, installed on any
number of repos. Or a GitHub Action: a workflow file living inside each
repo, running on `pull_request` events, no server to host.

v2 shipped the App. The question this ADR records is why the App came
first — and where the Action still fits (the roadmap keeps a "GitHub
Action wrapper" as a future item, and this decision shouldn't be read as
killing it).

## decision

Ship the GitHub App as the primary integration. The moving parts:

- `reviewer/webhook.py` is the receiver: `pull_request` events, only
  `opened` and `synchronize` actions ("a fresh review is only worth it on
  open and on new pushes"). `POST /webhooks/github` in `service.py`
  verifies `X-Hub-Signature-256` against `WEBHOOK_SECRET` — fail closed
  when the secret is set, warn-and-skip when it isn't.
- `reviewer/github.py` does the posting, all stdlib `urllib`: the
  summary comment (`post_comment`), per-finding inline comments anchored
  to diff positions (`post_review_comments`, built by
  `reviewer/inline.py`), and the check-run verdict
  (`post_check_run` — conclusion `failure` when any finding is critical,
  findings as check-run annotations).
- Everything is dry-run by default: `dry_run=True` returns the exact
  payload without touching the network, live mode refuses without a
  token, and the service opts into live posting only with
  `WEBHOOK_DRY_RUN=0` + `GITHUB_TOKEN`.
- Hosting is documented copy-paste style in `docs/deploy.md` (fly.io,
  railway, Render with `$PORT` notes, App permissions and events setup).

The trade-offs that tipped it:

- **Secrets.** The App's secrets live in one place — the service's env.
  An Action would need the reviewer configuration in every repo's
  secrets, or lean on the auto-provisioned `GITHUB_TOKEN`, which is
  repo-scoped and short-lived: fine for posting comments on its own PR,
  awkward for anything broader.
- **Permissions and coverage.** One install covers every repo it's
  added to; the webhook receives whatever events GitHub sends. An
  Action runs inside the repo it lives in — the diff is already there,
  no webhook, no signature verification — but someone has to add the
  workflow file, and it only reviews that repo.
- **Latency and cost.** The App needs an always-on service. The free
  Render tier sleeps after ~15 min idle, so the first webhook after a
  quiet night pays a cold start — the review-latency SLO (`first_review_ms`,
  30s default budget) keeps that visible. An Action spins up per PR
  event and vanishes: runner-boot latency instead of cold starts,
  billed minutes instead of uptime.
- **Testability.** The App is testable locally against recorded payloads:
  `test_webhook.py` (76/76) replays a fixture from psf/requests#7616.
  An Action's integration test means pushing a commit and reading the
  run log.

## consequences

- One deploy reviews many repos — the demo service on Render does
  exactly this, and the feedback learning loop (`reviewer/feedback.py`,
  `noise_stats.py`, `downweight.py`) aggregates per repo (`owner/repo`)
  across installs, which only works because reviews flow through one
  service.
- If the service is down, nobody gets a review. The dry-run default and
  the 401 fail-closed proof step in `docs/deploy.md` make misconfigurations
  loud, but the single point of failure stays real.
- The Action wrapper remains the planned alternative for people who'd
  rather not host anything — when it lands, `action.yml` will call the
  same `review_timed()` pipeline; the two-layer design (ADR 0001) makes
  that a packaging change, not a reviewer rewrite.

# Deploying pr-review-agent with a working signed webhook

This walks you from a fresh clone to a public URL where GitHub can deliver
`pull_request` webhooks and get a review back. Follow it top to bottom —
every command is copy-pasteable. The receiver lives at
`POST /webhooks/github` and is fully described in
[README.md](../README.md#github-webhook-receiver-dry-run); this doc only
covers getting it hosted.

## 0. What gets deployed

- `POST /review` — diff in, markdown review out (the demo UI on `/`).
- `POST /webhooks/github` — the GitHub App webhook: verifies the delivery
  signature, fetches the PR diff, reviews it, and (when you opt in) posts
  the markdown as a PR comment, inline line comments, and a check run.
- `GET /health` — returns `{"ok": true}`; use it as the host's health check.

Start command everywhere below: `uvicorn service:app --host 0.0.0.0 --port $PORT`
(ports must come from `$PORT` on hosted platforms, not the local `--port 8000`).

## 1. Generate the webhook secret

GitHub signs every webhook delivery with HMAC-SHA256 of the raw request
body, using a shared secret, and sends the signature in the
`X-Hub-Signature-256: sha256=<hex>` header. The app checks it in
`reviewer/webhook.py::verify_signature`: secret set → any missing or wrong
signature gets a **401** (fail closed); secret unset → verification is
skipped with a warning (fine for local dev, never for production).

Generate one secret and use the same value on GitHub and on your host:

```bash
openssl rand -hex 32
# example output: 9f2c7a1e... (64 hex chars) — keep this, don't paste it anywhere public
```

Optional local proof that signing works (no network needed):

```bash
export WEBHOOK_SECRET=<the secret from above>
python3 - <<'EOF'
import hmac, hashlib, os
from reviewer.webhook import verify_signature
body = b'{"zen":"test"}'
secret = os.environ["WEBHOOK_SECRET"]
sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
assert verify_signature(body, sig, secret) is True        # real signature passes
assert verify_signature(body, "sha256=wrong", secret) is False  # forgery fails
assert verify_signature(body, None, secret) is False     # missing header fails
assert verify_signature(body, None, None) is True         # unset secret skips
print("signature checks behave as documented")
EOF
```

## 2. Env vars the deployment needs

| Var | Required | What it does |
|-----|----------|--------------|
| `WEBHOOK_SECRET` | yes, for a real App | shared secret for signature verification |
| `GITHUB_TOKEN` | for live posting | app install token or PAT; also raises the API rate limit and unlocks private repos |
| `WEBHOOK_DRY_RUN` | no (default `1`) | set to `0` to post comments/check-runs live; leave unset while testing |
| `REVIEWER_BACKEND` | no (default mock) | `openai` / `anthropic` / `local` / `free`; see README for keys each needs |
| `LLM_API_KEY` | no | auto-selects the `free` backend when no `REVIEWER_BACKEND` is set |

Set them as **secrets/env vars on the host**, never in the repo or a Dockerfile.

## 3. Option A — fly.io

Install: `curl -L https://fly.io/install.sh | sh` (or `brew install flyctl`),
then `flyctl auth login`.

Add this `Dockerfile` at the repo root (it isn't committed — this keeps the
repo host-agnostic; fly builds from your local checkout):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD uvicorn service:app --host 0.0.0.0 --port ${PORT:-8000}
```

Then:

```bash
cd pr-review-agent
flyctl launch --name pr-review-agent --region bom --no-deploy
# say NO to postgres/redis — the app needs neither

flyctl secrets set WEBHOOK_SECRET=<your secret>
flyctl secrets set GITHUB_TOKEN=<your token>   # only if you want live posting

flyctl deploy
```

Expected: `flyctl status` shows the app running; `curl
https://pr-review-agent.fly.dev/health` returns `{"ok":true}`.
Set `fly.toml`'s `[[http_service]] internal_port` to match the port uvicorn
binds (the `Dockerfile` above reads `$PORT`, which fly sets for you).

Your webhook URL is `https://pr-review-agent.fly.dev/webhooks/github`.

## 4. Option B — railway

Install: `npm i -g @railway/cli`, then `railway login`. Or use the dashboard:
New Project → Deploy from GitHub repo → pick `pr-review-agent`.

CLI path:

```bash
cd pr-review-agent
railway init          # create the project
railway up            # deploys the repo; railway detects requirements.txt
```

Then set the runtime (dashboard → service → Settings, or
`railway variables set KEY=VALUE`):

- **Start command:** `uvicorn service:app --host 0.0.0.0 --port $PORT`
- **Variables:** `WEBHOOK_SECRET`, `GITHUB_TOKEN` (if live posting),
  `WEBHOOK_DRY_RUN` (leave unset until you're ready).
- **Health check path:** `/health`

Expected: the deployment log shows uvicorn listening, and
`curl https://<your-subdomain>.up.railway.app/health` returns `{"ok":true}`.

Your webhook URL is `https://<your-subdomain>.up.railway.app/webhooks/github`.

## 5. Point a GitHub App at it

1. GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.
2. **Webhook URL:** the URL from step 3 or 4 (must end in `/webhooks/github`).
3. **Webhook secret:** paste the secret from step 1 — this is the other half
   of the `X-Hub-Signature-256` check.
4. **Permissions:** Repository → Pull requests: Read & write (read to fetch
   the diff, write to post comments); Checks: Read & write (for the check
   run); Contents: Read (to read `.pr-review.yaml` at the PR head).
5. **Subscribe to events:** check `Pull request`.
6. Create the app, then install it on a test repo (install on just one repo
   while testing).

## 6. Prove the webhook is really signed end to end

1. Open a test PR in the installed repo.
2. GitHub App settings → Advanced → Recent Deliveries: find the
   `pull_request` delivery, open **Response**. You should see HTTP 200 with
   `"handled": true`, `"diff_chars"`, and `"comment": {"dry_run": true, ...}`.
3. Temporarily break the secret: App settings → change the webhook secret to
   a wrong value → redeliver → the response is now **401 "bad webhook
   signature"**. Restore the real secret and redeliver → 200 again.
   That 401 is the fail-closed check in `service.py` doing its job.
4. When the dry-run replies look right, flip to live: set `WEBHOOK_DRY_RUN=0`
   (and `GITHUB_TOKEN`) on the host, redeploy, open another test PR, and
   watch the review comment, inline comments, and check run land.

## 7. Troubleshooting

- **401 on every delivery** — the secret on the host and the secret in the
  App settings don't match. They must be byte-identical; re-set both.
- **200 but `"handled": false`** — the event wasn't `pull_request` or the
  action wasn't `opened`/`synchronize` (e.g. `labeled`, `closed`). Expected;
  the app only reviews on open and new pushes.
- **502 with a GitHub error** — the diff fetch failed: private repo without
  `GITHUB_TOKEN`, or the unauthenticated 60-requests/hour rate limit. Set
  the token.
- **Wrong/no signature passes locally** — `WEBHOOK_SECRET` isn't set in that
  environment, so verification is skipped with a warning. Check the env.
- **fly/railway health check fails** — the app is listening on 8000 but the
  platform probes `$PORT`. Use the start command from step 0 verbatim.

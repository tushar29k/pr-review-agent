# Roadmap

**v0 — now:** deterministic checks + mock backend, CLI + HTTP service, evals. The pipeline works end to end with zero API keys.

**v1 — real reviewer backend.** Implement `ReviewBackend` against an actual model (OpenAI/Anthropic/local). Prompt it per-file with the diff hunk plus surrounding context; keep the deterministic checks running first so model failures never hide secrets.

**v2 — GitHub App.** Webhook on `pull_request` events → fetch the diff via the GitHub API → post the markdown as a PR review comment. Inline comments per finding (line-level) instead of one summary comment.

**v3 — learning loop.** Track which findings humans dismiss vs act on; down-weight noisy checks per repo. A reviewer that cries wolf gets ignored — the goal is a signal-to-noise ratio humans trust.

**Later ideas:** language-aware checks via tree-sitter (unused imports, dead code), historical context ("this file breaks every other Friday"), review-latency SLOs.

# PR replay results

Run: 2026-10-07 — backend: mock (fully offline, same path as the eval suite). PRs are real, recently merged, fetched live via gh.

No ground truth exists for these PRs — the "notable misses" column is a spot-check flag, not a claim of bugs found or missed.

| PR | repo | diff (+/-) | findings (c/w/i) | checks fired | notable misses |
| --- | --- | --- | --- | --- | --- |
| [6767](https://github.com/psf/requests/pull/6767) | psf/requests | +16/-39 (1 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |
| [7010](https://github.com/psf/requests/pull/7010) | psf/requests | +6/-6 (4 files) | 0/0/0 | — | docs/config-only change; any findings are likely noise |
| [6096](https://github.com/pallets/flask/pull/6096) | pallets/flask | +23/-5 (4 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |
| [6133](https://github.com/pallets/flask/pull/6133) | pallets/flask | +14/-2 (3 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |
| [15058](https://github.com/pytest-dev/pytest/pull/15058) | pytest-dev/pytest | +49/-0 (4 files) | 0/0/1 | llm | only low-severity findings — nothing actionable surfaced |
| [15121](https://github.com/pytest-dev/pytest/pull/15121) | pytest-dev/pytest | +3/-2 (2 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |
| [3773](https://github.com/encode/httpx/pull/3773) | encode/httpx | +4/-1 (1 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |
| [3699](https://github.com/encode/httpx/pull/3699) | encode/httpx | +6/-1 (3 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |
| [22090](https://github.com/django/django/pull/22090) | django/django | +8/-3 (3 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |
| [22096](https://github.com/django/django/pull/22096) | django/django | +9/-3 (3 files) | 0/0/0 | — | no findings on a code change (possible miss — spot-check by hand) |

Total: 10 PRs replayed, 1 findings recorded.

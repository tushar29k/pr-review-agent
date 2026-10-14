"""History-aware reviewing: mine the repo's git log, scrutinise the files
that break often.

Files with a pile of bug-fix commits behind them are where the next bug
usually hides — a reviewer who knows the history looks twice at them.
mine_git_log() counts bug-fix commits per file; check_hotspots() (in
checks.py) turns the worst offenders into info findings on the PR.

A missing git binary or a non-repo directory just yields {} — the review
runs on without the history layer, never crashes on it.
"""
from __future__ import annotations

import re
import subprocess

# bug-fix-y subjects — deliberately narrow: "fix", "hotfix", "bugfix",
# "patch", "regression", "crash", "broken". "fixed a typo in the docs"
# counts; a fix is a fix, the threshold sorts the noise from the pattern.
FIX_RE = re.compile(
    r"\b(fix(es|ed|ing)?|bugfix|hotfix|patch|regression|crash|broken|defect)\b",
    re.IGNORECASE,
)

# one fix is noise, two is coincidence — three is where "breaks often"
# starts. same default as config.HISTORY_MIN_FIXES.
DEFAULT_MIN_FIXES = 3


def mine_git_log(repo_dir: str,
                 since_days: int = 365,
                 max_commits: int = 2000) -> dict[str, int]:
    """{path: bug-fix-commit count} for repo_dir's recent history."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, "log",
             f"--since={since_days}.days.ago",
             f"--max-count={max_commits}",
             "--pretty=format:COMMIT:%H:%s", "--name-only", "--no-merges"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return {}  # no git, no repo, no history — review continues anyway
    if proc.returncode != 0:
        return {}

    fix_counts: dict[str, int] = {}
    is_fix = False
    for line in proc.stdout.splitlines():
        if line.startswith("COMMIT:"):
            subject = line.split(":", 2)[-1]
            is_fix = bool(FIX_RE.search(subject))
        elif is_fix and line.strip():
            # renames show as "old => new" — the file that got fixed is
            # the new side
            path = line.rsplit(" => ", 1)[-1].strip()
            if path:
                fix_counts[path] = fix_counts.get(path, 0) + 1
    return fix_counts


def hotspots(fix_counts: dict[str, int],
             min_fixes: int = DEFAULT_MIN_FIXES) -> dict[str, int]:
    """Keep only the files fixed often enough to be worth flagging."""
    return {p: n for p, n in fix_counts.items() if n >= min_fixes}

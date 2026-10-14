"""Severity calibration: one place where confidence becomes severity.

Models report how sure they are (0.0-1.0); this maps that number onto
critical/warning/info so every backend calibrates the same way instead of
each one inventing its own scale. The deterministic checks skip this —
a leaked secret isn't a matter of opinion, they're certain by design
(implicitly confidence 1.0).
"""

from __future__ import annotations

import os


def _threshold(env: str, default: float) -> float:
    # env overrides keep deployments tunable without touching code
    try:
        v = float(os.environ.get(env, default))
    except (TypeError, ValueError):
        v = default
    return min(1.0, max(0.0, v))


def _count(env: str, default: int) -> int:
    # same idea for whole-number knobs — garbage falls back to default
    try:
        v = int(os.environ.get(env, default))
    except (TypeError, ValueError):
        v = default
    return max(1, v)


SEVERITY_CRITICAL: float = _threshold("SEVERITY_CRITICAL", 0.80)
SEVERITY_WARNING: float = _threshold("SEVERITY_WARNING", 0.50)

# history-aware check (reviewer/history.py): how many bug-fix commits a
# file needs before it counts as "breaks often". 3 because one fix is
# noise, two is coincidence, three is a pattern worth flagging.
HISTORY_MIN_FIXES: int = _count("HISTORY_MIN_FIXES", 3)

# complexity-smell limits (used by the tree-sitter complexity check; a
# repo's .pr-review.yaml can override both under `complexity:`):
# 50 lines because a function you can't see whole on one screen makes
# reviewers scroll-and-forget; 3 nesting levels because the fourth
# `if` inside `for` inside `while` inside `if` is where humans start
# losing track of which branch they're in.
MAX_FUNCTION_LINES: int = _count("COMPLEXITY_MAX_LINES", 50)
MAX_NESTING_DEPTH: int = _count("COMPLEXITY_MAX_DEPTH", 3)

# evaluated in order: confidence >= critical becomes critical,
# >= warning becomes warning, anything below is info.
# 0.80 / 0.50 because critical should mean "block the merge", which needs
# strong conviction; 0.50 keeps weak model guesses as info noise instead
# of warning-fatigue.


def confidence_to_severity(confidence: float,
                           critical: float | None = None,
                           warning: float | None = None) -> str:
    """Map a 0.0-1.0 model confidence onto critical/warning/info.

    The optional overrides let a repo's .pr-review.yaml remap the scale
    per-review; without them the module defaults (and their env-var
    overrides) apply."""
    crit = SEVERITY_CRITICAL if critical is None else critical
    warn = SEVERITY_WARNING if warning is None else warning
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        c = 0.0  # unreadable confidence — treat it as a weak guess
    c = min(1.0, max(0.0, c))
    if c >= crit:
        return "critical"
    if c >= warn:
        return "warning"
    return "info"

"""Down-weight noisy checks from dismissal history.

noise_stats.py prints per-repo dismissal rates per check. This module
closes the loop: checks a repo keeps dismissing get their findings
demoted one severity level, and info-level findings from a noisy check
are suppressed outright. Critical findings are never suppressed — a
dismissed true positive is a false negative you can't afford — they
only soften one notch.

No weights = no behaviour change: pass nothing and the review runs
exactly as before.
"""

from __future__ import annotations

from .noise_stats import compute_stats, dismissal_rate, load_events

# one notch softer; info is the bottom rung and falls off instead
_DEMOTE = {"critical": "warning", "warning": "info"}


def noisy_checks(events: list[dict], repo: str,
                 threshold: float = 0.5, min_n: int = 3) -> dict[str, float]:
    """{check: dismissal_rate} for checks crossing the noisy bar in repo.

    Same bar as the noise-stats table: dismissal rate at/above threshold
    on at least min_n verdicts. Summary comments (no check) can land here
    but never match a real finding, so they're harmless.
    """
    noisy: dict[str, float] = {}
    for (r, check), counts in compute_stats(events).items():
        if r != repo:
            continue
        rate = dismissal_rate(counts["positive"], counts["negative"])
        verdicts = counts["positive"] + counts["negative"]
        if rate is not None and rate >= threshold and verdicts >= min_n:
            noisy[check] = rate
    return noisy


def load_noisy_weights(log_path: str, repo: str,
                       threshold: float = 0.5,
                       min_n: int = 3) -> dict[str, float]:
    """Noise weights for a repo from its feedback JSONL. Missing log or
    no verdicts yet = {} — the review runs unweighted, never errors."""
    return noisy_checks(load_events(log_path), repo, threshold, min_n)


def apply_downweight(findings: list[dict],
                     noisy: dict[str, float]
                     ) -> tuple[list[dict], list[dict]]:
    """Demote findings from noisy checks one severity level; info-level
    ones drop out entirely. Returns (kept, suppressed); touched findings
    are copies tagged downweighted + dismissal_rate so the output can
    say why they moved."""
    if not noisy:
        return list(findings), []
    kept: list[dict] = []
    suppressed: list[dict] = []
    for f in findings:
        rate = noisy.get(f.get("check"))
        if rate is None:
            kept.append(f)
            continue
        out = dict(f)
        out["downweighted"] = True
        out["dismissal_rate"] = rate
        sev = out.get("severity")
        if sev == "info":
            suppressed.append(out)  # bottom rung — nothing left to soften
        elif sev in _DEMOTE:
            out["severity"] = _DEMOTE[sev]
            kept.append(out)
        else:
            kept.append(f)  # unknown severity — don't touch what we don't know
    return kept, suppressed

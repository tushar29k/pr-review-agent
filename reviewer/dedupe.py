"""Dedupe: merge deterministic + model findings that land on the same lines.

Both layers flag the same sins — the TODO regex and a model judging the
same line produce two findings for one problem, and the review reads like
two people spotting the same thing and both saying it. This pass runs
after every backend (so it's backend-agnostic: mock, openai, anthropic,
local all flow through the same merge) and collapses overlaps into one
finding.

merge rule:
- same file + overlapping line spans (a multi-line model span meeting a
  single-line deterministic hit counts) → one finding
- only deterministic↔model pairs merge — two deterministic checks on one
  line stay separate, they're different rules for different problems
- the model's message wins (richer text); severity is the higher of the
  two; confidence follows the winner — the model's own confidence when it
  won (backends already calibrate through confidence_to_severity, so no
  second remap), 1.0 when the deterministic check won (certain by design;
  hand-set deterministic severities are never pushed through the scale,
  so a warning stays a warning)
- provenance survives in `sources`, e.g. ["todos", "llm"], and the
  deterministic check name is kept as `check` so consumers keyed on the
  stable taxonomy (evals, the UI) don't break
"""

from __future__ import annotations

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def _is_model(finding: dict) -> bool:
    # every backend names itself llm, llm-openai, llm-anthropic, llm-local
    check = finding.get("check", "")
    return check == "llm" or check.startswith("llm-")


def _has_model_layer(finding: dict) -> bool:
    # a finding already merged with a model finding keeps its model side,
    # so it can still absorb further deterministic hits on its span
    if _is_model(finding):
        return True
    return any(_is_model({"check": s}) for s in finding.get("sources", []))


def _span(finding: dict) -> tuple[int | None, int | None]:
    # forward-compatible: findings may one day carry an explicit line_end
    line = finding.get("line")
    line_end = finding.get("line_end") or line
    return line, line_end


def _mergeable(a: dict, b: dict) -> bool:
    # same file, one deterministic + one model, overlapping spans
    if a["file"] != b["file"]:
        return False
    if _has_model_layer(a) == _has_model_layer(b):
        return False  # same layer — not this pass's job
    a0, a1 = _span(a)
    b0, b1 = _span(b)
    if a0 is None or b0 is None:
        return False  # unanchored file-level notes stay separate
    return a0 <= b1 and b0 <= a1


def _merge(a: dict, b: dict) -> dict:
    # the side that carries model judgement (directly or via an earlier merge)
    model, det = (a, b) if _has_model_layer(a) else (b, a)
    out = dict(det)  # deterministic check name = the stable taxonomy
    out["message"] = model["message"]  # model explains it better
    model_wins = (_SEVERITY_RANK.get(model["severity"], 9)
                  <= _SEVERITY_RANK.get(det["severity"], 9))
    out["severity"] = model["severity"] if model_wins else det["severity"]
    out["confidence"] = model.get("confidence") if model_wins else 1.0
    # keep the most specific anchor: model's line, else the deterministic one;
    # and keep the union of any line ranges so a multi-line model span still
    # overlaps the next deterministic hit it touched
    starts = [s for s in (_span(model)[0], _span(det)[0]) if s is not None]
    ends = [e for e in (_span(model)[1], _span(det)[1]) if e is not None]
    out["line"] = min(starts) if starts else None
    if ends and max(ends) != out["line"]:
        out["line_end"] = max(ends)
    else:
        out.pop("line_end", None)
    # provenance: who flagged this, in the order they ran
    srcs = list(det.get("sources") or [det["check"]])
    for s in model.get("sources") or [model["check"]]:
        if s not in srcs:
            srcs.append(s)
    out["sources"] = srcs
    return out


def dedupe_findings(findings: list[dict]) -> list[dict]:
    """Collapse overlapping deterministic↔model findings into one each."""
    work = [dict(f) for f in findings]
    for f in work:
        f.setdefault("sources", [f.get("check")])
    # fixpoint: merging widens nothing here (single-line spans), but a
    # multi-line model span can touch several deterministic hits, so loop
    # until no mergeable pair remains
    changed = True
    while changed:
        changed = False
        for i in range(len(work)):
            for j in range(i + 1, len(work)):
                if _mergeable(work[i], work[j]):
                    work[i] = _merge(work[i], work[j])
                    del work[j]
                    changed = True
                    break
            if changed:
                break
    return work

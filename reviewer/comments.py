"""Render findings as the markdown comment you'd post on the PR."""

from __future__ import annotations

_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def findings_to_markdown(findings: list[dict],
                         skipped: list[str] | None = None,
                         downweighted: list[dict] | None = None) -> str:
    skipped = skipped or []
    downweighted = downweighted or []
    lines = ["## 🤖 PR review", ""]
    if not findings:
        lines.append("No issues found. Either it's clean or I'm not "
                     "looking hard enough — give the logic a human skim anyway.")
    else:
        counts = {}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        summary = ", ".join(f"{counts[s]} {s}"
                            for s in ("critical", "warning", "info")
                            if s in counts)
        lines += [f"**{summary}** found.", ""]
        for f in findings:
            emoji = _SEVERITY_EMOJI.get(f["severity"], "⚪")
            where = f"`{f['file']}`"
            if f["line"]:
                where += f":{f['line']}"
            lines.append(f"- {emoji} **{f['severity']}** {where} — {f['message']}")
    if skipped:
        # skipped files get a line, never silence — someone should know the
        # review didn't look at them
        names = ", ".join(f"`{p}`" for p in skipped)
        plural = "s" if len(skipped) != 1 else ""
        lines += ["",
                  f"_Skipped {len(skipped)} generated/vendored file{plural} "
                  f"({names}) — matched the ignore patterns._"]
    if downweighted:
        # same rule: the learning loop touched the review, say so out loud
        parts = []
        for check in sorted({f.get("check", "?") for f in downweighted}):
            n = sum(1 for f in downweighted if f.get("check") == check)
            rate = next((f.get("dismissal_rate") for f in downweighted
                         if f.get("check") == check
                         and isinstance(f.get("dismissal_rate"), float)),
                        None)
            rate_s = f", dismissed {rate:.0%} of the time" if rate is not None else ""
            parts.append(f"{n} from noisy check `{check}`{rate_s}")
        plural = "s" if len(downweighted) != 1 else ""
        lines += ["",
                  f"_Down-weighted {len(downweighted)} finding{plural} "
                  f"({'; '.join(parts)})._"]
    lines += ["",
              "_Deterministic checks + mock reviewer. "
              "Wire up a real model backend for deeper feedback._"]
    return "\n".join(lines)

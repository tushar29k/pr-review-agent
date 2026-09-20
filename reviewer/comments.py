"""Render findings as the markdown comment you'd post on the PR."""

from __future__ import annotations

_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def findings_to_markdown(findings: list[dict]) -> str:
    if not findings:
        return ("## 🤖 PR review\n\nNo issues found. "
                "Either it's clean or I'm not looking hard enough — "
                "give the logic a human skim anyway.")

    counts = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    summary = ", ".join(f"{counts[s]} {s}" for s in ("critical", "warning", "info")
                        if s in counts)

    lines = ["## 🤖 PR review", "", f"**{summary}** found.", ""]
    for f in findings:
        emoji = _SEVERITY_EMOJI.get(f["severity"], "⚪")
        where = f"`{f['file']}`"
        if f["line"]:
            where += f":{f['line']}"
        lines.append(f"- {emoji} **{f['severity']}** {where} — {f['message']}")
    lines += ["",
              "_Deterministic checks + mock reviewer. "
              "Wire up a real model backend for deeper feedback._"]
    return "\n".join(lines)

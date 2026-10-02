"""Map review findings onto diff positions for GitHub inline comments.

GitHub review comments anchor to a file+line *inside the diff* — a finding
on an unchanged line has no anchor, so it can't become an inline comment
and gets skipped here instead of posting garbage the API would reject.
"""

from __future__ import annotations

from reviewer.diff_parser import parse_diff


def _new_side_lines(diff_text: str) -> dict[str, set[int]]:
    """path -> the new-file line numbers this diff actually touches.

    Only lines inside a hunk (added or context) can anchor an inline
    comment; context lines anchor fine too, so both are kept.
    """
    positions: dict[str, set[int]] = {}
    for f in parse_diff(diff_text):
        if f.is_binary:
            continue
        lines = {h.new_no for h in f.hunks
                 if h.kind in ("add", "ctx") and h.new_no is not None}
        if lines:
            positions[f.path] = lines
    return positions


def _comment_body(finding: dict) -> str:
    # short header so the inline comment says where it came from at a glance
    sev = finding.get("severity", "info")
    check = finding.get("check", "")
    msg = finding.get("message", "")
    return f"**{sev}** `{check}`\n\n{msg}"


def map_findings_to_positions(diff_text: str,
                             findings: list[dict]) -> list[dict]:
    """Anchor each finding to a (path, line, side) the diff supports.

    Returns [{path, line, side, body, severity, check, ...}]. Skips findings
    with no line (e.g. missing_tests), on files the diff never touches, or
    on unchanged lines — GitHub would reject those as unanchored comments.
    """
    positions = _new_side_lines(diff_text)
    comments = []
    for f in findings:
        path = f.get("file")
        line = f.get("line")
        if not path or line is None:
            continue  # file-level findings can't anchor inline
        if line not in positions.get(path, ()):
            continue  # unchanged line: no anchor, no inline comment
        comments.append({
            "path": path,
            "line": line,
            "side": "RIGHT",  # we only anchor to the new side of the diff
            "body": _comment_body(f),
            "severity": f.get("severity"),
            "check": f.get("check"),
        })
    return comments

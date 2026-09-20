"""Turn a raw unified diff into structures we can actually reason about.

Nothing fancy here — just careful line bookkeeping so every check
downstream knows exactly which file and line it's looking at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class HunkLine:
    kind: str          # "add", "del" or "ctx"
    old_no: int | None
    new_no: int | None
    text: str          # the line content, without the leading +/-/space


@dataclass
class FileDiff:
    path: str
    old_path: str
    is_new: bool = False
    is_binary: bool = False
    hunks: list[HunkLine] = field(default_factory=list)

    @property
    def added_lines(self) -> list[HunkLine]:
        return [h for h in self.hunks if h.kind == "add"]

    @property
    def added_count(self) -> int:
        return len(self.added_lines)


# @@ -old_start,old_len +new_start,new_len @@
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff. Tolerant of git and plain-diff flavours."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    old_no = new_no = 0

    def start_file(line: str) -> None:
        nonlocal current
        # diff --git a/foo.py b/foo.py  ->  paths are tokens 2 and 3
        parts = line.split()
        old = parts[2][2:] if len(parts) > 2 and parts[2].startswith("a/") else parts[2]
        new = parts[3][2:] if len(parts) > 3 and parts[3].startswith("b/") else old
        current = FileDiff(path=new, old_path=old)
        files.append(current)

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("diff --git"):
            start_file(line)
            continue
        if current is None:
            continue  # preamble junk before the first file header
        if line.startswith("Binary files "):
            current.is_binary = True
            continue
        if line.startswith("--- "):
            p = line[4:].strip()
            current.old_path = p[2:] if p.startswith("a/") else p
            if p == "/dev/null":
                current.is_new = True
            continue
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p != "/dev/null":
                current.path = p[2:] if p.startswith("b/") else p
            continue
        m = _HUNK_RE.match(line)
        if m:
            old_no, new_no = int(m.group(1)), int(m.group(2))
            continue
        if not line:
            continue
        marker, content = line[0], line[1:]
        if marker == "+":
            current.hunks.append(HunkLine("add", None, new_no, content))
            new_no += 1
        elif marker == "-":
            current.hunks.append(HunkLine("del", old_no, None, content))
            old_no += 1
        elif marker == " ":
            current.hunks.append(HunkLine("ctx", old_no, new_no, content))
            old_no += 1
            new_no += 1
        # anything else (e.g. "\ No newline at end of file") we just skip

    return files

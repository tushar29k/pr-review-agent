"""Versioned reviewer prompts: the instruction text lives in prompts/<version>/,
not inside the backends.

Why files instead of code: tweaking a prompt shouldn't need a code change,
and the A/B runner can enumerate every version on disk without touching
Python. v1 is the original prompt the four model backends used to carry as
identical copies; v2 is the reworked challenger.

Switch versions with REVIEWER_PROMPT_VERSION (default "v1") or the
prompt_version constructor arg on the model backends. A user template
formats with exactly three fields: file_path, new_flag (" (new file)" or
""), and diff (the rendered +/- hunks). The loader fails fast on a bad
template so a typo surfaces at startup, not as a silent wrong prompt.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .diff_parser import FileDiff

_ENV_VAR = "REVIEWER_PROMPT_VERSION"
_DIR_ENV_VAR = "REVIEWER_PROMPTS_DIR"  # override for tests / the A/B runner
DEFAULT_VERSION = "v1"

_cache: dict[str, tuple[str, str]] = {}


def prompts_dir() -> Path:
    custom = os.environ.get(_DIR_ENV_VAR)
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent / "prompts"


def versions() -> list[str]:
    # a version = a directory holding system.txt + user.txt
    d = prompts_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and (p / "system.txt").is_file()
                  and (p / "user.txt").is_file())


def current_version(explicit: str | None = None) -> str:
    # explicit constructor arg wins, then the env var, then v1. an unknown
    # version falls back to v1 with a loud note — a typo must never silently
    # review with the wrong instructions
    raw = (explicit if explicit is not None
           else os.environ.get(_ENV_VAR) or DEFAULT_VERSION).strip()
    if raw in versions():
        return raw
    print(f"unknown prompt version {raw!r} — falling back to {DEFAULT_VERSION!r}",
          file=sys.stderr)
    return DEFAULT_VERSION


def load(version: str | None = None) -> tuple[str, str]:
    """(system prompt, user template) for a version."""
    v = current_version(version)
    if v not in _cache:
        base = prompts_dir() / v
        system = (base / "system.txt").read_text(encoding="utf-8").strip()
        user = (base / "user.txt").read_text(encoding="utf-8").strip()
        # dry-run the format now: a missing placeholder raises here,
        # not halfway through a production review
        user.format(file_path="", new_flag="", diff="")
        _cache[v] = (system, user)
    return _cache[v]


def system_prompt(version: str | None = None) -> str:
    return load(version)[0]


def render_user(f: FileDiff, version: str | None = None) -> str:
    # the diff rendering is mechanical formatting, shared by all versions —
    # only the instruction text and the framing around the diff are versioned
    lines = []
    for h in f.hunks:
        marker = {"add": "+", "del": "-", "ctx": " "}[h.kind]
        no = h.new_no if h.new_no is not None else h.old_no
        lines.append(f"{marker} {no}: {h.text}")
    new_flag = " (new file)" if f.is_new else ""
    return load(version)[1].format(file_path=f.path, new_flag=new_flag,
                                   diff="\n".join(lines))

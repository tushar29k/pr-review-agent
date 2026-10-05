"""Repo-level config: .pr-review.yaml in the reviewed repo's root.

Three knobs today:

    enabled_checks:
      - secrets
      - debug_leftovers
      - todos
    severity_thresholds:
      critical: 0.90
      warning: 0.60
    ignore:                # files matching these fnmatch patterns are skipped
      - vendor/*           # (replaces the built-in defaults when present;
      - docs/*             #  an empty list disables ignoring entirely)

enabled_checks lists the checks that run — anything not listed is skipped.
"llm" is the model pass (all backends). severity_thresholds overrides the
defaults in config.py (0.80 / 0.50): model confidences remap through these.
ignore lists fnmatch path patterns for vendored/generated files the review
skips (skipped files are reported, never silently dropped); omitted means
the defaults in reviewer/ignore.py. Files carrying generated-code markers
(@generated, "DO NOT EDIT"…) are always skipped, config or not.

Discovery order: explicit --config path > .pr-review.yaml in the repo root
> .pr-review.yaml in the cwd (running the cli from your repo root) >
built-in defaults.

Hand-rolled subset parser, stdlib only — the file only needs these three
shapes (string lists and a float mapping), and reviewer/github.py keeps
the same stdlib-only convention.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

CONFIG_FILENAME = ".pr-review.yaml"

# every check name a config may enable/disable
KNOWN_CHECKS = frozenset({
    "secrets", "debug_leftovers", "todos", "binary",
    "diff_size", "missing_tests", "llm",
})


@dataclass
class RepoConfig:
    # None = run everything / use the config.py defaults /
    # use the default ignore patterns in reviewer/ignore.py
    enabled_checks: frozenset[str] | None = None
    critical_threshold: float | None = None
    warning_threshold: float | None = None
    ignore_patterns: list[str] | None = None

    def check_enabled(self, name: str) -> bool:
        return self.enabled_checks is None or name in self.enabled_checks

    def thresholds(self) -> tuple[float | None, float | None]:
        return self.critical_threshold, self.warning_threshold


def _clamp01(value: str, where: str) -> float | None:
    # garbage threshold values shouldn't kill a review — skip the line
    try:
        v = float(value)
    except (TypeError, ValueError):
        print(f".pr-review.yaml: ignoring bad threshold {where}: {value!r}",
              file=sys.stderr)
        return None
    return min(1.0, max(0.0, v))


def parse_config_text(text: str) -> RepoConfig:
    """Parse just the three shapes we support: string lists under
    enabled_checks and ignore, and a float mapping under
    severity_thresholds. Anything else is ignored — a config file must
    never crash a review."""
    enabled: list[str] = []
    ignore: list[str] = []
    saw_ignore = False
    thresholds: dict[str, float] = {}
    section: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()  # comments and blank lines
        if not line.strip():
            continue
        if not raw.startswith((" ", "\t")):
            section = None
            stripped = line.rstrip().rstrip(":")
            if stripped == "enabled_checks" and line.rstrip().endswith(":"):
                section = "enabled"
            elif stripped == "severity_thresholds" and line.rstrip().endswith(":"):
                section = "thresholds"
            elif stripped == "ignore" and line.rstrip().endswith(":"):
                section = "ignore"
                saw_ignore = True  # an empty list means "ignore nothing"
            continue
        if section == "enabled" and line.strip().startswith("- "):
            enabled.append(line.strip()[2:].strip().strip("\"'"))
        elif section == "ignore" and line.strip().startswith("- "):
            ignore.append(line.strip()[2:].strip().strip("\"'"))
        elif section == "thresholds" and ":" in line:
            key, _, val = line.strip().partition(":")
            key, val = key.strip(), val.strip().strip("\"'")
            if key in ("critical", "warning"):
                v = _clamp01(val, key)
                if v is not None:
                    thresholds[key] = v
    enabled_checks = None
    if enabled:
        unknown = [c for c in enabled if c not in KNOWN_CHECKS]
        for c in unknown:
            print(f".pr-review.yaml: unknown check {c!r} — want one of "
                  f"{sorted(KNOWN_CHECKS)}", file=sys.stderr)
        enabled_checks = frozenset(c for c in enabled if c in KNOWN_CHECKS)
    return RepoConfig(
        enabled_checks=enabled_checks,
        critical_threshold=thresholds.get("critical"),
        warning_threshold=thresholds.get("warning"),
        ignore_patterns=ignore if saw_ignore else None,
    )


def load_config(path: str) -> RepoConfig:
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        print(f".pr-review.yaml: can't read {path} ({exc}) — using defaults",
              file=sys.stderr)
        return RepoConfig()
    return parse_config_text(text)


def _in_dir(directory: str) -> str | None:
    path = os.path.join(directory, CONFIG_FILENAME)
    return path if os.path.isfile(path) else None


def discover_config(config_path: str | None = None,
                    repo_root: str | None = None) -> RepoConfig:
    # explicit flag first, then the repo being reviewed, then the cwd —
    # a missing file everywhere just means defaults
    path = (config_path
            or (repo_root and _in_dir(repo_root))
            or _in_dir(os.getcwd()))
    if not path:
        return RepoConfig()
    return load_config(path)

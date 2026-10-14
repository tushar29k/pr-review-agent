#!/usr/bin/env python3
"""Tests for the history-aware check (reviewer/history.py + the wiring).

Builds a scratch git repo so the miner has a real log to chew on — no
fixtures to go stale, and the "historically buggy file gets a note"
done-when is exercised end to end through Reviewer.

Run:  python3 test_history.py
"""
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer import checks  # noqa: E402
from reviewer.checks import check_hotspots  # noqa: E402
from reviewer.diff_parser import FileDiff, parse_diff  # noqa: E402
from reviewer.history import hotspots, mine_git_log  # noqa: E402
from reviewer.repo_config import RepoConfig, parse_config_text  # noqa: E402
from reviewer.reviewer import Reviewer  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True,
                   capture_output=True,
                   env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})


def make_repo():
    d = tempfile.mkdtemp()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    # buggy.py: three separate bug-fix commits — the hotspot
    for i, msg in enumerate(["fix crash on empty input",
                             "hotfix: off-by-one in retry loop",
                             "fix: handle None timeout"]):
        with open(os.path.join(d, "buggy.py"), "a") as f:
            f.write(f"# change {i}\nx = {i}\n")
        _git(d, "add", "buggy.py")
        _git(d, "commit", "-qm", msg)
    # calm.py: one fix — below the default threshold
    with open(os.path.join(d, "calm.py"), "w") as f:
        f.write("y = 1\n")
    _git(d, "add", "calm.py")
    _git(d, "commit", "-qm", "fix typo in comment")
    # feature.py: no fixes at all
    with open(os.path.join(d, "feature.py"), "w") as f:
        f.write("z = 2\n")
    _git(d, "add", "feature.py")
    _git(d, "commit", "-qm", "add feature module")
    return d


DIFF = """\
diff --git a/buggy.py b/buggy.py
index 1111111..2222222 100644
--- a/buggy.py
+++ b/buggy.py
@@ -1,1 +1,2 @@
 x = 2
+y = 3
"""


def main():
    repo = make_repo()

    # miner counts bug-fix commits per file
    counts = mine_git_log(repo)
    assert counts["buggy.py"] == 3, counts
    assert counts["calm.py"] == 1, counts
    assert "feature.py" not in counts, counts

    # non-repo dir: no history, no crash
    assert mine_git_log(tempfile.mkdtemp()) == {}

    # threshold filter
    assert hotspots({"a.py": 3, "b.py": 2, "c.py": 9}, min_fixes=3) == \
        {"a.py": 3, "c.py": 9}

    # the check fires on the hotspot...
    f = FileDiff(path="buggy.py", old_path="buggy.py")
    findings = check_hotspots(f, {"buggy.py": 3})
    assert len(findings) == 1, findings
    fd = findings[0]
    assert fd["check"] == "history" and fd["severity"] == "info"
    assert fd["file"] == "buggy.py" and fd["line"] is None
    assert "3 bug-fix" in fd["message"], fd["message"]

    # ...and stays quiet without history or for clean files
    assert check_hotspots(f, None) == []
    assert check_hotspots(f, {"other.py": 5}) == []

    # end to end through Reviewer
    findings = Reviewer(hotspots={"buggy.py": 3}).review(DIFF)
    assert any(f["check"] == "history" and f["file"] == "buggy.py"
               for f in findings), findings
    findings = Reviewer().review(DIFF)
    assert not any(f["check"] == "history" for f in findings), findings

    # enabled_checks can still switch it off
    cfg = RepoConfig(enabled_checks=frozenset({"secrets"}))
    findings = checks.run_all(parse_diff(DIFF), cfg.enabled_checks, None,
                              {"buggy.py": 3})
    assert not any(f["check"] == "history" for f in findings), findings

    # config: history section, bad values warn-and-ignore
    assert parse_config_text("history:\n  min_fixes: 5\n") \
        .history_threshold() == 5
    assert RepoConfig().history_threshold() == 3
    err = io.StringIO()
    with redirect_stderr(err):
        cfg = parse_config_text("history:\n  min_fixes: lots\n")
    assert cfg.history_threshold() == 3
    assert "ignoring bad history" in err.getvalue()

    print("test_history OK")


if __name__ == "__main__":
    main()

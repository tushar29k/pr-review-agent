#!/usr/bin/env python3
"""Tree-sitter static checks test: unused imports and dead code in Python.

Proves the done-when for "Tree-sitter checks: unused imports, dead code":
  1. an unused import in a new .py file is flagged (and only that one)
  2. used imports, __future__ imports, and TYPE_CHECKING imports are not
  3. code after return/raise in the same block is flagged as dead
  4. clean code and non-Python files stay quiet
  5. unused-import detection stays off for modified files (usage could
     live outside the hunks — the check refuses to guess)
  6. dead code still fires on modified files when terminator and dead
     lines are both added with no hidden lines between them

Run:  python3 test_tree_sitter.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reviewer.tree_sitter_checks import (  # noqa: E402
    check_dead_code, check_unused_imports)

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


def new_file_diff(path, body):
    lines = "".join(f"+{ln}\n" for ln in body.splitlines())
    n = len(body.splitlines())
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{n} @@\n{lines}")


def mod_file_diff(path, body):
    # body: list of (marker, text) with marker in "+- "
    hunks, new_no = [], 1
    for marker, text in body:
        hunks.append(f"{marker}{text}")
        if marker in "+ ":
            new_no += 1
    n_new = sum(1 for m, _ in body if m in "+ ")
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1,{len(body)} +1,{n_new} @@\n" + "\n".join(hunks) + "\n")


from reviewer.diff_parser import parse_diff  # noqa: E402


def files(diff):
    return parse_diff(diff)


print("== unused imports: flags the unused one, spares the rest ==")
d = new_file_diff("u.py", "import json\nimport re\n\nprint(re.compile('x'))")
fs = files(d)
ui = check_unused_imports(fs[0])
check("one finding for the unused import",
      len(ui) == 1 and ui[0]["line"] == 1
      and ui[0]["check"] == "unused_imports"
      and "json" in ui[0]["message"])
check("used import (re) not flagged",
      not any("`re`" in f["message"] for f in ui))

print("== import shapes: aliases, from-imports, __future__, TYPE_CHECKING ==")
d = new_file_diff(
    "u.py",
    "from __future__ import annotations\n"
    "import numpy as np\n"
    "from collections import defaultdict, Counter as C\n"
    "from x import *\n"
    "from typing import TYPE_CHECKING\n"
    "if TYPE_CHECKING:\n"
    "    import only_for_hints\n"
    "print(np.zeros(1))")
ui = check_unused_imports(files(d)[0])
msgs = " ".join(f["message"] for f in ui)
check("used aliased import (np) not flagged", "`np`" not in msgs)
check("from-imported defaultdict flagged", "`defaultdict`" in msgs)
check("aliased Counter (C) flagged", "`C`" in msgs)
check("star import never flagged", "*" not in msgs)
check("__future__ never flagged", "annotations" not in msgs)
check("TYPE_CHECKING import never flagged", "only_for_hints" not in msgs)
check("TYPE_CHECKING itself is used, not flagged",
      not any("TYPE_CHECKING`" in f["message"] for f in ui))

print("== dead code: statements after return/raise ==")
d = new_file_diff(
    "d.py",
    "def f(x):\n"
    "    if x:\n"
    "        return 1\n"
    "    return 2\n"
    "    y = 3\n"
    "    return y\n"
    "def g():\n"
    "    raise ValueError('bad')\n"
    "    print('never')\n"
    "print('fine')")
dc = check_dead_code(files(d)[0])
got = sorted(f["line"] for f in dc)
check("dead lines flagged (5, 6, 9)", got == [5, 6, 9])
check("all dead_code check name", all(f["check"] == "dead_code" for f in dc))
check("reachable code not flagged", 10 not in got)

print("== clean code stays quiet ==")
d = new_file_diff("c.py", "import os\n\nprint(os.getcwd())\n\n\ndef f():\n    return 1\n")
fs = files(d)[0]
check("no unused imports", check_unused_imports(fs) == [])
check("no dead code", check_dead_code(fs) == [])

print("== non-Python files are skipped ==")
d = new_file_diff("app.js", "import x from 'y';\n\nfunction f() {\n  return 1;\n  console.log('dead');\n}")
fs = files(d)[0]
check("js: no unused imports", check_unused_imports(fs) == [])
check("js: no dead code", check_dead_code(fs) == [])

print("== modified files: unused imports stay off, dead code still works ==")
d = mod_file_diff("m.py", [
    (" ", "import json"),
    (" ", "import re"),
    (" ", ""),
    (" ", "def f():"),
    ("+", "    return 1"),
    ("+", "    x = 2"),
    (" ", "    return x"),
])
f = files(d)[0]
check("unused-import check skips modified files",
      check_unused_imports(f) == [])
dc = check_dead_code(f)
check("dead code fires on added lines in a modified file",
      len(dc) == 1 and dc[0]["line"] == 6)

print("== terminator separated by hidden lines: no guess ==")
d = mod_file_diff("h.py", [
    (" ", "def f():"),
    ("+", "    return 1"),
])
# second hunk far below in the new file — simulate the gap with ctx numbering
d2 = (f"diff --git a/h.py b/h.py\n--- a/h.py\n+++ b/h.py\n"
      "@@ -1,2 +1,3 @@\n def f():\n+    return 1\n"
      "@@ -50,3 +51,4 @@\n x = 1\n+    y = 2\n z = 3\n")
f = files(d2)[0]
check("no dead-code finding across a hunk gap",
      check_dead_code(f) == [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

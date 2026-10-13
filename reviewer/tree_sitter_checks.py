"""Tree-sitter static checks for Python diffs: unused imports, dead code,
complexity smells.

Regexes can't see structure — tree-sitter can, so an import is "used" only
when its bound name actually appears as a reference, "dead" means a
statement sitting after a return inside the same block, and "complex" means
a function whose body is too long or nests too deep to hold in your head.
stdlib ast would do for whole files, but a diff only shows fragments;
tree-sitter's error tolerance means a hunk still parses instead of raising.

Honest scoping, because a diff isn't the whole file:
- unused imports run only on new files — on a modified file the import
  could be used by code outside the hunks, and we refuse to guess.
- dead code is local: a statement after return/raise/continue/break in the
  same block is dead no matter what the rest of the file looks like. the
  terminator and the dead statement must both be added lines with no hidden
  lines between them, so dropped context between hunks can't fake the
  block structure.
- complexity runs only on new files too — a function's full body has to
  be visible to measure its length; on a modified file it could start
  outside the hunks, and half a function tells you nothing.
"""

from __future__ import annotations

from .diff_parser import FileDiff

try:
    from tree_sitter import Language, Parser
    import tree_sitter_python
    _TS = True
except ImportError:  # pragma: no cover — dev installs carry tree-sitter
    _TS = False

_TERMINATORS = {"return_statement", "raise_statement",
                "continue_statement", "break_statement"}
_CONTAINERS = {"module", "block"}  # statement sequences live directly under these
# statements that open a new nesting level for everything inside them
_NESTERS = {"if_statement", "for_statement", "while_statement",
            "try_statement", "with_statement", "function_definition",
            "class_definition", "match_statement"}


def _parser():
    if not _TS:
        return None
    if not hasattr(_parser, "p"):
        _parser.p = Parser(Language(tree_sitter_python.language()))
    return _parser.p


def _view(f: FileDiff):
    """(new_no, group, kind, text) for every ctx/add line, in file order.

    group marks runs of lines with nothing hidden between them: a jump in
    new_no means unchanged lines the diff didn't show, so block structure
    across the jump can't be trusted. within one group the view is exact.
    """
    rows = []
    group = 0
    prev_no = None
    for h in f.hunks:
        if h.kind in ("add", "ctx"):
            if prev_no is not None and h.new_no != prev_no + 1:
                group += 1  # hidden lines between — nesting may differ
            rows.append((h.new_no, group, h.kind, h.text))
            prev_no = h.new_no
    return rows


def _has_ancestor(node, types):
    n = node.parent
    while n is not None:
        if n.type in types:
            return True
        n = n.parent
    return False


def _in_type_checking(node):
    # if TYPE_CHECKING: / if typing.TYPE_CHECKING: — imports there feed
    # string annotations tree-sitter can't see, so never flag them
    n = node.parent
    while n is not None:
        if n.type == "if_statement":
            cond = n.child_by_field_name("condition")
            if cond and "TYPE_CHECKING" in cond.text.decode("utf8", "ignore"):
                return True
        n = n.parent
    return False


def _imported_names(node):
    """Bound names an import statement introduces: import a.b -> a,
    import a as b -> b, from x import y as z -> z. star imports bind
    nothing we can track — skipped."""
    names = []
    if node.type == "import_statement":
        for child in node.children:
            if child.type == "aliased_import":
                alias = child.child_by_field_name("alias")
                names.append(alias.text.decode("utf8"))
            elif child.type == "dotted_name":
                first = child.children[0].text.decode("utf8")
                names.append(first)  # import a.b binds a
    elif node.type == "import_from_statement":
        mod = node.child_by_field_name("module_name")
        if mod and mod.text == b"__future__":
            return []  # compiler directives, not imports
        for child in node.children:
            if child is mod:
                continue  # the module path isn't a bound name
            if child.type == "wildcard_import":
                return []  # from x import * — can't track, don't guess
            if child.type == "aliased_import":
                alias = child.child_by_field_name("alias")
                names.append(alias.text.decode("utf8"))
            elif child.type == "dotted_name":
                names.append(child.text.decode("utf8"))
    return names


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def check_unused_imports(f: FileDiff) -> list[dict]:
    """Imported names never referenced anywhere in a new Python file."""
    if not f.path.endswith(".py"):
        return []
    if not f.is_new:
        return []  # usage may live outside the hunks — don't guess
    p = _parser()
    if p is None:
        return []  # tree-sitter unavailable: stay silent, never half-check
    rows = _view(f)
    tree = p.parse("\n".join(r[3] for r in rows).encode("utf8"))
    root = tree.root_node

    imports = []  # (name, view_row)
    for node in _walk(root):
        if node.type in ("import_statement", "import_from_statement"):
            if _in_type_checking(node):
                continue
            row = node.start_point[0]
            for name in _imported_names(node):
                imports.append((name, row))

    used = set()
    for node in _walk(root):
        if node.type == "identifier" and not _has_ancestor(
                node, ("import_statement", "import_from_statement")):
            used.add(node.text.decode("utf8", "ignore"))

    out = []
    for name, row in imports:
        if name not in used:
            out.append({
                "severity": "warning", "file": f.path,
                "line": rows[row][0], "check": "unused_imports",
                "confidence": 1.0,
                "message": f"Unused import: `{name}` is imported but never "
                           "referenced — dead weight on every reader.",
            })
    return out


def _dead(f: FileDiff, line: int, terminator: str) -> dict:
    kind = terminator.replace("_statement", "")
    return {
        "severity": "warning", "file": f.path,
        "line": line, "check": "dead_code",
        "confidence": 1.0,
        "message": f"Unreachable code after `{kind}` — it never "
                   "runs. Delete it or move it above.",
    }


def _nest_depth(node, current):
    # deepest statement nesting under node — each control statement
    # adds one level for everything inside it. flat function = 0.
    deepest = current
    for child in node.children:
        if child.type in _NESTERS:
            deepest = max(deepest, _nest_depth(child, current + 1))
        else:
            deepest = max(deepest, _nest_depth(child, current))
    return deepest


def check_complexity(f: FileDiff, max_lines=None,
                     max_depth=None) -> list[dict]:
    """Long functions and deep nesting in new Python files.

    Same honest scoping as unused imports: new files only. A function's
    full body has to be visible to measure its length — on a modified
    file the body could start outside the hunks, and we refuse to guess
    at half a function. Length counts the body block's line span;
    depth counts nested control statements around the deepest statement.
    """
    if not f.path.endswith(".py"):
        return []
    if not f.is_new:
        return []  # half a function tells you nothing — don't guess
    if max_lines is None or max_depth is None:
        from .config import MAX_FUNCTION_LINES, MAX_NESTING_DEPTH
        max_lines = MAX_FUNCTION_LINES if max_lines is None else max_lines
        max_depth = MAX_NESTING_DEPTH if max_depth is None else max_depth
    p = _parser()
    if p is None:
        return []  # tree-sitter unavailable: stay silent, never half-check
    rows = _view(f)
    tree = p.parse("\n".join(r[3] for r in rows).encode("utf8"))

    out = []
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        body = node.child_by_field_name("body")
        if body is None or body.type != "block":
            continue  # one-liner def — can't be long by construction
        name = node.child_by_field_name("name").text.decode("utf8",
                                                             "ignore")
        line = rows[node.start_point[0]][0]
        lines = body.end_point[0] - body.start_point[0] + 1
        depth = _nest_depth(node, 0)
        if lines > max_lines:
            out.append({
                "severity": "info", "file": f.path, "line": line,
                "check": "complexity", "confidence": 1.0,
                "message": f"Long function: `{name}` spans {lines} lines "
                           f"(limit {max_lines}) — it won't fit on one "
                           "screen. Extract a helper or two.",
            })
        if depth > max_depth:
            out.append({
                "severity": "info", "file": f.path, "line": line,
                "check": "complexity", "confidence": 1.0,
                "message": f"Deep nesting: `{name}` nests {depth} levels "
                           f"(limit {max_depth}) — flatten with early "
                           "returns or pull the inner loop out.",
            })
    return out


def check_dead_code(f: FileDiff) -> list[dict]:
    """Statements after return/raise/continue/break in the same block."""
    if not f.path.endswith(".py"):
        return []
    p = _parser()
    if p is None:
        return []
    rows = _view(f)
    added = {(r[0], r[1]) for r in rows if r[2] == "add"}
    if not added:
        return []
    tree = p.parse("\n".join(r[3] for r in rows).encode("utf8"))

    out = []
    for node in _walk(tree.root_node):
        if node.type not in _CONTAINERS:
            continue
        terminated = None  # (new_no, group, kind) of the terminator
        for stmt in node.children:
            if stmt.type == "comment":
                continue  # trailing comments ride along, not statements
            if stmt.type in _TERMINATORS:
                row = stmt.start_point[0]
                new_no, grp = rows[row][0], rows[row][1]
                # same-group rule: terminator and dead code both added with
                # no hidden lines between — the block structure is exact
                if (new_no, grp) in added:
                    if terminated is not None and grp == terminated[1]:
                        # an unreachable return is still dead code
                        out.append(_dead(f, new_no, terminated[2]))
                    terminated = (new_no, grp, stmt.type)
                continue
            if terminated is None:
                continue
            row = stmt.start_point[0]
            new_no, grp = rows[row][0], rows[row][1]
            if (new_no, grp) in added and grp == terminated[1]:
                out.append(_dead(f, new_no, terminated[2]))
    return out

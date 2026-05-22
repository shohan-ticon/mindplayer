"""
Static AST parser: source code -> structure tree.

Produces the same `structure_by_file` shape every consumer expects:
a flat list of nodes, each with id, kind, lineno, end_lineno, parent.

Also returns:
- triggers: per-line list of branch/loop events to emit when execution hits that line
- enclosing: per-line list of innermost-first branch/loop node ids
  (used to resolve parent_seq on every event)
"""
import ast


def build_structure(path, src):
    """Parse `src` (treated as `path`) and return (structure, triggers, enclosing)."""
    tree = ast.parse(src, filename=path)
    nodes = []
    triggers = {}   # body-first-line -> [{kind, node_id, label}]
    enclosing = {}  # line_no -> [outer, ..., inner]; reversed to innermost-first at end

    def first_line(stmts):
        return stmts[0].lineno if stmts else None

    def reg(line, kind, node_id, label):
        if line is not None:
            triggers.setdefault(line, []).append({"kind": kind, "node_id": node_id, "label": label})

    def mark(stmts, node_id):
        if not stmts:
            return
        lo, hi = stmts[0].lineno, stmts[-1].end_lineno
        if lo is None or hi is None:
            return
        for line in range(lo, hi + 1):
            enclosing.setdefault(line, []).append(node_id)

    def recurse(stmts, parent_id):
        # parent_id is the immediate structural parent (function / branch / loop).
        # Statements emit kind="statement" so consumers can render a node per line.
        for s in stmts:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nid = f"{parent_id}.{s.name}" if parent_id else s.name
                nodes.append({"id": nid, "kind": "function", "name": s.name,
                              "lineno": s.lineno, "end_lineno": s.end_lineno,
                              "parent": parent_id or None,
                              "params": [a.arg for a in s.args.args]})
                recurse(s.body, nid)
            elif isinstance(s, ast.If):
                nid = f"{parent_id}.if{s.lineno}"
                nodes.append({"id": nid, "kind": "branch", "subkind": "if",
                              "lineno": s.lineno, "end_lineno": s.end_lineno,
                              "parent": parent_id or None})
                reg(first_line(s.body), "branch", nid, "if")
                mark(s.body, nid)
                recurse(s.body, nid)
                orelse = s.orelse
                if orelse:
                    if len(orelse) == 1 and isinstance(orelse[0], ast.If):
                        recurse(orelse, parent_id)            # elif -> chained sibling If
                    else:
                        reg(first_line(orelse), "branch", nid, "else")
                        mark(orelse, nid)
                        recurse(orelse, nid)
            elif isinstance(s, (ast.For, ast.While)):
                sub = "for" if isinstance(s, ast.For) else "while"
                nid = f"{parent_id}.{sub}{s.lineno}"
                nodes.append({"id": nid, "kind": "loop", "subkind": sub,
                              "lineno": s.lineno, "end_lineno": s.end_lineno,
                              "parent": parent_id or None})
                reg(first_line(s.body), "loop", nid, "iteration")
                mark(s.body, nid)
                recurse(s.body, nid)
                recurse(s.orelse, nid)
            else:
                sid = f"{parent_id}.s{s.lineno}" if parent_id else f"s{s.lineno}"
                nodes.append({"id": sid, "kind": "statement",
                              "subkind": type(s).__name__,
                              "lineno": s.lineno, "end_lineno": s.end_lineno or s.lineno,
                              "parent": parent_id or None})
                for field in ("body", "orelse", "finalbody"):
                    b = getattr(s, field, None)
                    if isinstance(b, list):
                        recurse(b, parent_id)

    recurse(tree.body, "")
    enclosing = {ln: list(reversed(ids)) for ln, ids in enclosing.items()}
    structure = {"version": "0.1", "source_path": path, "nodes": nodes}
    return structure, triggers, enclosing

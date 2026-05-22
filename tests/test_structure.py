"""Unit tests for tracesnap._structure.build_structure."""
import textwrap

from tracesnap._structure import build_structure


def test_simple_function():
    src = textwrap.dedent("""
        def f(x):
            y = x + 1
            return y
    """).strip()
    structure, triggers, enclosing = build_structure("t.py", src)
    nodes = structure["nodes"]
    by_kind = {n["kind"] for n in nodes}
    assert "function" in by_kind
    assert "statement" in by_kind
    # f's body has two statements
    f_stmts = [n for n in nodes if n["kind"] == "statement" and n["parent"] == "f"]
    assert len(f_stmts) == 2


def test_for_loop_and_branch_emit_correct_parentage():
    src = textwrap.dedent("""
        def g(xs):
            total = 0
            for x in xs:
                if x > 0:
                    total = total + x
            return total
    """).strip()
    structure, triggers, enclosing = build_structure("t.py", src)
    nodes = structure["nodes"]
    by_id = {n["id"]: n for n in nodes}
    # The for loop is parented to the function.
    for_node = next(n for n in nodes if n["kind"] == "loop")
    assert for_node["parent"] == "g"
    # The if branch is parented to the for loop (not the function).
    branch = next(n for n in nodes if n["kind"] == "branch")
    assert branch["parent"] == for_node["id"]
    # The assign inside the if is parented to the branch.
    inner_assign = next(n for n in nodes
                        if n["kind"] == "statement" and n["parent"] == branch["id"])
    assert inner_assign["subkind"] == "Assign"


def test_enclosing_map_innermost_first():
    src = textwrap.dedent("""
        def h(xs):
            for x in xs:
                if x:
                    y = 1
    """).strip()
    _, _, enclosing = build_structure("t.py", src)
    # Line of `y = 1`: enclosing should list [if, for] (innermost first).
    # The exact line number depends on textwrap; find the if-body line.
    lines = src.split("\n")
    y_line = next(i+1 for i, ln in enumerate(lines) if "y = 1" in ln)
    chain = enclosing.get(y_line, [])
    assert len(chain) == 2
    assert "if" in chain[0]
    assert "for" in chain[1]


def test_redaction_default_set_present():
    from tracesnap.redaction import DEFAULT, is_redacted
    assert "password" in DEFAULT
    assert "api_key" in DEFAULT
    assert is_redacted("password")
    assert is_redacted("PASSWORD")
    assert not is_redacted("user")

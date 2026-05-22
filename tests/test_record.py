"""End-to-end tests of the record() API on a tiny program in a tmpfile."""
import json
import textwrap
from pathlib import Path

import tracesnap


SCRIPT = textwrap.dedent("""
    def add(a, b):
        result = a + b
        return result

    def caller():
        total = 0
        for x in [1, 2, 3]:
            total = add(total, x)
        return total

    out = caller()
""").lstrip()


def _write_script(tmp_path):
    p = tmp_path / "demo.py"
    p.write_text(SCRIPT)
    return p


def test_record_as_context_manager(tmp_path):
    script = _write_script(tmp_path)
    out_path = tmp_path / "trace.json"

    with tracesnap.record(trace_id="demo", source_files=[str(script)],
                          output=str(out_path)) as rec:
        code = compile(SCRIPT, str(script), "exec")
        exec(code, {"__name__": "__traced__", "__file__": str(script)})

    assert out_path.exists()
    assert rec.trace_id == "demo"
    assert rec.event_count > 0
    assert rec.trace["session"]["kind"] == "script"
    types = {e["type"] for e in rec.trace["events"]}
    assert {"call", "line", "assign", "loop", "return"} <= types


def test_parent_seq_inside_loop_matches_loop_event(tmp_path):
    script = _write_script(tmp_path)
    with tracesnap.record(trace_id="t", source_files=[str(script)], output=None) as rec:
        code = compile(SCRIPT, str(script), "exec")
        exec(code, {"__name__": "__traced__", "__file__": str(script)})

    events = rec.trace["events"]
    by_seq = {e["seq"]: e for e in events}
    # In caller(), `total = add(...)` is inside the for loop. Find its assign
    # event (in caller's frame -- second call event for `caller`).
    # Easiest: find any assign with var=='total' and change_index>=1 in caller.
    changed_totals = [e for e in events
                      if e["type"] == "assign" and e["var"] == "total" and e["change_index"] >= 1]
    assert changed_totals
    for e in changed_totals:
        parent = by_seq.get(e["parent_seq"])
        assert parent is not None
        assert parent["type"] == "loop"


def test_structure_by_file_present(tmp_path):
    script = _write_script(tmp_path)
    with tracesnap.record(trace_id="t", source_files=[str(script)], output=None) as rec:
        code = compile(SCRIPT, str(script), "exec")
        exec(code, {"__name__": "__traced__", "__file__": str(script)})

    sbf = rec.trace["session"]["structure_by_file"]
    assert str(script) in sbf
    nodes = sbf[str(script)]
    assert any(n["kind"] == "function" and n["name"] == "add" for n in nodes)
    assert any(n["kind"] == "loop" for n in nodes)


def test_write_then_load_roundtrip(tmp_path):
    script = _write_script(tmp_path)
    out = tmp_path / "out.json"
    with tracesnap.record(trace_id="t", source_files=[str(script)],
                          output=str(out)) as rec:
        code = compile(SCRIPT, str(script), "exec")
        exec(code, {"__name__": "__traced__", "__file__": str(script)})

    loaded = tracesnap.load_trace(str(out))
    assert loaded["trace_id"] == "t"
    assert loaded["events"][0]["type"] == "call"


def test_exception_event_captures_failure(tmp_path):
    src = textwrap.dedent("""
        def divide(a, b):
            return a / b
        def caller():
            x = 10
            y = 0
            return divide(x, y)
        result = caller()
    """).lstrip()
    script = tmp_path / "boom.py"
    script.write_text(src)

    try:
        with tracesnap.record(trace_id="t", source_files=[str(script)], output=None) as rec:
            code = compile(src, str(script), "exec")
            exec(code, {"__name__": "__traced__", "__file__": str(script)})
    except ZeroDivisionError:
        pass  # the script crashes; we still expect the trace to be saved.

    events = rec.trace["events"]
    excs = [e for e in events if e["type"] == "exception"]
    assert excs, "expected at least one exception event"
    # Deepest frame to raise is `divide` on the `return a / b` line.
    first = excs[0]
    assert first["exc_type"] == "ZeroDivisionError"
    assert "division by zero" in first["message"].lower()
    assert first["func"] == "divide"
    # Exception propagates through caller too.
    funcs_unwound = [e["func"] for e in excs]
    assert "caller" in funcs_unwound
    # The return events for both frames should be marked unwind=True
    # (i.e. not normal returns with a value).
    returns_unwind = [e for e in events
                      if e["type"] == "return" and e.get("unwind") is True]
    assert len(returns_unwind) >= 2


def test_redaction_via_record(tmp_path):
    src = textwrap.dedent("""
        def login(user, password, api_key):
            token = "tok-" + user
            return token
        login("nora", "hunter2", "k-123")
    """).lstrip()
    script = tmp_path / "auth.py"
    script.write_text(src)

    with tracesnap.record(trace_id="t", source_files=[str(script)], output=None) as rec:
        code = compile(src, str(script), "exec")
        exec(code, {"__name__": "__traced__", "__file__": str(script)})

    # Find the call event for login
    call_ev = next(e for e in rec.trace["events"] if e["type"] == "call" and e["func"] == "login")
    args = call_ev["args"]
    assert args["password"]["redacted"] is True
    assert args["api_key"]["redacted"] is True
    assert args["user"]["redacted"] is False
    # The `token` local assign is also redacted (it's in DEFAULT)
    token_assigns = [e for e in rec.trace["events"]
                     if e["type"] == "assign" and e["var"] == "token"]
    assert token_assigns
    assert token_assigns[0]["value"]["redacted"] is True

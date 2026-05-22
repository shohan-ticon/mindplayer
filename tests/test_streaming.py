"""Smoke test the interactive-record (SSE) endpoints."""
import json
import socket
import textwrap
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

import pytest

from tracesnap import library
from tracesnap.server import _make_handler, _stage_player_files


@pytest.fixture
def tmp_library(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACESNAP_HOME", str(tmp_path / "lib"))
    return tmp_path / "lib"


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _http(method, url, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, json.loads(raw or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture
def server(tmp_library, tmp_path):
    import http.server, socketserver
    static = tmp_path / "static"; static.mkdir()
    _stage_player_files(static)
    scan_root = tmp_path / "scripts"; scan_root.mkdir()
    port = _free_port()
    handler = _make_handler(str(static), scan_root=str(scan_root))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield {"base": f"http://127.0.0.1:{port}", "scan_root": scan_root}
    finally:
        httpd.shutdown(); httpd.server_close()


def _read_sse_events(url, want_exit=True, max_seconds=15):
    """Open an SSE stream and collect events until 'exit' (or timeout)."""
    out = []
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=max_seconds) as r:
        deadline = time.time() + max_seconds
        ev_type = None
        for line_bytes in r:
            if time.time() > deadline:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("event: "):
                ev_type = line[7:].strip()
            elif line.startswith("data: "):
                data_str = line[6:]
                try:
                    out.append((ev_type, json.loads(data_str)))
                except json.JSONDecodeError:
                    out.append((ev_type, {"raw": data_str}))
                if ev_type == "exit" and want_exit:
                    return out
            elif line == "":
                ev_type = None  # event boundary
    return out


def test_streaming_no_input_exits(server, tmp_library):
    """A trivial script with no input() finishes cleanly via the streaming flow."""
    script = server["scan_root"] / "trivial.py"
    script.write_text("print('hello')\nprint('bye')\n")
    status, body = _http("POST", server["base"] + "/api/run/script/start",
                         body={"path": str(script), "name": "trivial"})
    assert status == 200
    job_id = body["job_id"]
    events = _read_sse_events(server["base"] + "/api/run/script/stream/" + job_id)
    types = [t for t, _ in events]
    assert "exit" in types
    exit_data = next(d for t, d in events if t == "exit")
    assert exit_data["returncode"] == 0
    assert exit_data["new_id"] is not None


def test_streaming_interactive_input(server, tmp_library):
    """The script asks for input, we write to /stdin, the script reads it
    and exits successfully."""
    script = server["scan_root"] / "interactive.py"
    script.write_text(textwrap.dedent("""
        def main():
            who = input("name? ")
            return who.upper()
        out = main()
    """).lstrip())
    status, body = _http("POST", server["base"] + "/api/run/script/start",
                         body={"path": str(script), "name": "interactive"})
    job_id = body["job_id"]

    # Start the SSE consumer in a thread so we can talk to /stdin in parallel.
    events_holder = {}
    def consume():
        events_holder["events"] = _read_sse_events(
            server["base"] + "/api/run/script/stream/" + job_id, max_seconds=10)
    t = threading.Thread(target=consume, daemon=True)
    t.start()

    # Give the subprocess a moment to print the prompt, then send the reply.
    time.sleep(0.5)
    rs, rb = _http("POST", server["base"] + "/api/run/script/stdin/" + job_id,
                   body={"data": "Alice\n"})
    assert rs == 200
    assert rb["ok"] is True

    t.join(timeout=10)
    events = events_holder.get("events", [])
    types = [t for t, _ in events]
    assert "exit" in types, events
    exit_data = next(d for t, d in events if t == "exit")
    assert exit_data["returncode"] == 0
    assert exit_data["new_id"]
    meta, trace = library.get(exit_data["new_id"])
    reprs = {e["var"]: e["value"]["repr"]
             for e in trace["events"] if e["type"] == "assign"}
    # `who` is set inside main() and IS recorded; module-level `out = main()`
    # is intentionally skipped (recorder filters out <module> frames). Confirm
    # the script saw the input we sent by inspecting main's return value.
    assert reprs.get("who") == "'Alice'"
    ret_events = [e for e in trace["events"] if e["type"] == "return" and e.get("func") == "main"]
    assert ret_events and ret_events[0]["value"]["repr"] == "'ALICE'"


def test_streaming_kill(server, tmp_library):
    """A long-running script can be killed via /kill; subsequent exit event
    reports a non-zero returncode."""
    script = server["scan_root"] / "long.py"
    script.write_text("import time\nfor i in range(100): time.sleep(0.1)\n")
    body = _http("POST", server["base"] + "/api/run/script/start",
                 body={"path": str(script)})[1]
    job_id = body["job_id"]

    events_holder = {}
    def consume():
        events_holder["events"] = _read_sse_events(
            server["base"] + "/api/run/script/stream/" + job_id, max_seconds=10)
    t = threading.Thread(target=consume, daemon=True); t.start()

    time.sleep(0.3)
    rs, _ = _http("POST", server["base"] + "/api/run/script/kill/" + job_id)
    assert rs == 200
    t.join(timeout=5)
    events = events_holder.get("events", [])
    assert any(et == "exit" for et, _ in events)
    exit_data = next(d for et, d in events if et == "exit")
    assert exit_data["returncode"] != 0

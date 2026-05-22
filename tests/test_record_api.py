"""Tests for /api/sources, /api/run/script, /api/run/request."""
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
    (scan_root / "demo.py").write_text(textwrap.dedent("""
        def add(a, b):
            return a + b
        result = add(1, 2)
    """).lstrip())
    # also a noisy file under a skipped dir
    skip = scan_root / "__pycache__"; skip.mkdir()
    (skip / "noise.py").write_text("# should not be discovered")

    port = _free_port()
    handler = _make_handler(str(static), scan_root=str(scan_root))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield {
            "base": f"http://127.0.0.1:{port}",
            "scan_root": str(scan_root),
            "demo_path": str(scan_root / "demo.py"),
        }
    finally:
        httpd.shutdown(); httpd.server_close()


def test_sources_lists_py_files_and_skips_noise(server):
    status, body = _http("GET", server["base"] + "/api/sources")
    assert status == 200
    paths = [f["rel"] for f in body["files"]]
    assert "demo.py" in paths
    # skipped dir contents excluded
    assert not any("__pycache__" in p for p in paths)
    assert body["cwd"] == server["scan_root"]


def test_run_script_creates_library_entry(server, tmp_library):
    before = library.list_traces()
    assert before == []
    status, body = _http("POST", server["base"] + "/api/run/script",
                         body={"path": server["demo_path"], "name": "from-browser"})
    assert status == 200
    assert body["returncode"] == 0
    assert body["new_id"]
    after = library.list_traces()
    assert len(after) == 1
    assert after[0]["name"] == "from-browser"


def test_run_script_missing_path_returns_400(server):
    status, _ = _http("POST", server["base"] + "/api/run/script",
                      body={"path": "/nope/does/not/exist.py"})
    assert status == 400


def test_run_request_proxies_through(server):
    # Use this same server as a self-target — GET / serves a directory listing
    # from the staged static dir, which is fine for proving the proxy works.
    status, body = _http("POST", server["base"] + "/api/run/request",
                         body={"method": "GET", "url": server["base"] + "/player.html"})
    assert status == 200
    assert body["status"] == 200
    assert "<!DOCTYPE html>" in body["body"] or "<!doctype html>" in body["body"]


def test_run_request_handles_bad_url(server):
    status, body = _http("POST", server["base"] + "/api/run/request",
                         body={"method": "GET", "url": "http://127.0.0.1:1/no-server"})
    # Connection refused -> URLError path -> ok=false but the API still 200's
    assert status == 200
    assert body["ok"] is False


def test_run_script_passes_stdin(server, tmp_library, tmp_path):
    # Drop a script that reads from input() into the scan root.
    script = Path(server["scan_root"]) / "needs_stdin.py"
    script.write_text(textwrap.dedent("""
        def collect():
            who = input("name? ")
            num = int(input("age? "))
            return (who, num)
        out = collect()
    """).lstrip())
    status, body = _http("POST", server["base"] + "/api/run/script",
                         body={"path": str(script),
                               "stdin": "Alice\n42\n",
                               "name": "with-stdin",
                               "timeout": 30})
    assert status == 200, body
    assert body["timed_out"] is False
    assert body["returncode"] == 0, body["log"]
    assert body["new_id"]
    # Verify the recorded trace has Alice / 42 captured as assigns.
    meta, trace = library.get(body["new_id"])
    assigns = [e for e in trace["events"] if e["type"] == "assign"]
    reprs = {e["var"]: e["value"]["repr"] for e in assigns}
    assert reprs.get("who") == "'Alice'"
    assert reprs.get("num") == "42"


def test_run_script_eof_when_stdin_empty(server, tmp_library):
    """No stdin + input() in the script => EOFError; subprocess returns
    non-zero but DOESN'T hang (the user's original complaint)."""
    script = Path(server["scan_root"]) / "needs_stdin_unanswered.py"
    script.write_text("x = input()\n")
    status, body = _http("POST", server["base"] + "/api/run/script",
                         body={"path": str(script), "timeout": 10})
    assert status == 200
    assert body["timed_out"] is False
    assert body["returncode"] != 0
    assert "EOFError" in body["log"] or "EOF" in body["log"]


def test_run_script_timeout_returns_clean_error(server, tmp_library):
    """Script that genuinely blocks (sleep) is killed at the timeout."""
    script = Path(server["scan_root"]) / "blocks_forever.py"
    script.write_text("import time\ntime.sleep(30)\n")
    status, body = _http("POST", server["base"] + "/api/run/script",
                         body={"path": str(script), "timeout": 2})
    assert status == 200
    assert body["timed_out"] is True
    assert body["returncode"] != 0
    assert "killed" in body["log"].lower() or "timed out" in body["log"].lower()

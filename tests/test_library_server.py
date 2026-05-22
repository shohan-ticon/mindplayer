"""Smoke test the /api/traces HTTP surface end-to-end."""
import json
import os
import socket
import threading
import urllib.request
import urllib.error

import pytest

from tracesnap import library
from tracesnap.server import _make_handler, _stage_player_files


@pytest.fixture
def tmp_library(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACESNAP_HOME", str(tmp_path / "lib"))
    return tmp_path / "lib"


def _fake_trace(name="demo", events=4):
    return {
        "version": "0.1",
        "trace_id": name,
        "session": {"kind": "script", "entry": "demo.py:<module>", "source": "x=1\n"},
        "events": [{"seq": i, "type": "line", "line": 1} for i in range(events)],
    }


def _http(method, url, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def running_server(tmp_library, tmp_path):
    """Start the handler on a free port, yield base url, stop on teardown."""
    import http.server
    import socketserver

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    _stage_player_files(static_dir)

    port = _free_port()
    handler = _make_handler(str(static_dir))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_api_list_empty(running_server):
    status, body = _http("GET", running_server + "/api/traces")
    assert status == 200
    assert body == []


def test_api_list_after_add(running_server, tmp_library):
    m = library.add(_fake_trace())
    status, body = _http("GET", running_server + "/api/traces")
    assert status == 200
    assert len(body) == 1
    assert body[0]["id"] == m["id"]


def test_api_get_returns_trace(running_server, tmp_library):
    m = library.add(_fake_trace())
    status, body = _http("GET", running_server + f"/api/traces/{m['id']}")
    assert status == 200
    assert body["trace_id"] == "demo"
    assert len(body["events"]) == 4


def test_api_get_unknown_404(running_server):
    status, _ = _http("GET", running_server + "/api/traces/missing")
    assert status == 404


def test_api_patch_rename(running_server, tmp_library):
    m = library.add(_fake_trace(), name="orig")
    status, body = _http("PATCH", running_server + f"/api/traces/{m['id']}",
                         body={"name": "renamed"})
    assert status == 200
    assert body["name"] == "renamed"
    assert library.list_traces()[0]["name"] == "renamed"


def test_api_delete(running_server, tmp_library):
    m = library.add(_fake_trace())
    status, _ = _http("DELETE", running_server + f"/api/traces/{m['id']}")
    assert status == 200
    assert library.list_traces() == []


def test_api_serves_player_html(running_server):
    with urllib.request.urlopen(running_server + "/player.html", timeout=2) as r:
        assert r.status == 200
        body = r.read()
    assert body.startswith(b"<!DOCTYPE html>") or body.startswith(b"<!doctype html>")

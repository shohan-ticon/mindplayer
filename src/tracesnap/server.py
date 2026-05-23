"""
Local http.server for `tracesnap view`.

Serves the bundled player HTMLs + a small JSON API over the on-disk library
plus subprocess-driven recording. Zero external deps (stdlib only).

Routes:
- `GET    /api/traces`              -> list of library metadata (newest first)
- `GET    /api/traces/<id>`         -> the trace JSON
- `PATCH  /api/traces/<id>`         -> `{name: "..."}` rename
- `DELETE /api/traces/<id>`         -> remove from library
- `GET    /api/sources`             -> `{cwd, files: [...]}` discovered .py files
- `POST   /api/run/script`          -> `{path, name, redact}` runs subprocess
                                       `tracesnap record ...`, returns new entry
- `POST   /api/run/request`         -> `{method, url, headers, body}` proxies
                                       to the target app, returns its response
"""
import http.server
import json
import os
import queue
import secrets
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

try:
    from importlib.resources import files as _resource_files
except ImportError:                               # pragma: no cover
    from importlib_resources import files as _resource_files

from . import library


_PLAYER_HTMLS = ("home.html", "player.html", "simulator.html", "call_graph.html",
                 "event_graph.html", "record.html")
_SCAN_IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
                     "dist", "build", ".tox", ".pytest_cache", ".mypy_cache",
                     ".idea", ".vscode"}


def _player_dir():
    return _resource_files("tracesnap.player")


def _stage_player_files(dst):
    """Copy all bundled player HTMLs into dst directory."""
    dst = Path(dst)
    for name in _PLAYER_HTMLS:
        src = _player_dir() / name
        with src.open("rb") as f:
            (dst / name).write_bytes(f.read())


def serve(trace_path=None, target_id=None, view=None,
          port=0, open_browser=True, scan_root=None):
    """Spin up the local server.

    - `trace_path` (optional): stage this file into the tmpdir and open
      `<view>.html?trace=<file>`.
    - `target_id` (optional): library record id. If set, the URL will be
      `<view>.html?id=<id>` so the player loads it via the API.
    - `scan_root`: dir the Script tab on the New-record page walks for
      `.py` files. Defaults to the current working directory.
    """
    view_to_html = {
        "home": "home.html",
        "text": "player.html",
        "simulator": "simulator.html",
        "graph": "call_graph.html",
        "call_graph": "call_graph.html",
        "event_graph": "event_graph.html",
        "events": "event_graph.html",
        "record": "record.html",
    }
    # Default: open the trace in the call graph if one was specified, otherwise
    # land on the TraceSnap home page so the user can browse the library.
    if view is None:
        view = "call_graph" if (target_id or trace_path) else "home"
    if view not in view_to_html:
        raise ValueError(f"unknown view: {view!r}. Pick one of {sorted(view_to_html)}")
    html_name = view_to_html[view]

    tmp = tempfile.mkdtemp(prefix="tracesnap-")
    tmpdir = Path(tmp)
    _stage_player_files(tmpdir)

    if scan_root is None:
        scan_root = os.getcwd()
    scan_root = str(Path(scan_root).resolve())

    query = ""
    if target_id:
        query = f"?id={target_id}"
    elif trace_path:
        trace_path = Path(trace_path).resolve()
        if not trace_path.exists():
            raise FileNotFoundError(f"trace not found: {trace_path}")
        shutil.copy(trace_path, tmpdir / trace_path.name)
        query = f"?trace={trace_path.name}"

    handler = _make_handler(str(tmpdir), scan_root=scan_root)
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler) as httpd:
        actual_port = httpd.server_address[1]
        url = f"http://127.0.0.1:{actual_port}/{html_name}{query}"
        print("tracesnap: serving the player library", file=sys.stderr)
        print(f"tracesnap: open {url}", file=sys.stderr)
        print(f"tracesnap: library at {library.library_root()}", file=sys.stderr)
        print(f"tracesnap: script scan root {scan_root}", file=sys.stderr)
        print("tracesnap: press Ctrl-C to stop", file=sys.stderr)

        if open_browser:
            threading.Timer(0.2, webbrowser.open, args=(url,)).start()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\ntracesnap: stopped", file=sys.stderr)


# ---------------------------------------------------------------------------
# Helpers for /api/run/*
# ---------------------------------------------------------------------------
def _discover_sources(root, *, limit=200):
    """List .py files under `root`, depth-limited, skipping noisy dirs.
    Returns a list of dicts: [{path, rel, size, mtime}, ...]."""
    out = []
    root_p = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root_p):
        # in-place prune
        dirnames[:] = [d for d in dirnames if d not in _SCAN_IGNORE_DIRS
                       and not d.startswith(".")]
        for f in filenames:
            if not f.endswith(".py"):
                continue
            full = Path(dirpath) / f
            try:
                stat = full.stat()
            except OSError:
                continue
            try:
                rel = str(full.relative_to(root_p))
            except ValueError:
                rel = str(full)
            out.append({
                "path": str(full),
                "rel": rel,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
            if len(out) >= limit:
                return out
    out.sort(key=lambda r: r["rel"])
    return out


def _run_record_subprocess(script_path, *, name=None, redact=None,
                           stdin=None, timeout=60, id_label="recorded"):
    """Run `tracesnap.cli record` as a subprocess.

    Returns (returncode, combined_log, timed_out).

    - `stdin`: string piped to the subprocess's stdin (one input() answer per
      line). Empty string by default so input() raises EOFError quickly
      instead of hanging forever.
    - `timeout`: seconds. On timeout the process is killed and we report a
      helpful error in the log.
    """
    cmd = [sys.executable, "-m", "tracesnap.cli", "record", script_path,
           "--id", id_label]
    if name:
        cmd += ["--name", name]
    if redact:
        cmd += ["--redact", redact]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              input=stdin or "", timeout=timeout)
        log = (proc.stderr or "") + "\n" + (proc.stdout or "")
        return proc.returncode, log, False
    except subprocess.TimeoutExpired as e:
        # e.stdout / e.stderr may be bytes when capture_output=True
        def _dec(b):
            if b is None:
                return ""
            return b.decode("utf-8", errors="replace") if isinstance(b, (bytes, bytearray)) else str(b)
        msg = (f"tracesnap: script did not finish within {timeout}s and was killed.\n"
               "If your script reads input(), supply each answer on its own line in "
               "the 'Stdin' field above, or increase the timeout.\n"
               "--- stderr ---\n" + _dec(e.stderr) +
               "\n--- stdout ---\n" + _dec(e.stdout))
        return -1, msg, True


# ---------------------------------------------------------------------------
# Streaming script jobs (interactive stdin via SSE)
# ---------------------------------------------------------------------------
_jobs = {}   # job_id -> _Job


class _Job:
    """A live `tracesnap.cli record ...` subprocess with stdin/stdout/stderr
    streamed to one SSE consumer. Each event goes on `events_q` as
    `(event_type, data_dict)`.
    """
    def __init__(self, path, *, name=None, redact=None, id_label="recorded"):
        self.id = secrets.token_hex(6)
        self.path = path
        self.name = name
        self.redact = redact
        self.id_label = id_label
        self.proc = None
        self.events_q = queue.Queue()
        self.alive = False
        self.exit_returncode = None
        self.added = []
        self.new_id = None
        self._before_ids = set()
        self.created_ts = time.time()

    def start(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        cmd = [sys.executable, "-u", "-m", "tracesnap.cli", "record",
               self.path, "--id", self.id_label]
        if self.name:
            cmd += ["--name", self.name]
        if self.redact:
            cmd += ["--redact", self.redact]
        self._before_ids = {m["id"] for m in library.list_traces()}
        # Binary mode + bufsize=0 means stdout/stderr/stdin are unbuffered
        # FileIO objects -- read() returns the moment data is available,
        # so `input("Enter a number: ")` (no newline) reaches the browser
        # the instant Python writes it.
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, bufsize=0,
        )
        self.alive = True
        threading.Thread(target=self._read_loop,
                         args=(self.proc.stdout, "out"), daemon=True).start()
        threading.Thread(target=self._read_loop,
                         args=(self.proc.stderr, "err"), daemon=True).start()
        threading.Thread(target=self._wait_loop, daemon=True).start()

    def _read_loop(self, stream, ev_type):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                self.events_q.put((ev_type, {"text": text}))
        except Exception as exc:                # noqa: BLE001
            self.events_q.put(("err", {"text": f"\n[stream error: {exc}]\n"}))

    def _wait_loop(self):
        rc = self.proc.wait()
        # Give read loops a chance to drain.
        time.sleep(0.05)
        after = library.list_traces()
        self.added = [m for m in after if m["id"] not in self._before_ids]
        self.new_id = self.added[0]["id"] if self.added else None
        self.exit_returncode = rc
        self.events_q.put(("exit", {
            "returncode": rc,
            "added": self.added,
            "new_id": self.new_id,
        }))
        self.alive = False

    def send_stdin(self, data):
        if self.proc is None or not self.alive:
            return False
        stdin = self.proc.stdin
        if stdin is None or stdin.closed:
            return False
        try:
            payload = data.encode("utf-8") if isinstance(data, str) else data
            stdin.write(payload)
            stdin.flush()
            echo = data if isinstance(data, str) else payload.decode("utf-8", errors="replace")
            self.events_q.put(("in", {"text": echo}))
            return True
        except (BrokenPipeError, ValueError, OSError):
            return False

    def close_stdin(self):
        if self.proc and self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def kill(self):
        if self.proc and self.alive:
            try:
                self.proc.kill()
            except OSError:
                pass


def _proxy_http(method, url, headers=None, body=None, timeout=30):
    """Server-side proxy: make the HTTP request and return a JSON-able
    summary of the response."""
    data = None
    if body is not None and body != "":
        data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}),
                                 method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return {
                "ok": True,
                "status": r.status,
                "reason": getattr(r, "reason", ""),
                "headers": dict(r.headers),
                "body": raw.decode("utf-8", errors="replace"),
                "body_size": len(raw),
            }
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        return {
            "ok": False,
            "status": e.code,
            "reason": e.reason if hasattr(e, "reason") else "",
            "headers": dict(e.headers) if hasattr(e, "headers") else {},
            "body": raw.decode("utf-8", errors="replace") if raw else "",
            "body_size": len(raw),
        }
    except urllib.error.URLError as e:
        return {"ok": False, "status": 0, "reason": str(e.reason),
                "headers": {}, "body": "", "body_size": 0,
                "error": str(e)}
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "status": 0, "reason": "exception",
                "headers": {}, "body": "", "body_size": 0,
                "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# HTTP handler with /api/traces routes overlaid on static file serving
# ---------------------------------------------------------------------------
def _make_handler(directory, *, scan_root=None):
    class Handler(http.server.SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, fmt, *args):
            if args and isinstance(args[0], str) and args[0].startswith(("4", "5")):
                super().log_message(fmt, *args)

        def end_headers(self):
            # Make sure the browser always gets the freshest player HTML — the
            # tmpdir on disk is the source of truth for this run.
            if self.path.endswith(".html"):
                self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

        # ---- HTTP method dispatch ----
        def do_GET(self):
            api = self._match_api()
            if api is not None:
                self._handle_api(api, "GET")
                return
            # Root URL lands on the TraceSnap home page.
            path_only = self.path.split("?", 1)[0]
            if path_only in ("/", "/index.html"):
                self.send_response(302)
                self.send_header("Location", "/home.html")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            super().do_GET()

        def do_POST(self):
            api = self._match_api()
            if api is None:
                self.send_error(404)
                return
            self._handle_api(api, "POST")

        def do_PATCH(self):
            api = self._match_api()
            if api is None:
                self.send_error(404)
                return
            self._handle_api(api, "PATCH")

        def do_DELETE(self):
            api = self._match_api()
            if api is None:
                self.send_error(404)
                return
            self._handle_api(api, "DELETE")

        # ---- routing ----
        def _match_api(self):
            path = self.path.split("?", 1)[0]
            if not path.startswith("/api/"):
                return None
            if path == "/api/sources":
                return {"kind": "sources"}
            if path == "/api/run/script":
                return {"kind": "run_script"}
            if path == "/api/run/request":
                return {"kind": "run_request"}
            if path == "/api/run/script/start":
                return {"kind": "job_start"}
            if path.startswith("/api/run/script/stream/"):
                return {"kind": "job_stream", "id": path.split("/")[-1]}
            if path.startswith("/api/run/script/stdin/"):
                return {"kind": "job_stdin", "id": path.split("/")[-1]}
            if path.startswith("/api/run/script/eof/"):
                return {"kind": "job_eof", "id": path.split("/")[-1]}
            if path.startswith("/api/run/script/kill/"):
                return {"kind": "job_kill", "id": path.split("/")[-1]}
            if path.startswith("/api/traces"):
                rest = path[len("/api/traces"):]
                if rest in ("", "/"):
                    return {"kind": "traces_collection"}
                if rest.startswith("/"):
                    id_ = rest[1:]
                    if id_:
                        return {"kind": "traces_item", "id": id_}
            return None

        def _send_json(self, payload, status=200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_sse_stream(self, job):
            """Open an SSE response and forward the job's events until exit."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                done = False
                while not done:
                    try:
                        ev_type, data = job.events_q.get(timeout=2.0)
                    except queue.Empty:
                        # Heartbeat keeps the connection (and any proxies) alive.
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    payload = (f"event: {ev_type}\n"
                               f"data: {json.dumps(data)}\n\n").encode("utf-8")
                    self.wfile.write(payload)
                    self.wfile.flush()
                    if ev_type == "exit":
                        done = True
            except (BrokenPipeError, ConnectionResetError, OSError):
                # client went away; that's fine, the subprocess keeps running.
                return

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}

        # ---- API handlers ----
        def _handle_api(self, api, method):
            kind = api["kind"]
            if kind == "traces_collection" and method == "GET":
                self._send_json(library.list_traces())
                return
            if kind == "traces_item" and method == "GET":
                meta, trace = library.get(api["id"])
                if trace is None:
                    self.send_error(404, "trace not in library")
                    return
                self._send_json(trace)
                return
            if kind == "traces_item" and method == "PATCH":
                body = self._read_json_body()
                updated = library.rename(api["id"], body.get("name", ""))
                if updated is None:
                    self.send_error(404)
                    return
                self._send_json(updated)
                return
            if kind == "traces_item" and method == "DELETE":
                if not library.delete(api["id"]):
                    self.send_error(404)
                    return
                self._send_json({"ok": True})
                return
            if kind == "sources" and method == "GET":
                root = scan_root or os.getcwd()
                self._send_json({"cwd": root, "files": _discover_sources(root)})
                return
            if kind == "run_script" and method == "POST":
                body = self._read_json_body()
                path = body.get("path")
                if not path or not os.path.isfile(path):
                    self.send_error(400, "missing or non-existent 'path'")
                    return
                # Snapshot library ids before/after so we can find what was added.
                before = {m["id"] for m in library.list_traces()}
                try:
                    timeout = max(1, min(600, int(body.get("timeout") or 60)))
                except (TypeError, ValueError):
                    timeout = 60
                rc, log, timed_out = _run_record_subprocess(
                    path,
                    name=(body.get("name") or "").strip() or None,
                    redact=(body.get("redact") or "").strip() or None,
                    stdin=body.get("stdin") or "",
                    timeout=timeout,
                    id_label=(body.get("id") or "").strip() or "recorded",
                )
                after = library.list_traces()
                added = [m for m in after if m["id"] not in before]
                self._send_json({
                    "returncode": rc,
                    "log": log,
                    "timed_out": timed_out,
                    "added": added,
                    "new_id": added[0]["id"] if added else None,
                })
                return
            if kind == "job_start" and method == "POST":
                body = self._read_json_body()
                path = body.get("path")
                if not path or not os.path.isfile(path):
                    self.send_error(400, "missing or non-existent 'path'")
                    return
                job = _Job(path,
                           name=(body.get("name") or "").strip() or None,
                           redact=(body.get("redact") or "").strip() or None,
                           id_label=(body.get("id") or "").strip() or "recorded")
                _jobs[job.id] = job
                job.start()
                self._send_json({"job_id": job.id})
                return
            if kind == "job_stream" and method == "GET":
                job = _jobs.get(api["id"])
                if job is None:
                    self.send_error(404)
                    return
                self._send_sse_stream(job)
                return
            if kind == "job_stdin" and method == "POST":
                job = _jobs.get(api["id"])
                if job is None:
                    self.send_error(404)
                    return
                body = self._read_json_body()
                data = body.get("data", "")
                if not isinstance(data, str):
                    self.send_error(400, "stdin data must be a string")
                    return
                ok = job.send_stdin(data)
                self._send_json({"ok": ok, "alive": job.alive})
                return
            if kind == "job_eof" and method == "POST":
                job = _jobs.get(api["id"])
                if job is None:
                    self.send_error(404)
                    return
                job.close_stdin()
                self._send_json({"ok": True})
                return
            if kind == "job_kill" and method == "POST":
                job = _jobs.get(api["id"])
                if job is None:
                    self.send_error(404)
                    return
                job.kill()
                self._send_json({"ok": True})
                return
            if kind == "run_request" and method == "POST":
                body = self._read_json_body()
                url = body.get("url")
                if not url:
                    self.send_error(400, "missing 'url'")
                    return
                # Snapshot the library so we can spot the new trace (if any).
                before = {m["id"] for m in library.list_traces()}
                resp = _proxy_http(
                    method=body.get("method", "GET"),
                    url=url,
                    headers=body.get("headers") or {},
                    body=body.get("body"),
                )
                after = library.list_traces()
                added = [m for m in after if m["id"] not in before]
                resp["added"] = added
                resp["new_id"] = added[0]["id"] if added else None
                self._send_json(resp)
                return
            self.send_error(405, "method not allowed")

    return Handler

"""
Flask integration.

    from flask import Flask
    from tracesnap.integrations.flask import TraceSnap

    app = Flask(__name__)
    TraceSnap(app, output_dir="traces")

Every request gets recorded; the trace lands in `traces/<endpoint>-<seq>.json`.
The hooks are named with the `_recorder_` prefix so the recorder skips
them (they never appear in the trace itself).
"""
import os
import time
from pathlib import Path

try:
    from flask import g, request
except ImportError as exc:  # pragma: no cover
    raise ImportError("Flask is not installed. Try: pip install tracesnap[flask]") from exc

from .._recorder import start_recording, stop_recording
from ..api import write_trace


class TraceSnap:
    def __init__(self, app=None, *, output_dir="traces", trace_id_prefix="req",
                 enabled=None, source_files=None, redact_names=None):
        self.output_dir = Path(output_dir)
        self.trace_id_prefix = trace_id_prefix
        self.enabled = enabled or (lambda req: True)
        self.source_files = source_files
        self.redact_names = redact_names
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        app.before_request(self._recorder_begin)
        app.after_request(self._recorder_capture_status)
        app.teardown_request(self._recorder_end)
        app.extensions = getattr(app, "extensions", {})
        app.extensions["tracesnap"] = self

        # If source_files wasn't given, default to the app's module file.
        if self.source_files is None:
            mod_file = getattr(app, "root_path", None)
            if mod_file:
                main_mod = os.path.join(mod_file, "__init__.py")
                if os.path.exists(main_mod):
                    self.source_files = [main_mod]

    def _resolve_source_files(self):
        if self.source_files:
            return list(self.source_files)
        # Fallback: the file that imported flask.request.endpoint's view function.
        try:
            from flask import current_app
            view_func = current_app.view_functions.get(request.endpoint)
            if view_func and view_func.__code__:
                return [view_func.__code__.co_filename]
        except Exception:
            pass
        return []

    def _recorder_begin(self):
        if not self.enabled(request):
            g._tracesnap_skip = True
            return
        g._tracesnap_skip = False
        g._tracesnap_t0 = time.perf_counter()
        source_files = self._resolve_source_files()
        if not source_files:
            g._tracesnap_skip = True
            return
        trace_id = f"{self.trace_id_prefix}-{int(time.time() * 1000)}"
        g._tracesnap_trace_id = trace_id
        start_recording(trace_id=trace_id, kind="request",
                        source_files=source_files, redact_names=self.redact_names)

    def _recorder_capture_status(self, resp):
        g._tracesnap_status = resp.status_code
        return resp

    def _recorder_end(self, exc):
        if getattr(g, "_tracesnap_skip", True):
            return
        duration_ms = round(
            (time.perf_counter() - getattr(g, "_tracesnap_t0", time.perf_counter())) * 1000.0, 2)
        status = getattr(g, "_tracesnap_status", 500 if exc else 200)
        try:
            trace = stop_recording(
                entry=f"{request.endpoint or '<unknown>'}",
                request={"method": request.method, "path": request.path,
                         "status": status, "duration_ms": duration_ms})
        except RuntimeError:
            return
        out = self.output_dir / f"{g._tracesnap_trace_id}.json"
        write_trace(trace, out)

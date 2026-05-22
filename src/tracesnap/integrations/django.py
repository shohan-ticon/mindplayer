"""
Django middleware.

In settings.py:

    MIDDLEWARE = [
        ...,
        "tracesnap.integrations.django.RecorderMiddleware",
    ]
    TRACESNAP = {
        "output_dir": "traces",
        "source_files": [],   # auto-discover if empty
        "trace_id_prefix": "req",
    }
"""
import os
import time
from pathlib import Path

try:
    from django.conf import settings
except ImportError as exc:  # pragma: no cover
    raise ImportError("Django is not installed. Try: pip install tracesnap[django]") from exc

from .._recorder import start_recording, stop_recording
from ..api import write_trace


class RecorderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        cfg = getattr(settings, "TRACESNAP", {}) or {}
        self.output_dir = Path(cfg.get("output_dir", "traces"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.trace_id_prefix = cfg.get("trace_id_prefix", "req")
        self.source_files = list(cfg.get("source_files", []))
        self.redact_names = cfg.get("redact_names")
        self.enabled = cfg.get("enabled", True)

    def __call__(self, request):
        if not self.enabled or not self.source_files:
            return self.get_response(request)
        trace_id = f"{self.trace_id_prefix}-{int(time.time() * 1000)}"
        t0 = time.perf_counter()
        start_recording(trace_id=trace_id, kind="request",
                        source_files=self.source_files, redact_names=self.redact_names)
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            try:
                trace = stop_recording(
                    entry=request.resolver_match.view_name if request.resolver_match else "<unknown>",
                    request={"method": request.method, "path": request.path,
                             "status": status, "duration_ms": duration_ms})
                write_trace(trace, self.output_dir / f"{trace_id}.json")
            except RuntimeError:
                pass

"""
FastAPI integration.

    from fastapi import FastAPI
    from tracesnap.integrations.fastapi import install

    app = FastAPI()
    install(app, output_dir="traces", source_files=[__file__])

Note: settrace is per-thread; contextvars are per-asyncio-task. For
single-handler-per-request flow (the common case) this works correctly.
Concurrent `asyncio.gather(...)` of multiple traced sub-tasks within a
single request boundary will all share the same recording session —
this is documented behaviour for v0.1. If your handler spawns parallel
tasks that you want individually traced, instrument inside each task
with `tracesnap.record(...)` instead.
"""
import time
from pathlib import Path

try:
    from fastapi import Request
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError as exc:  # pragma: no cover
    raise ImportError("FastAPI is not installed. Try: pip install tracesnap[fastapi]") from exc

from .._recorder import start_recording, stop_recording
from ..api import write_trace


class TraceSnapMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, output_dir="traces", source_files=None,
                 trace_id_prefix="req", redact_names=None, enabled=None):
        super().__init__(app)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.source_files = source_files or []
        self.trace_id_prefix = trace_id_prefix
        self.redact_names = redact_names
        self.enabled = enabled or (lambda r: True)

    async def dispatch(self, request: "Request", call_next):
        if not self.source_files or not self.enabled(request):
            return await call_next(request)

        trace_id = f"{self.trace_id_prefix}-{int(time.time() * 1000)}"
        t0 = time.perf_counter()
        start_recording(trace_id=trace_id, kind="request",
                        source_files=self.source_files, redact_names=self.redact_names)
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            try:
                trace = stop_recording(
                    entry=str(request.url.path),
                    request={"method": request.method, "path": str(request.url.path),
                             "status": status, "duration_ms": duration_ms})
                write_trace(trace, self.output_dir / f"{trace_id}.json")
            except RuntimeError:
                pass


def install(app, **kwargs):
    """Attach TraceSnapMiddleware to a FastAPI app."""
    app.add_middleware(TraceSnapMiddleware, **kwargs)
    return app

"""
Headline API: `tracesnap.record(...)` works as both a context manager
and a decorator, and `write_trace` / `load_trace` for I/O.
"""
import functools
import inspect
import json
import os
from pathlib import Path

from ._recorder import start_recording, stop_recording


class Recording:
    """Returned from `with record(...) as out:`. Exposes .path, .trace_id,
    .event_count, .trace (the dict) once the block exits."""

    __slots__ = ("trace_id", "path", "event_count", "trace")

    def __init__(self, trace_id, path):
        self.trace_id = trace_id
        self.path = path
        self.event_count = 0
        self.trace = None

    def __repr__(self):
        return (f"<Recording trace_id={self.trace_id!r} path={self.path!r} "
                f"events={self.event_count}>")


class _RecordCM:
    """Context-manager/decorator hybrid returned by `record(...)`."""

    def __init__(self, trace_id, source_files=None, output="trace.json",
                 kind="script", redact_names=None, **session_extra):
        self.trace_id = trace_id
        self._source_files = source_files
        self.output = output
        self.kind = kind
        self.redact_names = redact_names
        self.session_extra = session_extra
        self._result = None

    def _resolve_source_files(self, fallback_file=None):
        if self._source_files is not None:
            return [str(p) for p in self._source_files]
        # Default: the caller's file
        if fallback_file:
            return [fallback_file]
        frame = inspect.stack()[2]   # caller of __enter__ / decorator wrap
        return [frame.filename]

    # Context-manager usage
    def __enter__(self):
        source_files = self._resolve_source_files()
        start_recording(trace_id=self.trace_id, kind=self.kind,
                        source_files=source_files, redact_names=self.redact_names)
        self._result = Recording(self.trace_id,
                                 path=str(self.output) if self.output else None)
        return self._result

    def __exit__(self, exc_type, exc_val, exc_tb):
        trace = stop_recording(**self.session_extra)
        self._result.trace = trace
        self._result.event_count = len(trace["events"])
        if self.output:
            write_trace(trace, self.output)
        return False   # never swallow exceptions

    # Decorator usage: @record("id") def fn(...)
    def __call__(self, fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            source_files = self._resolve_source_files(
                fallback_file=inspect.getsourcefile(fn))
            start_recording(trace_id=self.trace_id, kind=self.kind,
                            source_files=source_files, redact_names=self.redact_names)
            try:
                return fn(*args, **kwargs)
            finally:
                trace = stop_recording(**self.session_extra)
                if self.output:
                    write_trace(trace, self.output)
        return wrapper


def record(trace_id, source_files=None, output="trace.json", kind="script",
           redact_names=None, **session_extra):
    """Record a slice of execution.

    Use as a context manager (preferred for explicit scope):

        with tracesnap.record(trace_id="demo") as out:
            do_stuff()
        # out.path  -> "trace.json"
        # out.trace -> the trace dict

    Or as a decorator (records every call to the wrapped function):

        @tracesnap.record(trace_id="checkout")
        def checkout(items, coupon):
            ...

    Args:
        trace_id:      caller-chosen identifier embedded in the trace.
        source_files:  list of file paths to trace. Default: the caller's
                       __file__.
        output:        path to write the trace JSON. Pass None to keep the
                       trace only in memory (`.trace` attribute).
        kind:          "script" | "request" | "test" | ... — recorded in
                       session.kind.
        redact_names:  set of variable names to redact (case-insensitive).
                       Default: tracesnap.redaction.DEFAULT.
        **session_extra: extra fields merged into session (e.g. request=...).
    """
    return _RecordCM(trace_id, source_files=source_files, output=output,
                     kind=kind, redact_names=redact_names, **session_extra)


def write_trace(trace, path):
    """Write a trace dict to a JSON file (UTF-8)."""
    path = os.fspath(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)


def load_trace(path):
    """Load a trace JSON file."""
    path = os.fspath(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

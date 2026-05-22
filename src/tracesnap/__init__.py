"""
tracesnap — record once, replay anywhere.

Quick start:

    import tracesnap

    with tracesnap.record(trace_id="demo") as out:
        do_stuff()

    # out.path     -> "trace.json"
    # out.trace    -> the trace dict
    # out.event_count

Then on the command line:

    tracesnap view trace.json

See README for the full API, framework integrations (Flask / Django /
FastAPI), and the trace format spec.
"""
from ._recorder import start_recording, stop_recording
from .api import Recording, record, write_trace, load_trace
from . import redaction

__version__ = "0.1.0"

__all__ = [
    "record",
    "start_recording",
    "stop_recording",
    "write_trace",
    "load_trace",
    "Recording",
    "redaction",
    "__version__",
]

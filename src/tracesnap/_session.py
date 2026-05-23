"""
Per-recording session state.

We hold one `_Session` per active recording. The session pointer lives in
a `contextvars.ContextVar` so it works correctly under both threads (each
thread gets its own copy via Context.run) and asyncio (each task gets its
own copy via the event loop).
"""
import contextvars
import time

from ._structure import build_structure
from .redaction import DEFAULT as DEFAULT_REDACT, is_redacted


# The active session for the current context (thread or asyncio task).
_current_session = contextvars.ContextVar("tracesnap_session", default=None)


def current():
    """Return the active _Session in this context, or None."""
    return _current_session.get()


def set_current(sess):
    """Set the active session for this context. Returns a token to reset with."""
    return _current_session.set(sess)


def reset_current(token):
    _current_session.reset(token)


class _Session:
    def __init__(self, trace_id, kind, source_files, redact_names=None):
        self.trace_id = trace_id
        self.kind = kind
        self.source_files = [str(p) for p in source_files]
        self.source_files_set = set(self.source_files)
        self.redact = set(redact_names) if redact_names is not None else set(DEFAULT_REDACT)

        self.sources = {}
        self.triggers_by_file = {}
        self.enclosing_by_file = {}
        self.structures_by_file = {}
        for path in self.source_files:
            with open(path) as f:
                src = f.read()
            struct, trig, enc = build_structure(path, src)
            self.sources[path] = src
            self.triggers_by_file[path] = trig
            self.enclosing_by_file[path] = enc
            self.structures_by_file[path] = struct

        self.events = []
        self.seq = 0
        self.start_ns = time.perf_counter_ns()

        self.prev_locals = {}        # frame id -> snapshot
        self.last_line = {}          # frame id -> last line seen
        self.change_counts = {}      # var name -> times changed
        self.loop_iters = {}         # loop node_id -> iteration counter
        self.stack = []              # active frame ids (for depth)
        self.last_struct_event = {}  # frame id -> {node_id: seq of most recent branch/loop event}
        self._unwinding = {}         # frame id -> True if this frame raised (next return is an unwind)

        # Last seen line in a traced frame -- used to attribute extcall events
        # back to the source line that triggered the outbound HTTP call.
        self.last_app_file = None
        self.last_app_fid = None
        self.last_app_line = None

    def now_ns(self):
        return time.perf_counter_ns() - self.start_ns

    def cap(self, name, v):
        """Capture a value: returns the trace-format value object (truncated/redacted)."""
        if is_redacted(name, self.redact):
            return {"repr": "<redacted>", "type": type(v).__name__, "id": id(v),
                    "truncated": False, "redacted": True}
        r = repr(v)
        return {"repr": r[:120], "type": type(v).__name__, "id": id(v),
                "truncated": len(r) > 120, "redacted": False}

    def emit(self, **kw):
        kw.setdefault("parent_seq", None)
        self.events.append({"seq": self.seq, "ts": self.now_ns(),
                            "depth": len(self.stack), **kw})
        self.seq += 1

    def resolve_parent(self, fid, file, line, skip_node=None):
        """Innermost enclosing branch/loop event for a (frame, line). Walks
        the static enclosing list innermost-first and returns the seq of the
        most recent matching branch/loop event emitted in this frame.
        Returns None if no enclosing structural event has fired."""
        enc = self.enclosing_by_file.get(file, {}).get(line) or ()
        frame_struct = self.last_struct_event.get(fid, {})
        for node_id in enc:
            if node_id == skip_node:
                continue
            seq = frame_struct.get(node_id)
            if seq is not None:
                return seq
        return None

    def build_session_dict(self, extras=None):
        """Build the trace.session sub-object. Caller fills the rest."""
        primary = self.source_files[0] if self.source_files else None
        d = {
            "kind": self.kind,
            "granularity": "line",
            "entry": f"{primary}:<module>" if primary else "",
            "source": self.sources.get(primary, "") if primary else "",
            "sources_by_file": dict(self.sources),
            "redaction": {"key_rules": sorted(self.redact)},
            "structure_by_file": {p: s["nodes"] for p, s in self.structures_by_file.items()},
        }
        if extras:
            d.update(extras)
        return d

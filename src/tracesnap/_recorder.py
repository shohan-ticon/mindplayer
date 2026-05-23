"""
The sys.settrace-driven recorder.

Public surface:
- `start_recording(trace_id, kind="script", source_files=..., redact_names=None)`
- `stop_recording(**session_extra) -> trace dict`

Both run on the current thread / async task. Recording state lives in a
`contextvars.ContextVar` (see _session.py) so each thread/task gets its
own session.
"""
import sys

from ._session import _Session, current, set_current, reset_current
from ._extcall import install as install_extcall


def _changed(a, b):
    try:
        return a != b
    except Exception:
        return a is not b


def _diff(sess, frame, attribute_line):
    fid = id(frame)
    fname = frame.f_code.co_filename
    prev = sess.prev_locals.get(fid, {})
    cur = frame.f_locals
    for name, val in cur.items():
        parent = sess.resolve_parent(fid, fname, attribute_line)
        if name not in prev:
            sess.change_counts[name] = 0
            sess.emit(type="assign", var=name, scope="local", line=attribute_line,
                      file=fname,
                      value=sess.cap(name, val), prev=None, change_index=0,
                      parent_seq=parent)
        elif _changed(prev[name], val):
            sess.change_counts[name] = sess.change_counts.get(name, 0) + 1
            sess.emit(type="assign", var=name, scope="local", line=attribute_line,
                      file=fname,
                      value=sess.cap(name, val), prev=sess.cap(name, prev[name]),
                      change_index=sess.change_counts[name],
                      parent_seq=parent)
    sess.prev_locals[fid] = dict(cur)


def _local_trace(frame, event, arg):
    sess = current()
    if sess is None:
        return None
    fid = id(frame)
    fname = frame.f_code.co_filename
    triggers = sess.triggers_by_file.get(fname, {})
    if event == "line":
        line = frame.f_lineno
        _diff(sess, frame, sess.last_line.get(fid, line))
        for t in triggers.get(line, []):
            # branch/loop event's parent = next outer structural context
            parent = sess.resolve_parent(fid, fname, line, skip_node=t["node_id"])
            emitted = sess.seq
            if t["kind"] == "loop":
                sess.loop_iters[t["node_id"]] = sess.loop_iters.get(t["node_id"], -1) + 1
                sess.emit(type="loop", node_id=t["node_id"], line=line, file=fname,
                          iteration=sess.loop_iters[t["node_id"]], parent_seq=parent)
            else:
                sess.emit(type="branch", node_id=t["node_id"], line=line, file=fname,
                          taken=t["label"], parent_seq=parent)
            sess.last_struct_event.setdefault(fid, {})[t["node_id"]] = emitted
        parent = sess.resolve_parent(fid, fname, line)
        sess.emit(type="line", line=line, file=fname,
                  func=frame.f_code.co_name, parent_seq=parent)
        sess.last_line[fid] = line
        sess.last_app_file = fname
        sess.last_app_fid = fid
        sess.last_app_line = line
    elif event == "return":
        _diff(sess, frame, sess.last_line.get(fid, frame.f_lineno))
        parent = sess.resolve_parent(fid, fname, frame.f_lineno)
        # If this frame raised, mark the return as an unwind (no value).
        is_unwind = sess._unwinding.get(fid, False)
        if is_unwind:
            sess.emit(type="return", line=frame.f_lineno, file=fname,
                      func=frame.f_code.co_name,
                      value=None, unwind=True, parent_seq=parent)
        else:
            sess.emit(type="return", value=sess.cap(None, arg),
                      line=frame.f_lineno, file=fname,
                      func=frame.f_code.co_name, parent_seq=parent)
        if sess.stack and sess.stack[-1] == fid:
            sess.stack.pop()
        sess.prev_locals.pop(fid, None)
        sess.last_line.pop(fid, None)
        sess.last_struct_event.pop(fid, None)
        sess._unwinding.pop(fid, None)
    elif event == "exception":
        if arg is not None:
            exc_type, exc_value, _tb = arg
            line = frame.f_lineno
            parent = sess.resolve_parent(fid, fname, line)
            type_name = getattr(exc_type, "__name__", str(exc_type))
            try:
                msg = str(exc_value)
            except Exception:                                # noqa: BLE001
                msg = repr(exc_value)
            sess.emit(
                type="exception",
                exc_type=type_name,
                message=msg[:200],
                value=sess.cap(None, exc_value),
                line=line,
                file=fname,
                func=frame.f_code.co_name,
                parent_seq=parent,
            )
            sess._unwinding[fid] = True
    return _local_trace


def _global_trace(frame, event, arg):
    sess = current()
    if sess is None:
        return None
    fname = frame.f_code.co_filename
    if fname not in sess.source_files_set:
        return None
    co_name = frame.f_code.co_name
    if co_name == "<module>":
        return None
    if co_name.startswith("_recorder_"):
        return None                            # convention: skip framework glue
    if event == "call":
        fid = id(frame)
        sess.stack.append(fid)
        sess.prev_locals[fid] = dict(frame.f_locals)
        sess.last_line[fid] = frame.f_lineno
        # call event's parent = caller frame's enclosing structural context
        parent = None
        caller = frame.f_back
        if caller is not None:
            cfile = caller.f_code.co_filename
            if cfile in sess.source_files_set:
                parent = sess.resolve_parent(id(caller), cfile, caller.f_lineno)
        sess.emit(type="call", func=co_name, line=frame.f_lineno, file=fname,
                  args={k: sess.cap(k, v) for k, v in frame.f_locals.items()},
                  parent_seq=parent)
        return _local_trace
    return None


# We track the ContextVar token so stop_recording can pop the session.
# Stored on the session itself.

def start_recording(trace_id, kind="script", source_files=None, redact_names=None):
    """Begin recording in the current thread / asyncio task.

    Returns the underlying `_Session` (mostly for tests; callers usually
    pair this with `stop_recording`).
    """
    if current() is not None:
        raise RuntimeError("recording already active in this context")
    if source_files is None:
        raise ValueError("source_files is required (list of file paths to trace)")
    sess = _Session(trace_id, kind, source_files, redact_names=redact_names)
    sess._cv_token = set_current(sess)
    install_extcall()
    sys.settrace(_global_trace)
    return sess


def stop_recording(**session_extra):
    """End recording in the current context. Returns the trace dict.

    Any keyword args are merged into `session` (e.g.
    `request={method, path, status, duration_ms}` or
    `entry="app.py:checkout"`).
    """
    sess = current()
    if sess is None:
        raise RuntimeError("no active recording in this context")
    sys.settrace(None)
    reset_current(sess._cv_token)

    session = sess.build_session_dict(extras=session_extra)
    if "entry" in session_extra:
        session["entry"] = session_extra["entry"]
    return {
        "version": "0.1",
        "trace_id": sess.trace_id,
        "structure_ref": "structure.json",
        "session": session,
        "events": sess.events,
    }

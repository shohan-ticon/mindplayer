"""
On-disk library of saved traces.

Each `tracesnap record` adds an entry. `tracesnap view` (without args)
serves the whole library so the player can browse/rename/delete them.

Layout (default at ~/.tracesnap/, overridable via $TRACESNAP_HOME):

    <library_root>/
    ├── index.json                            # ordered list of metadata records
    └── traces/
        ├── <id>.json                         # trace file
        └── <id>.structure.json               # optional sibling

The index file is rewritten atomically (write-tmp + rename) on every
change so a crash mid-write doesn't corrupt the library.
"""
import json
import os
import secrets
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path


_INDEX_VERSION = 1
_ID_LEN = 6


def library_root():
    env = os.environ.get("TRACESNAP_HOME")
    root = Path(env).expanduser() if env else Path.home() / ".tracesnap"
    (root / "traces").mkdir(parents=True, exist_ok=True)
    return root


def _index_path():
    return library_root() / "index.json"


def _read_index():
    p = _index_path()
    if not p.exists():
        return {"version": _INDEX_VERSION, "traces": []}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "traces" not in data:
            return {"version": _INDEX_VERSION, "traces": []}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": _INDEX_VERSION, "traces": []}


def _write_index(idx):
    p = _index_path()
    # Atomic-ish write
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".index-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=2)
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _new_id():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(_ID_LEN // 2)}"


def _trace_path(id_):
    return library_root() / "traces" / f"{id_}.json"


def _structure_path(id_):
    return library_root() / "traces" / f"{id_}.structure.json"


def list_traces():
    """Return the list of metadata records (newest first)."""
    idx = _read_index()
    return list(reversed(idx.get("traces", [])))


def get(id_):
    """Return (metadata, trace_dict) for a saved record, or (None, None)."""
    idx = _read_index()
    meta = next((m for m in idx["traces"] if m["id"] == id_), None)
    if meta is None:
        return None, None
    path = _trace_path(id_)
    if not path.exists():
        return meta, None
    with path.open("r", encoding="utf-8") as f:
        return meta, json.load(f)


def get_path(id_):
    """Return the on-disk path to a record's trace file (or None)."""
    p = _trace_path(id_)
    return p if p.exists() else None


def add(trace, *, name=None, source=None, structure_json=None):
    """Persist a trace dict in the library. Returns the new metadata dict."""
    id_ = _new_id()
    p = _trace_path(id_)
    with p.open("w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)
    if structure_json is not None:
        with _structure_path(id_).open("w", encoding="utf-8") as f:
            json.dump(structure_json, f, indent=2)

    session = (trace.get("session") or {}) if isinstance(trace, dict) else {}
    meta = {
        "id": id_,
        "name": name or trace.get("trace_id") or id_,
        "trace_id": trace.get("trace_id", ""),
        "created": datetime.now().isoformat(timespec="seconds"),
        "created_ts": time.time(),
        "event_count": len(trace.get("events", [])),
        "source": source or session.get("entry") or "",
        "kind": session.get("kind", "script"),
    }
    idx = _read_index()
    idx.setdefault("traces", []).append(meta)
    _write_index(idx)
    return meta


def rename(id_, new_name):
    """Update the display name. Returns the updated metadata, or None."""
    idx = _read_index()
    for m in idx.get("traces", []):
        if m["id"] == id_:
            m["name"] = (new_name or "").strip() or m["name"]
            _write_index(idx)
            return m
    return None


def delete(id_):
    """Remove a record. Returns True if it existed."""
    idx = _read_index()
    traces = idx.get("traces", [])
    for i, m in enumerate(traces):
        if m["id"] == id_:
            traces.pop(i)
            _write_index(idx)
            for p in (_trace_path(id_), _structure_path(id_)):
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass
            return True
    return False


def resolve(identifier):
    """Resolve a user-supplied identifier to (id, trace_path) or (None, None).

    Accepts a library id, an exact name match, or a filesystem path. Used by
    `tracesnap view` so users can say `view trace.json`, `view <id>`, or
    `view "my checkout"`.
    """
    # Filesystem path?
    p = Path(identifier).expanduser()
    if p.exists() and p.is_file():
        return None, p
    # Library id or unique name?
    idx = _read_index()
    by_id = {m["id"]: m for m in idx.get("traces", [])}
    if identifier in by_id:
        return identifier, _trace_path(identifier)
    matches = [m for m in idx.get("traces", []) if m["name"] == identifier]
    if len(matches) == 1:
        m = matches[0]
        return m["id"], _trace_path(m["id"])
    return None, None

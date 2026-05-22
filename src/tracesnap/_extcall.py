"""
Outbound HTTP capture — monkey-patches `requests.Session.send` and
`urllib.request.urlopen` so each outbound call emits an `extcall` event
into the active session.

Patches are installed lazily on the first start_recording() call and
left in place — they no-op when no session is active, so leaving them
patched has no cost for non-recording code paths.

Headers are never included in the event. Only verb, target URL,
timing, and status are recorded.
"""
import threading

from ._session import current as _current_session


_patches_lock = threading.Lock()
_patches_installed = False
_orig_session_send = None
_orig_urlopen = None


def install():
    global _patches_installed, _orig_session_send, _orig_urlopen
    with _patches_lock:
        if _patches_installed:
            return
        _patches_installed = True

        # requests
        try:
            import requests
            _orig_session_send = requests.Session.send

            def patched_send(self, request, **kwargs):
                sess = _current_session()
                if sess is None:
                    return _orig_session_send(self, request, **kwargs)
                line = sess.last_app_line
                fid = sess.last_app_fid
                afile = sess.last_app_file
                started = sess.now_ns()
                status = None
                err = None
                try:
                    resp = _orig_session_send(self, request, **kwargs)
                    status = resp.status_code
                    return resp
                except Exception as e:
                    err = repr(e)[:120]
                    raise
                finally:
                    ended = sess.now_ns()
                    parent = sess.resolve_parent(fid, afile, line) if (fid is not None and afile) else None
                    ev = {"type": "extcall", "kind": "http",
                          "verb": request.method, "target": request.url,
                          "started_ts": started, "ended_ts": ended,
                          "duration_ms": round((ended - started) / 1_000_000, 2),
                          "status": status, "line": line, "parent_seq": parent}
                    if err is not None:
                        ev["error"] = err
                    sess.emit(**ev)
            requests.Session.send = patched_send
        except ImportError:
            pass

        # urllib
        try:
            import urllib.request as _ur
            _orig_urlopen = _ur.urlopen

            def patched_urlopen(url, *args, **kwargs):
                sess = _current_session()
                if sess is None:
                    return _orig_urlopen(url, *args, **kwargs)
                line = sess.last_app_line
                fid = sess.last_app_fid
                afile = sess.last_app_file
                if isinstance(url, str):
                    target, verb = url, "GET"
                else:
                    target = getattr(url, "full_url", str(url))
                    verb = getattr(url, "get_method", lambda: "GET")()
                started = sess.now_ns()
                status = None
                err = None
                try:
                    resp = _orig_urlopen(url, *args, **kwargs)
                    status = getattr(resp, "status", None)
                    return resp
                except Exception as e:
                    err = repr(e)[:120]
                    raise
                finally:
                    ended = sess.now_ns()
                    parent = sess.resolve_parent(fid, afile, line) if (fid is not None and afile) else None
                    ev = {"type": "extcall", "kind": "http",
                          "verb": verb, "target": target,
                          "started_ts": started, "ended_ts": ended,
                          "duration_ms": round((ended - started) / 1_000_000, 2),
                          "status": status, "line": line, "parent_seq": parent}
                    if err is not None:
                        ev["error"] = err
                    sess.emit(**ev)
            _ur.urlopen = patched_urlopen
        except ImportError:
            pass


def uninstall():
    """Restore the original send/urlopen. Mostly for tests."""
    global _patches_installed
    with _patches_lock:
        if not _patches_installed:
            return
        try:
            import requests
            if _orig_session_send is not None:
                requests.Session.send = _orig_session_send
        except ImportError:
            pass
        try:
            import urllib.request as _ur
            if _orig_urlopen is not None:
                _ur.urlopen = _orig_urlopen
        except ImportError:
            pass
        _patches_installed = False

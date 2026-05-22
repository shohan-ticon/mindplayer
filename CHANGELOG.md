# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] — initial release

### Added
- Core recorder: `start_recording` / `stop_recording`, ContextVar-based
  per-context session.
- High-level API: `tracesnap.record(...)` as both context manager and
  decorator; `write_trace` / `load_trace` helpers.
- CLI: `tracesnap record` and `tracesnap view` (the latter spins up a
  stdlib `http.server` and opens the bundled players in the default
  browser).
- Three bundled players (HTML/JS, no JS dependencies):
  - Text view (`player.html`)
  - Flowchart simulator (`simulator.html`)
  - Call graph with click-to-details + zoom + draggable columns
    (`call_graph.html`)
- All three players auto-load via `?trace=<path>` URL parameter when
  served by `tracesnap view`.
- Framework integrations (pip extras): Flask (`tracesnap[flask]`),
  Django (`tracesnap[django]`), FastAPI (`tracesnap[fastapi]`).
- Trace format: `parent_seq` on every event, `structure_by_file`
  bundled in session, statement-level structure nodes for
  flowchart drawing.
- Configurable redaction via `redact_names=`.
- Outbound HTTP capture via monkey-patching `requests.Session.send`
  and `urllib.request.urlopen`.
- Examples for each framework + two standalone scripts
  (`sample_program.py`, `sample_complex.py`).
- Documentation: README + `docs/trace-format-v0.1.md`.
- Pytest suite: structure, record API, redaction, CLI, Flask
  integration smoke.

### Known limitations
- Recursion: player keys frames by stack position; recursive calls
  collide.
- In-place mutation isn't logged (use rebinding).
- No `httpx` / DB driver / socket capture yet.
- Async: per-task contextvars work; concurrent traced sub-tasks
  share a session.
- Exception events not yet emitted.

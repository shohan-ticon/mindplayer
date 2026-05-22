# tracesnap

**Record once, replay anywhere.** A time-travel debugger for Python with a
visual browser-based player. Run your code under instrumentation, get a
JSON trace, then scrub through it line-by-line in three different views
(text, flowchart, call graph) — no re-execution.

> Status: alpha. Stdlib-only core, plug-and-play integrations for
> Flask, Django, and FastAPI.

## Why

`pdb` lets you pause once. `print()` litters your code. Profilers count
nanoseconds but don't show you state. tracesnap captures every line, every
assignment, every branch, and every outbound HTTP call into one
self-contained JSON file you can replay, share in a PR, or attach to a
bug report.

## Install

```bash
pip install tracesnap                # core + CLI + bundled players
pip install tracesnap[flask]         # core + Flask integration
pip install tracesnap[django]
pip install tracesnap[fastapi]
pip install tracesnap[all]
```

## 30-second quickstart

Record a script:

```bash
tracesnap record examples/sample_complex.py --out trace.json
```

Open it in your browser:

```bash
tracesnap view trace.json
```

You'll get a three-view player: source code on the left, and one of:
- **Text view** — current step, call stack, full per-variable history.
- **Simulator** — animated flowchart with loops as cycle boxes, branches
  with the taken arm tagged ✓, variable chips that flash when changed.
- **Call graph** — every function as a node, edges labelled with args
  going in and return values coming back; click a node for per-call
  details.

All three views consume the same `trace.json` and switch via the buttons
in the header.

## Library use

Three equally-valid entry points, same underlying engine:

```python
import tracesnap

# 1) Context manager (preferred for explicit scope)
with tracesnap.record(trace_id="demo") as out:
    do_stuff()
# out.path        -> "trace.json"
# out.event_count
# out.trace       -> the trace dict in memory

# 2) Decorator (records every call to the wrapped function)
@tracesnap.record(trace_id="checkout")
def checkout(items, coupon):
    ...

# 3) Imperative (lowest level; what the integrations use under the hood)
tracesnap.start_recording(trace_id="x", source_files=[__file__])
try:
    do_stuff()
finally:
    trace = tracesnap.stop_recording()
    tracesnap.write_trace(trace, "trace.json")
```

## Framework integrations

### Flask

```python
from flask import Flask
from tracesnap.integrations.flask import TraceSnap

app = Flask(__name__)
TraceSnap(app, output_dir="traces")
```

Every request → one `traces/req-<timestamp>.json`. Open any of them with
`tracesnap view`.

### Django

```python
# settings.py
MIDDLEWARE = [
    ...,
    "tracesnap.integrations.django.RecorderMiddleware",
]
TRACESNAP = {
    "output_dir": "traces",
    "source_files": ["/path/to/your/app/__init__.py"],
}
```

### FastAPI

```python
from fastapi import FastAPI
from tracesnap.integrations.fastapi import install

app = FastAPI()
install(app, output_dir="traces", source_files=[__file__])
```

Async note: `sys.settrace` is per-thread; `contextvars` are per-task. For
a single handler per request (the common case) this works correctly.
Concurrent `asyncio.gather(...)` of multiple traced sub-tasks within a
single request boundary share the same recording session — not
recommended for production. Document/test your specific use.

## What gets recorded

Every event carries `seq`, `ts`, `depth`, `line`, `parent_seq` plus
type-specific fields:

| type      | fields                                                       |
|-----------|--------------------------------------------------------------|
| `call`    | `func`, `args` (each value with `repr`, `type`, `redacted`)  |
| `line`    | `func`                                                       |
| `assign`  | `var`, `scope`, `value`, `prev`, `change_index`              |
| `branch`  | `node_id`, `taken` (`"if"` / `"else"`)                       |
| `loop`    | `node_id`, `iteration` (0-based)                             |
| `return`  | `func`, `value`                                              |
| `extcall` | `kind`, `verb`, `target`, `status`, `duration_ms`, `started_ts`, `ended_ts` |

Values are `{repr, type, id, truncated, redacted}`. `repr` is capped at
120 chars (`truncated: true` if hit). Variables named `password`,
`token`, `secret`, `authorization`, `api_key` get `<redacted>` — both as
function args and as locals. Configurable via `redact_names=` to any
entry point.

`parent_seq` links events into a tree:
- Inside a `for` body, every event has `parent_seq` pointing at the
  current iteration's `loop` event.
- Inside an `if` arm, every event points at the `branch` event.
- Top-level events in a function have `parent_seq: null`.
- A `call` event's `parent_seq` is the *caller's* context at the call
  site (so a call made inside an `if` arm points at the branch event in
  the caller, not at the new frame).

Full spec: [`docs/trace-format-v0.1.md`](docs/trace-format-v0.1.md).

## CLI

```
tracesnap record  PATH [--out trace.json] [--id NAME] [--redact NAMES]
tracesnap view    PATH [--view text|simulator|graph] [--port 0] [--no-browser]
tracesnap --version
```

`view` starts a tiny stdlib `http.server` on a free port (no extra
dependencies), copies the bundled player HTMLs into a tmpdir alongside
your trace, and opens your default browser pointed at
`<view>.html?trace=<file>`. Switch views in-app via the header buttons.

## Known issues / edges

- **Recursion**: the player keys frames by stack position, so recursive
  calls collide. Roadmap item: per-call `frame_id`.
- **Assign attribution is one line late** (`sys.settrace` fires before a
  line runs; we attribute the diff to the previous line). Documented in
  the trace-format spec.
- **Value-change vs assignment**: we log value *changes*, so in-place
  mutation like `xs.append(y)` doesn't emit. Use rebinding
  (`xs = xs + [y]`) to see growth.
- **`extcall` scope**: only `requests.Session.send` and
  `urllib.request.urlopen` are wrapped. `httpx`, DB drivers, and stdlib
  `socket` are not (yet).
- **Async**: per-task `contextvars` work; per-task `threading.settrace`
  does not (yet). Concurrent traced sub-tasks share the same session.

## Roadmap

- SQLite backend for traces > ~10k events (single-trace decision; JSON
  stays default).
- `depends_on` field on assigns → true data-flow graphs in the call
  graph view.
- `exception` events on unwind.
- `httpx` + DB driver `extcall` capture.
- Per-task `threading.settrace` for parallel-async support.

## License

MIT — see [LICENSE](LICENSE).

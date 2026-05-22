# tracesnap trace format — v0.1

A trace is a self-contained JSON document. One trace = one bounded unit of
execution (a script run, an HTTP request, a test, an explicit
`with record(...)` block).

## Top-level shape

```json
{
  "version": "0.1",
  "trace_id": "demo",
  "structure_ref": "structure.json",
  "session": { ... },
  "events": [ ... ]
}
```

- `version` — format version. Bumped on breaking changes.
- `trace_id` — caller-chosen identifier (free-form string).
- `structure_ref` — optional path to a sibling `structure.json` (legacy).
  Consumers should prefer the inline `session.structure_by_file`.

## `session` object

```json
{
  "kind": "script" | "request" | "test",
  "granularity": "line",
  "entry": "path/to/file.py:<module>",
  "source": "def f(): ...\n",
  "redaction": { "key_rules": ["password", "token", ...] },
  "structure_by_file": { "<path>": [<structure node>, ...] },
  "request": {                       // present when kind == "request"
    "method": "GET",
    "path": "/checkout",
    "status": 200,
    "duration_ms": 1130.25
  }
}
```

`source` is the full text of the primary file. The player renders it on
the left side of every view.

`structure_by_file` maps each recorded file to its static tree (see
**Structure nodes** below). Used by the simulator and call-graph players
to draw control-flow shapes without re-parsing Python in JS.

## Events

Every event has these common fields:

| field        | type                                | meaning |
|--------------|-------------------------------------|---------|
| `seq`        | int                                 | monotonically increasing per trace |
| `ts`         | int (ns)                            | nanoseconds since session start |
| `depth`      | int                                 | stack depth at this event |
| `line`       | int                                 | source line associated with this event |
| `parent_seq` | int \| null                         | seq of the innermost enclosing branch/loop event (see below) |
| `type`       | enum                                | one of: call, line, assign, branch, loop, return, extcall |

### `call`
Function entry.

| field | meaning |
|-------|---------|
| `func` | function name (`co_name`) |
| `args` | object of `name → value` for every argument passed |

`parent_seq` is the **caller's** structural context at the call site
(a call made from inside an `if` arm points at the branch event).

### `line`
About-to-execute statement.

| field | meaning |
|-------|---------|
| `func` | enclosing function name |

### `assign`
A local variable's value changed since the previous line event.

| field | meaning |
|-------|---------|
| `var` | variable name |
| `scope` | `"local"` |
| `value` | value object (see below) |
| `prev` | value object, or `null` for the first time the var is set |
| `change_index` | 0 the first time; N for the (N+1)-th change |

Attribution: assigns are attributed to the *previous* line, because
`sys.settrace` fires before a line runs. Documented behaviour.

### `branch`
Execution entered one arm of an `if`/`elif`/`else`.

| field | meaning |
|-------|---------|
| `node_id` | id of the `branch` node in `structure_by_file` |
| `taken` | `"if"` or `"else"` |

### `loop`
A for/while iteration started.

| field | meaning |
|-------|---------|
| `node_id` | id of the `loop` node in `structure_by_file` |
| `iteration` | 0-based |

### `return`
Function exit.

| field | meaning |
|-------|---------|
| `func` | function name |
| `value` | value object — the returned value |

### `extcall`
An outbound HTTP call (via `requests.Session.send` or
`urllib.request.urlopen`).

| field | meaning |
|-------|---------|
| `kind` | `"http"` |
| `verb` | `"GET"`, `"POST"`, … |
| `target` | full URL |
| `started_ts`, `ended_ts` | nanoseconds since session start |
| `duration_ms` | float |
| `status` | int (HTTP status) or null on error |
| `error` | optional string if the call raised |

Headers are never recorded.

## Value object

```json
{
  "repr": "{'token': 'tok_a1b2'}",
  "type": "dict",
  "id": 140562036584944,
  "truncated": false,
  "redacted": false
}
```

- `repr` — `repr(value)` truncated to 120 chars.
- `type` — class name.
- `id` — `id(value)` at recording time (CPython-specific).
- `truncated` — true if the full `repr()` exceeded 120 chars.
- `redacted` — true if the variable name was in the redaction set
  (`password`, `token`, `secret`, `authorization`, `api_key` by default);
  `repr` becomes the literal string `"<redacted>"`.

## Structure node

```json
{
  "id": "checkout.for9.if11",
  "kind": "function" | "branch" | "loop" | "statement",
  "subkind": "if" | "else" | "for" | "while" | "Assign" | "Return" | ...,
  "name": "checkout",                   // only on functions
  "params": ["items", "coupon"],        // only on functions
  "lineno": 11,
  "end_lineno": 15,
  "parent": "checkout.for9"             // id of the immediate structural parent
}
```

`parent` is the **immediate** structural parent (function, branch, or
loop) — not the enclosing function. This lets consumers walk the tree
naturally: every function's children are its top-level statements; a
loop's children are the bodies inside that loop; etc.

## Parent-seq resolution

For any event at `(frame, line)`:
1. Look up the *enclosing chain* for that source line (innermost first):
   the list of branch/loop nodes whose body contains that line.
2. For each candidate, check whether a branch/loop event with that
   `node_id` has fired in this frame yet.
3. The first match wins — that's `parent_seq`.

The chain is purely static (computed by `_structure.build_structure`)
and is reused for every event. This is what makes `parent_seq` cheap.

## Versioning

Breaking changes bump the major part (e.g. `1.0`). Additive changes
(new event types, new optional fields) keep `version: "0.1"` until v1.0
ships. Players in this repo accept missing fields gracefully.

## Future fields (not yet emitted)

- `exception` events on unwind.
- `depends_on` array on `assign` — RHS names parsed at compile time.
- `thread` / `task` ids for concurrency.
- Optional SQLite-backed storage for very large traces (same logical
  schema, different physical layout).

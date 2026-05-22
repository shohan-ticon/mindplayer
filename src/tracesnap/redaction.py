"""
Redaction rules — variable / arg names whose values are blanked out in the trace.

Configurable via `start_recording(..., redact_names=...)` or
`record(redact_names=...)`. Defaults err on the side of safety: anything
that looks like a credential.

The check is case-insensitive against the variable name itself. Redaction
DOES NOT walk into nested data (e.g. a dict with a 'token' key still shows
the value if the dict is bound to a non-redacted variable name).
"""

DEFAULT = frozenset({"password", "token", "secret", "authorization", "api_key"})


def is_redacted(name, redact_set=DEFAULT):
    return bool(name) and name.lower() in redact_set

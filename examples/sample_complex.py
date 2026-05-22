"""
sample_complex.py — exercises every event type the recorder emits.

Run:   python3 recorder.py sample_complex.py
Open:  player.html, click "Load trace.json"

What you'll see in the trace:
  call      every function entry (main, authorize, categorize, fetch_quote, long_string)
  line      every executed statement
  assign    every value change to a local (rebound, not mutated in place)
  branch    every if / elif / else arm taken (including arms nested inside loops)
  loop      every for / while iteration, including a loop nested inside a branch
  return    every function exit
  extcall   one outbound HTTPS request inside fetch_quote()

Plus:
  redaction:   variables named password / token / secret / api_key / authorization
               are shown as <redacted> in the player.
  truncation:  values whose repr() exceeds 120 chars are flagged truncated.

Note: the network call hits httpbin.org. If you're offline it will raise after
10 s and the trace will still include an extcall event with `error` set.
"""
import requests


def authorize(user, password, api_key):
    # `password` and `api_key` are redacted by name on the call event.
    # `token` is also a redacted name, so its assign appears as <redacted>.
    token = "tok_" + api_key[:4]
    is_admin = user == "admin"
    # 3-way branch -- exactly one arm fires per call, all three reachable via
    # different inputs from main()'s repeated calls below.
    if is_admin:
        scope = "all"
    elif user.startswith("svc_"):
        scope = "service"
    else:
        scope = "read"
    return {"token": token, "scope": scope}


def categorize(numbers):
    # for loop with a 3-way branch inside -- parent_seq on the branch event
    # points to the current iteration's loop event.
    buckets = {"low": 0, "mid": 0, "high": 0}
    seen = []
    for n in numbers:
        seen = seen + [n]                  # rebind so the change is captured
        if n < 10:
            buckets = {**buckets, "low": buckets["low"] + 1}
        elif n < 100:
            buckets = {**buckets, "mid": buckets["mid"] + 1}
        else:
            buckets = {**buckets, "high": buckets["high"] + 1}

    # while loop with a branch inside.
    countdown = 3
    note = ""
    while countdown > 0:
        if countdown == 2:
            note = "halfway"
        else:
            note = "tick"
        countdown = countdown - 1
    return buckets, seen, note


def fetch_quote():
    # Outbound HTTP -> extcall event (kind=http, status, duration_ms, …).
    # Network errors are tolerated so the demo still completes offline; the
    # recorder still emits an extcall event with `error` set.
    try:
        r = requests.get("https://httpbin.org/uuid", timeout=5)
    except requests.exceptions.RequestException:
        return ""
    if r.status_code == 200:
        return r.json().get("uuid", "")
    return ""


def long_string(n):
    # Built by rebinding so each growth step is a separate assign event.
    # The final return value is > 120 chars, so the trace marks it truncated.
    parts = []
    for i in range(n):
        parts = parts + [f"item-{i:03d}"]
    return ", ".join(parts)


def main():
    # Three calls to authorize exercise all three arms of its if/elif/else.
    creds_admin = authorize("admin",   password="hunter2", api_key="key-AAAA1111")
    creds_svc   = authorize("svc_bot", password="r0bot!",  api_key="key-BBBB2222")
    creds_user  = authorize("nora",    password="qwerty",  api_key="key-CCCC3333")

    counts, seen, last_note = categorize([3, 42, 7, 250, 99, 800, 1])
    quote_id = fetch_quote()
    big = long_string(20)                 # > 120 chars -> truncated value
    summary = {
        "scope_admin": creds_admin["scope"],
        "scope_svc":   creds_svc["scope"],
        "scope_user":  creds_user["scope"],
        "buckets":     counts,
        "seen_count":  len(seen),
        "last_note":   last_note,
        "quote_id_present": bool(quote_id),
        "big_len":     len(big),
    }
    return summary


result = main()

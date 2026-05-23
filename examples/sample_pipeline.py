"""
sample_pipeline.py - a fake ETL/notification pipeline composed of four
self-contained "sub-scripts", each with its own helpers, all wired
together by main(). Use this to see how the player's call graph and
event graph views render a workflow that has several distinct stages
instead of one big tree.

Run:   tracesnap record examples/sample_pipeline.py --out trace.json
Open:  tracesnap view trace.json

What you'll see in the trace, stage by stage:
  Stage 1 - load_users        : list-of-dicts ingest, name redaction
  Stage 2 - score_users       : nested loop + branch, accumulator rebinds
  Stage 3 - build_report      : dict comprehension via rebind, formatting
  Stage 4 - notify_admins     : retry loop with a simulated failure,
                                then success; raised + caught exception
                                so the player shows an unwind on one path

Each stage has its own `run_*` entry function that main() calls in
sequence, so in the call graph you should see four sibling subtrees
hanging off main().

Offline-friendly: no network, pure stdlib.
"""
from __future__ import annotations


# =========================================================================
# Stage 1 - "load_users" sub-script
# =========================================================================


def _parse_user_row(row):
    # row is "id,name,role,password" -- password gets redacted by name.
    parts = row.split(",")
    user_id = int(parts[0])
    name = parts[1]
    role = parts[2]
    password = parts[3]
    return {"id": user_id, "name": name, "role": role, "password": password}


def _validate_user(user):
    if not user["name"]:
        return False
    if user["role"] not in ("admin", "member", "guest"):
        return False
    return True


def run_load_users(raw_rows):
    users = []
    skipped = 0
    for row in raw_rows:
        user = _parse_user_row(row)
        if _validate_user(user):
            users = users + [user]
        else:
            skipped = skipped + 1
    return users, skipped


# =========================================================================
# Stage 2 - "score_users" sub-script
# =========================================================================


def _role_weight(role):
    if role == "admin":
        return 10
    elif role == "member":
        return 5
    else:
        return 1


def _activity_bonus(activity_log):
    bonus = 0
    for event in activity_log:
        if event == "login":
            bonus = bonus + 1
        elif event == "post":
            bonus = bonus + 3
        elif event == "report":
            bonus = bonus + 5
    return bonus


def run_score_users(users, activity_by_id):
    scores = []
    for user in users:
        base = _role_weight(user["role"])
        log = activity_by_id.get(user["id"], [])
        bonus = _activity_bonus(log)
        scores = scores + [{"id": user["id"], "name": user["name"],
                            "score": base + bonus}]
    return scores


# =========================================================================
# Stage 3 - "build_report" sub-script
# =========================================================================


def _format_line(rank, entry):
    return f"#{rank:02d}  {entry['name']:<10}  score={entry['score']}"


def _sort_scores(scores):
    # tiny insertion sort so the trace shows the inner loop clearly
    sorted_scores = []
    for entry in scores:
        inserted = False
        new_list = []
        for existing in sorted_scores:
            if not inserted and entry["score"] > existing["score"]:
                new_list = new_list + [entry]
                inserted = True
            new_list = new_list + [existing]
        if not inserted:
            new_list = new_list + [entry]
        sorted_scores = new_list
    return sorted_scores


def run_build_report(scores):
    ranked = _sort_scores(scores)
    lines = []
    rank = 1
    for entry in ranked:
        lines = lines + [_format_line(rank, entry)]
        rank = rank + 1
    report = "\n".join(lines)
    return {"line_count": len(lines), "top_name": ranked[0]["name"] if ranked else None,
            "report": report}


# =========================================================================
# Stage 4 - "notify_admins" sub-script
# =========================================================================


def _send_once(channel, payload, attempt):
    # Simulate a flaky channel: the "pager" channel fails on attempt 1
    # and succeeds on attempt 2, so the retry loop has something to do.
    api_key = "key-NOT-A-REAL-SECRET"   # redacted by name
    if channel == "pager" and attempt == 1:
        raise ConnectionError(f"{channel} unreachable (attempt {attempt})")
    return {"channel": channel, "delivered": True, "bytes": len(payload),
            "api_key": api_key}


def _send_with_retry(channel, payload, max_attempts):
    last_error = None
    attempt = 1
    while attempt <= max_attempts:
        try:
            return _send_once(channel, payload, attempt)
        except ConnectionError as exc:
            last_error = str(exc)
            attempt = attempt + 1
    # Exhausted retries: raise so the caller sees the unwind path.
    raise RuntimeError(f"giving up on {channel}: {last_error}")


def run_notify_admins(report):
    payload = f"REPORT\n{report}"
    delivered = []
    errors = []
    for channel in ("email", "pager", "slack"):
        try:
            result = _send_with_retry(channel, payload, max_attempts=3)
            delivered = delivered + [result["channel"]]
        except RuntimeError as exc:
            errors = errors + [{"channel": channel, "error": str(exc)}]
    return {"delivered": delivered, "errors": errors}


# =========================================================================
# main - wires the four sub-scripts together
# =========================================================================


def main():
    raw_rows = [
        "1,alice,admin,hunter2",
        "2,bob,member,p@ss",
        "3,,guest,xyz",                 # invalid -- empty name, skipped
        "4,carol,member,qwerty",
        "5,dan,wizard,zzz",             # invalid -- bad role, skipped
        "6,eve,guest,letmein",
    ]
    activity = {
        1: ["login", "post", "post", "report"],
        2: ["login", "login"],
        4: ["login", "post"],
        6: [],
    }

    users, skipped = run_load_users(raw_rows)
    scores = run_score_users(users, activity)
    report = run_build_report(scores)
    delivery = run_notify_admins(report["report"])

    summary = {
        "loaded":     len(users),
        "skipped":    skipped,
        "scored":     len(scores),
        "top":        report["top_name"],
        "delivered":  delivery["delivered"],
        "errors":     [e["channel"] for e in delivery["errors"]],
    }
    return summary


result = main()

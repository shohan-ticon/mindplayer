"""Smoke test for the Flask integration. Skipped if Flask isn't installed."""
import os
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")


def test_flask_records_one_trace_per_request(tmp_path):
    from flask import Flask
    from tracesnap.integrations.flask import TraceSnap

    APP_PY = tmp_path / "app_under_test.py"
    APP_PY.write_text("""
import os
from flask import Flask
from tracesnap.integrations.flask import TraceSnap

app = Flask(__name__)
TraceSnap(app, output_dir=os.environ['TRACE_DIR'], source_files=[__file__])

def helper(items):
    total = 0
    for x in items:
        total = total + x
    return total

@app.route('/sum')
def sum_endpoint():
    return {'total': helper([1, 2, 3])}
""")
    import importlib.util
    out_dir = tmp_path / "traces"
    os.environ["TRACE_DIR"] = str(out_dir)
    spec = importlib.util.spec_from_file_location("app_under_test", str(APP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    client = mod.app.test_client()
    resp = client.get("/sum")
    assert resp.status_code == 200
    assert resp.get_json() == {"total": 6}

    files = list(out_dir.glob("*.json"))
    assert len(files) == 1
    import json
    trace = json.loads(files[0].read_text())
    assert trace["session"]["kind"] == "request"
    assert trace["session"]["request"]["status"] == 200
    funcs = {e.get("func") for e in trace["events"] if e["type"] == "call"}
    assert "sum_endpoint" in funcs
    assert "helper" in funcs

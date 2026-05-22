"""Smoke tests for the CLI (record subcommand)."""
import json
import os
import subprocess
import sys
import textwrap


def test_cli_record(tmp_path):
    script = tmp_path / "demo.py"
    script.write_text(textwrap.dedent("""
        def f(x):
            return x + 1
        out = f(2)
    """).lstrip())
    out = tmp_path / "trace.json"
    cmd = [sys.executable, "-m", "tracesnap.cli", "record", str(script),
           "--out", str(out), "--id", "cli-test"]
    res = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ})
    assert res.returncode == 0, res.stderr
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["trace_id"] == "cli-test"
    assert data["events"][0]["type"] == "call"


def test_cli_version():
    res = subprocess.run([sys.executable, "-m", "tracesnap.cli", "--version"],
                         capture_output=True, text=True)
    assert res.returncode == 0
    assert "tracesnap" in res.stdout

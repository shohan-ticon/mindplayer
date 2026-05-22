"""
DEPRECATED: this file is a thin compat shim that re-exports from the
`tracesnap` package. New code should `import tracesnap` directly.

It also keeps the old CLI behaviour working:
    python3 recorder.py sample_program.py     # writes trace.json + structure.json

so existing demos and READMEs that reference this entry point keep working.
The shim will be removed in tracesnap 0.2.
"""
import json
import sys
from pathlib import Path

from tracesnap import start_recording, stop_recording
from tracesnap._structure import build_structure   # used by some older scripts

__all__ = ["start_recording", "stop_recording", "build_structure"]


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "sample_program.py"
    target_path = Path(target).resolve()
    src = target_path.read_text()
    structure, _, _ = build_structure(str(target_path), src)

    start_recording(trace_id="demo", kind="script", source_files=[str(target_path)])
    code = compile(src, str(target_path), "exec")
    try:
        exec(code, {"__name__": "__traced__", "__file__": str(target_path)})
    finally:
        trace = stop_recording()

    with open("structure.json", "w") as f:
        json.dump(structure, f, indent=2)
    with open("trace.json", "w") as f:
        json.dump(trace, f, indent=2)

    events = trace["events"]
    print(f"structure.json: {len(structure['nodes'])} nodes")
    print(f"trace.json:     {len(events)} events")
    print("event type counts:", {
        t: sum(1 for e in events if e["type"] == t)
        for t in ["call", "line", "assign", "branch", "loop", "return", "extcall", "exception"]
    })


if __name__ == "__main__":
    main()

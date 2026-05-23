"""
release_check.py - local pre-flight before pushing a release tag.

Runs the same checks the GitHub Actions publish workflow runs, so you can
catch packaging problems on your laptop instead of in CI:

  1. python -m build              -> sdist + wheel into dist/
  2. python -m twine check        -> README renders on PyPI, metadata valid
  3. wheel content sanity check   -> player HTML files are actually bundled
  4. install in a throwaway venv  -> `tracesnap --version` works from the wheel

Usage:
    python scripts/release_check.py

Exits non-zero on the first failure. Pass --skip-install to skip step 4
(faster, but leaves the install-in-fresh-venv smoke test off).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def run(cmd, **kw):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def clean_dist():
    if DIST.exists():
        shutil.rmtree(DIST)


def find_wheel() -> Path:
    wheels = sorted(DIST.glob("*.whl"))
    if not wheels:
        sys.exit("no wheel found in dist/")
    return wheels[-1]


def find_sdist() -> Path:
    sdists = sorted(DIST.glob("*.tar.gz"))
    if not sdists:
        sys.exit("no sdist found in dist/")
    return sdists[-1]


def step_build():
    clean_dist()
    run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
         "build", "twine"])
    run([sys.executable, "-m", "build"])


def step_twine_check():
    run([sys.executable, "-m", "twine", "check", "--strict",
         *[str(p) for p in DIST.glob("*")]])


def step_wheel_contents():
    wheel = find_wheel()
    print(f"\n$ inspect {wheel.name}")
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
    for n in names:
        print(f"  {n}")
    html_files = [n for n in names if n.startswith("tracesnap/player/") and n.endswith(".html")]
    if not html_files:
        sys.exit("ERROR: player HTML files are missing from the wheel.\n"
                 "Check `[tool.hatch.build] artifacts = ...` in pyproject.toml.")
    print(f"OK: {len(html_files)} player HTML files bundled.")


def step_install_smoke():
    wheel = find_wheel()
    with tempfile.TemporaryDirectory(prefix="tracesnap-relcheck-") as td:
        venv = Path(td) / "venv"
        run([sys.executable, "-m", "venv", str(venv)])
        # Windows puts binaries under Scripts/, POSIX under bin/.
        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        py = bin_dir / ("python.exe" if os.name == "nt" else "python")
        tracesnap = bin_dir / ("tracesnap.exe" if os.name == "nt" else "tracesnap")
        run([str(py), "-m", "pip", "install", "--quiet", str(wheel)])
        run([str(tracesnap), "--version"])
        # Make sure the player HTMLs were actually installed into site-packages.
        run([str(py), "-c",
             "import tracesnap, importlib.resources as r, sys;"
             " from pathlib import Path;"
             " root = Path(tracesnap.__file__).parent / 'player';"
             " files = sorted(p.name for p in root.glob('*.html'));"
             " print('player HTMLs installed:', files);"
             " sys.exit(0 if files else 'no HTMLs installed')"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--skip-install", action="store_true",
                   help="Skip the install-in-fresh-venv smoke test.")
    args = p.parse_args()

    print(f"release_check: root = {ROOT}")
    step_build()
    step_twine_check()
    step_wheel_contents()
    if args.skip_install:
        print("\nSkipped venv install smoke test (--skip-install).")
    else:
        step_install_smoke()

    print("\nAll checks passed.")
    print(f"Artifacts in {DIST}:")
    for f in sorted(DIST.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
    print("\nNext: bump version in pyproject.toml, commit, tag (e.g. `git tag v0.1.0`),")
    print("and `git push origin main --tags` to trigger the publish workflow.")


if __name__ == "__main__":
    main()

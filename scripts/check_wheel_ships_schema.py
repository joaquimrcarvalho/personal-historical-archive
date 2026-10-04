"""Gate: a built wheel ships pha-sidecar.schema.json and can load it.

This is the F4 gate from the bug report
``enhancements/pha-installed-wheel-missing-schema-bug-report.md``. In a source
checkout the repo-root ``schema/`` sits exactly where the old ``parents[2]``
arithmetic expected it, so this failure is invisible to the unit tests; the
only way to catch it is to build a distribution and import from it.

Run from the repo root:  python scripts/check_wheel_ships_schema.py
Needs the project's runtime deps importable (PyYAML at minimum).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MEMBER = "personal_historical_archive/schema/pha-sidecar.schema.json"
ROOT = Path(__file__).resolve().parents[1]


def candidate_builders(out):
    return [
        ("uv", ["uv", "build", "--out-dir", str(out)]),
        ("build", [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)]),
        ("pip", [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), "."]),
    ]


def build_wheel(out):
    last = None
    for name, cmd in candidate_builders(out):
        if name == "uv" and not shutil.which("uv"):
            continue
        if name in ("build", "pip"):
            probe = subprocess.run([sys.executable, "-c", f"import {name}"],
                                   capture_output=True)
            if probe.returncode != 0:
                continue
        print("$", " ".join(cmd), flush=True)
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
        if proc.returncode == 0:
            return name
        last = proc.stdout + proc.stderr
    raise SystemExit("no usable wheel builder (tried uv, build, pip)\n" + (last or ""))


def probe_load(unpacked, tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(unpacked) + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import personal_historical_archive as p;"
        "from personal_historical_archive.sidecar import _schema;"
        "print('module=' + p.__file__);"
        "print('title=' + _schema()['title'])"
    )
    return subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          env=env, text=True, capture_output=True)


def main():
    with tempfile.TemporaryDirectory(prefix="pha-wheel-gate-") as tmp:
        tmp_path = Path(tmp)
        out = tmp_path / "dist"
        out.mkdir()
        builder = build_wheel(out)
        wheels = sorted(out.glob("*.whl"))
        if not wheels:
            raise SystemExit(f"{builder} produced no wheel in {out}")
        wheel = wheels[-1]
        print("built", wheel.name)

        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
            if MEMBER not in names:
                near = [n for n in names if "schema" in n.lower()]
                raise SystemExit(f"FAIL: {MEMBER} absent from {wheel.name}"
                                 + (f"; schema-ish members: {near}" if near else ""))
            print("ok: wheel contains", MEMBER)
            unpacked = tmp_path / "unpacked"
            zf.extractall(unpacked)

        proc = probe_load(unpacked, tmp_path)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout + proc.stderr)
            if "yaml" in (proc.stderr or "").lower():
                raise SystemExit("FAIL: probe python cannot import PyYAML; run "
                                 "this gate in the project environment")
            raise SystemExit("FAIL: _schema() did not load from the built wheel")
        print(proc.stdout.strip())

        module = next((ln.split("=", 1)[1] for ln in proc.stdout.splitlines()
                       if ln.startswith("module=")), "")
        if not Path(module).resolve().is_relative_to(unpacked.resolve()):
            raise SystemExit("FAIL: probe imported from " + module + " (not the wheel)")
        if "title=" not in proc.stdout:
            raise SystemExit("FAIL: no schema title in probe output")
        print("PASS: the built wheel ships and loads pha-sidecar.schema.json")


if __name__ == "__main__":
    main()

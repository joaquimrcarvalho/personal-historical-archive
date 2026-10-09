"""Gate: a built wheel ships the files an installed pha reads from its package.

The F4 gate from ``enhancements/pha-installed-wheel-missing-schema-bug-report.md``
(pha-sidecar.schema.json), widened to every file a built install must carry:
the schema, the bundled ``dsh-pha`` payload, and the documentation that
``pha help <topic>`` points at. In a source checkout these files sit exactly
where the code looks first, so a missing one is invisible to the unit tests; the
only way to catch it is to build a distribution and look inside.

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
VIEW_MEMBERS = {
    "personal_historical_archive/_view/package.json",
    "personal_historical_archive/_view/lib/index.js",
    "personal_historical_archive/_view/lib/client.js",
}
ROOT = Path(__file__).resolve().parents[1]


def doc_members() -> set[str]:
    """Every documentation file the build PROMISES to ship: the ``.md`` entries
    of the wheel force-include table, read from pyproject so this gate cannot
    drift from the build.

    These are the files ``pha help <topic>`` points at. A wheel install has no
    checkout, so ``cli._help_doc_path`` reads them from the package — before they
    were force-included, ``pha help mcp`` in a ``uv tool install`` printed a path
    that did not exist, and HARNESS_INTRODUCTION.md was not distributed at all.
    """
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force = data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    return {dest for dest in force.values() if dest.endswith(".md")}


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
            missing_view = sorted(VIEW_MEMBERS - set(names))
            if missing_view:
                raise SystemExit("FAIL: bundled dsh-pha payload absent: "
                                 + ", ".join(missing_view))
            print("ok: wheel contains the bundled dsh-pha payload")
            missing_docs = sorted(doc_members() - set(names))
            if missing_docs:
                raise SystemExit(
                    "FAIL: documentation absent from the wheel — a `pha help "
                    "<topic>` path would not exist in an install: "
                    + ", ".join(missing_docs))
            print(f"ok: wheel contains all {len(doc_members())} help documents")
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

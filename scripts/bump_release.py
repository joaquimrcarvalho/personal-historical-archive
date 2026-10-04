#!/usr/bin/env python3
"""Bump the one release version that `pha update` watches.

Every user-visible change in this repository, including a `dsh-pha/` plugin
change, must bump this version.  The script updates the three files that must
stay in sync:

- ``pyproject.toml`` (the wheel version)
- ``src/personal_historical_archive/__init__.py`` (the version `pha update` reads)
- ``dsh-pha/package.json`` (the bundled plugin package version)

Usage:
    python scripts/bump_release.py patch
    python scripts/bump_release.py --set 0.37.0
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src/personal_historical_archive/__init__.py"
PLUGIN = ROOT / "dsh-pha/package.json"


def current_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("pyproject.toml has no project version")
    return match.group(1)


def _bump(version: str, part: str) -> str:
    core = version.split("-", 1)[0]
    parts = [int(p) for p in core.split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def write_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', text, count=1, flags=re.M)
    PYPROJECT.write_text(text, encoding="utf-8")

    text = INIT.read_text(encoding="utf-8")
    text = re.sub(r'^__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', text, count=1, flags=re.M)
    INIT.write_text(text, encoding="utf-8")

    data = json.loads(PLUGIN.read_text(encoding="utf-8"))
    data["version"] = version
    PLUGIN.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="bump the pha + dsh-pha release version")
    parser.add_argument("part", nargs="?", choices=("patch", "minor", "major"), default="patch")
    parser.add_argument("--set", dest="set_version", default=None, help="set an exact version")
    args = parser.parse_args()

    old = current_version()
    new = args.set_version or _bump(old, args.part)
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise SystemExit(f"refusing non-X.Y.Z version: {new}")
    write_version(new)
    print(f"pha release: {old} -> {new}")
    print("updated: pyproject.toml, src/personal_historical_archive/__init__.py, dsh-pha/package.json")


if __name__ == "__main__":
    main()

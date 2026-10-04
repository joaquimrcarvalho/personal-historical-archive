#!/usr/bin/env python3
"""CI guard: a dsh-pha plugin change must bump the pha release version.

Usage:
    python scripts/check_release_bump.py origin/main
    python scripts/check_release_bump.py origin/main --head HEAD

The plugin is installed by `pha view install`, but `pha update` only notices a
new release when the remote `__version__` moves.  A plugin-only change that
does not bump the release version is therefore invisible to every DSH machine.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


VERSION_FILES = {
    "pyproject.toml",
    "src/personal_historical_archive/__init__.py",
}


def changed_files(base: str, head: str) -> set[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or "git diff failed")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description="check that plugin changes bump the release")
    parser.add_argument("base", help="base ref (e.g. origin/main)")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    changed = changed_files(args.base, args.head)
    plugin_changed = any(path.startswith("dsh-pha/") for path in changed)
    if not plugin_changed:
        print("ok: no dsh-pha changes")
        return
    missing = VERSION_FILES - changed
    if missing:
        print(
            "error: dsh-pha changed without a pha release bump; missing changes in: "
            + ", ".join(sorted(missing)),
            file=sys.stderr,
        )
        print("run: python scripts/bump_release.py patch", file=sys.stderr)
        raise SystemExit(1)
    print("ok: dsh-pha changes include a pha release bump")


if __name__ == "__main__":
    main()

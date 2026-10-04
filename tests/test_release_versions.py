"""One release unit: pha and the bundled dsh-pha plugin move together."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_and_dunder_versions_match():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    init = (ROOT / "src/personal_historical_archive/__init__.py").read_text(encoding="utf-8")
    py_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    init_version = re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.M)
    assert py_version, "pyproject.toml has no project version"
    assert init_version, "__init__.py has no __version__"
    assert py_version.group(1) == init_version.group(1)


def test_dsh_pha_package_version_matches_pha():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    plugin = json.loads((ROOT / "dsh-pha/package.json").read_text(encoding="utf-8"))
    py_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert py_version
    assert plugin["version"] == py_version.group(1)

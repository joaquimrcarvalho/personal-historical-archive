"""Packaged release notes for `pha whatsnew`.

The changelog is generated at release time and shipped in the wheel, so an
installed machine never needs the git repository or the network. The data
lives in ``CHANGELOG.json`` next to this module.
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

CHANGELOG_FILE = "CHANGELOG.json"
CHANGELOG_SCHEMA = 1

_TYPE_LABELS = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "docs": "Documentation",
    "refactor": "Refactoring",
    "test": "Tests",
    "chore": "Maintenance",
    "other": "Other",
}


class ReleaseNotesError(RuntimeError):
    """The packaged changelog is missing or malformed."""


def _load_manifest(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        resource = resources.files(__package__).joinpath(CHANGELOG_FILE)
    else:
        resource = Path(path)
    try:
        text = resource.read_text(encoding="utf-8")
    except OSError:
        return {"schema": CHANGELOG_SCHEMA, "releases": {}}
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ReleaseNotesError(f"invalid {CHANGELOG_FILE}: {e}") from e
    if not isinstance(data, dict) or data.get("schema") != CHANGELOG_SCHEMA:
        raise ReleaseNotesError(f"unsupported {CHANGELOG_FILE} schema")
    if not isinstance(data.get("releases"), dict):
        raise ReleaseNotesError(f"{CHANGELOG_FILE} has no releases object")
    return data


def load_changelog(path: Path | None = None) -> dict[str, Any]:
    """Load the packaged changelog, or an explicit file for tests/tools."""
    return _load_manifest(path)


def version_key(version: str) -> tuple:
    """A simple ordering key for X.Y.Z-style versions."""
    parts = []
    for piece in str(version).lstrip("v").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def releases_newer_than(data: dict[str, Any], version: str) -> list[str]:
    current = version_key(version)
    versions = [v for v in data.get("releases", {}) if version_key(v) > current]
    return sorted(versions, key=version_key)


def get_release(data: dict[str, Any], version: str) -> dict[str, Any] | None:
    entry = data.get("releases", {}).get(version)
    return entry if isinstance(entry, dict) else None


def _change_line(change: dict[str, Any]) -> str:
    text = str(change.get("text") or change.get("subject") or "").strip()
    scope = str(change.get("scope") or "").strip()
    if scope and text:
        return f"{scope}: {text}"
    return text


def format_release(version: str, entry: dict[str, Any]) -> str:
    lines = [f"pha {version}"]
    summary = str(entry.get("summary") or "").strip()
    if summary:
        lines.append("")
        lines.append(summary)
    changes = entry.get("changes") or []
    grouped: dict[str, list[str]] = {}
    for change in changes:
        if not isinstance(change, dict):
            continue
        kind = str(change.get("type") or "other").lower()
        text = _change_line(change)
        if text:
            grouped.setdefault(kind, []).append(text)
    for kind in ("feat", "fix", "perf", "refactor", "docs", "test", "chore", "other"):
        items = grouped.get(kind)
        if not items:
            continue
        lines.append("")
        lines.append(f"{_TYPE_LABELS.get(kind, kind.title())}:")
        for item in items:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def select_releases(
    data: dict[str, Any],
    *,
    version: str | None = None,
    since: str | None = None,
    all_releases: bool = False,
) -> list[str]:
    """Return the versions to show, oldest first.

    Unknown requested versions raise; an empty packaged changelog returns an
    empty list so callers can print a plain message.
    """
    releases = data.get("releases", {})
    if version is not None:
        if version not in releases:
            raise ReleaseNotesError(f"no packaged release notes for {version}")
        return [version]
    if since is not None:
        if since not in releases:
            # the boundary may itself be older than the packaged entries:
            # return everything newer than it.
            newer = releases_newer_than(data, since)
            if not newer:
                raise ReleaseNotesError(f"no packaged releases newer than {since}")
            return newer
        newer = releases_newer_than(data, since)
        if not newer:
            raise ReleaseNotesError(f"no packaged releases newer than {since}")
        return newer
    if all_releases:
        return sorted(releases, key=version_key)
    if not releases:
        return []
    return [sorted(releases, key=version_key)[-1]]


def render(data: dict[str, Any], versions: list[str]) -> str:
    if not versions:
        return "No release notes are packaged in this pha build."
    return "\n\n".join(format_release(v, data["releases"][v]) for v in versions)


def as_json(data: dict[str, Any], versions: list[str]) -> dict[str, Any]:
    return {
        "schema": CHANGELOG_SCHEMA,
        "versions": versions,
        "releases": {v: data["releases"][v] for v in versions},
    }

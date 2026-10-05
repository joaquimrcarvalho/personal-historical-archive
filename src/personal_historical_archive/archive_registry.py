"""Known archive roots in the per-user pha settings.

The per-user settings file can carry one **active** archive plus a list of
alternatives::

    paths:
      archive_dir: /Users/me/jesuit-archive   # active/default
      archives:
        - /Users/me/jesuit-archive
        - /Users/me/litterae

The registry is deliberately user-scoped: an installed pha launched from an
arbitrary cwd (an MCP/DSH host starts at ``/``) has no project around it, and
one person may keep several archives on one machine. ``paths.archive_dir`` is
still the only key archive resolution reads; this module only maintains the
bookkeeping that lets ``pha list archive-dir`` / ``pha use archive-dir`` /
``pha rm archive-dir`` manage it.

Reads are forgiving: a missing or malformed file behaves like an empty
registry. Writes preserve unrelated top-level settings, but are a YAML
round-trip (the file is machine-owned, unlike a project's commented
``config.yaml``).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import user_settings_file


def _read() -> dict:
    path = user_settings_file()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    paths = data.get("paths")
    if not isinstance(paths, dict):
        data["paths"] = {}
    return data


def _write(data: dict) -> Path:
    path = user_settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _norm(path: str | Path) -> str:
    return str(Path(str(path)).expanduser().resolve())


def _paths(data: dict) -> dict:
    paths = data.setdefault("paths", {})
    if not isinstance(paths, dict):
        paths = {}
        data["paths"] = paths
    return paths


def _archive_list(paths: dict) -> list[str]:
    """Registered paths, with the active pointer always included first."""
    out: list[str] = []
    active = str(paths.get("archive_dir") or "").strip()
    if active:
        out.append(_norm(active))
    raw = paths.get("archives") or []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                candidate = _norm(item)
                if candidate not in out:
                    out.append(candidate)
    return out


def active() -> str:
    """The per-user default archive, or '' when unset/unreadable."""
    return str(_paths(_read()).get("archive_dir") or "").strip()


def archives() -> list[str]:
    """Every known archive root, active first, then alternatives."""
    return _archive_list(_paths(_read()))


def register(path: str | Path) -> bool:
    """Add ``path`` to the alternatives. Returns False when already present."""
    candidate = _norm(path)
    data = _read()
    paths = _paths(data)
    known = _archive_list(paths)
    if candidate in known:
        if paths.get("archive_dir") or candidate not in (paths.get("archives") or []):
            # The active pointer is implicit; make the list explicit once.
            paths["archives"] = known
            _write(data)
        return False
    known.append(candidate)
    paths["archives"] = known
    _write(data)
    return True


def activate(path: str | Path) -> Path:
    """Make ``path`` the per-user default and ensure it is registered."""
    candidate = _norm(path)
    data = _read()
    paths = _paths(data)
    known = _archive_list(paths)
    if candidate not in known:
        known.append(candidate)
    paths["archives"] = known
    paths["archive_dir"] = candidate
    return _write(data)


def remove(path: str | Path, force: bool = False) -> tuple[bool, str]:
    """Remove ``path`` from the registry.

    Refuses to remove the active default unless ``force`` is true; with force
    the first remaining archive becomes active, or the pointer is cleared.
    Never touches archive files.
    """
    candidate = _norm(path)
    data = _read()
    paths = _paths(data)
    known = _archive_list(paths)
    if candidate not in known:
        return False, f"not registered: {candidate}"
    current_active = str(paths.get("archive_dir") or "").strip()
    if current_active and _norm(current_active) == candidate and not force:
        return False, ("is the active per-user default; use `--use <other>` first, "
                       "or pass --force to clear it")
    known = [p for p in known if p != candidate]
    if current_active and _norm(current_active) == candidate:
        if known:
            paths["archive_dir"] = known[0]
            message = f"removed the active archive {candidate}; active is now {known[0]}"
        else:
            paths.pop("archive_dir", None)
            message = f"removed the active archive {candidate}; no per-user default remains"
    else:
        message = f"removed {candidate}"
    if known:
        paths["archives"] = known
    else:
        paths.pop("archives", None)
    _write(data)
    return True, message

"""Install / update the PHA view plugin (`@personal-historical-archive/dsh-pha`).

The plugin lives in this repository under ``dsh-pha/``.  ``pha update`` updates
the Python tool and then asks a freshly installed ``pha`` to run
``pha view install``, which links (or pnpm-adds) the plugin into every DeepSeek
Harness profile that already uses it.  A running DSH host loads the plugin once
at startup, so the install step ends by telling the caller to restart it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import source_checkout_root


PLUGIN_PACKAGE = "@personal-historical-archive/dsh-pha"
PLUGIN_LINK_PARTS = ("node_modules", "@personal-historical-archive", "dsh-pha")

_PATCH_HEADER = (
    "# Your patch layer for this dsh profile, applied after every bundle layer:\n"
    "# a top-level YAML array of loader patch entries (id-targeted config\n"
    "# overrides, disables, and insert lists; `!!js` expressions allowed).\n"
)


class ViewError(RuntimeError):
    """Raised when the view plugin cannot be located or installed."""


def dsh_home() -> Path:
    """The DeepSeek Harness home (``DSH_HOME`` or ``~/.dsh``)."""
    raw = os.environ.get("DSH_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".dsh"


def _user_data_dir() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw).expanduser() if raw else Path.home() / ".local" / "share"
    return base / "personal-historical-archive"


def _installed_pha_version() -> str:
    from . import __version__

    return str(__version__)


def plugin_payload() -> Path:
    """The directory holding the runtime dsh-pha payload.

    A source checkout uses its own ``dsh-pha/`` so development changes are live.
    A wheel install uses the bundled ``personal_historical_archive/_view``
    directory, copied to a stable cache path because uv may replace the wheel
    directory on the next update.
    """
    root = source_checkout_root()
    if root is not None:
        src = root / "dsh-pha"
        if (src / "lib" / "index.js").is_file():
            return src.resolve()

    bundled = Path(__file__).resolve().parent / "_view"
    if not (bundled / "lib" / "index.js").is_file():
        raise ViewError(
            "the bundled PHA view payload is missing; run from a source checkout "
            "or reinstall pha"
        )
    dest = _user_data_dir() / "view" / _installed_pha_version()
    if (dest / "lib" / "index.js").is_file():
        return dest
    _copy_tree_atomic(bundled, dest)
    return dest


def _copy_tree_atomic(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)


def plugin_version(payload: Path | None = None) -> str:
    payload = payload or plugin_payload()
    try:
        data = json.loads((payload / "package.json").read_text(encoding="utf-8"))
        return str(data.get("version") or "")
    except (OSError, ValueError):
        return ""


def list_profiles(home: Path | None = None) -> list[Path]:
    home = home or dsh_home()
    root = home / "profiles"
    if not root.is_dir():
        return []
    profiles = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name == "node_modules" or child.name.startswith("."):
            continue
        profiles.append(child)
    return sorted(profiles)


def profile_plugin_link(profile: Path) -> Path:
    return profile.joinpath(*PLUGIN_LINK_PARTS)


def profile_patch_path(profile: Path) -> Path:
    return profile / "cordis.patch.yml"


def row_present(profile: Path) -> bool:
    path = profile_patch_path(profile)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"^\s*-\s*id:\s*['\"]?dsh-pha['\"]?\s*(#.*)?$", text, re.M))


def detect_running_profile(home: Path | None = None) -> str | None:
    """Best-effort: the profile of a currently running harness process."""
    names = [p.name for p in list_profiles(home)]
    if not names:
        return None
    try:
        proc = subprocess.run(
            ["ps", "-Ao", "args="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in proc.stdout.splitlines():
        args = line.strip()
        if not args:
            continue
        if "dsh" not in args and "desktop-cli" not in args and "bin.js" not in args:
            continue
        for name in names:
            if args.endswith(" " + name) or (" " + name + " ") in args:
                return name
    return None


def target_profiles(
    profile: str | None = None,
    all_profiles: bool = False,
    home: Path | None = None,
) -> list[Path]:
    home = home or dsh_home()
    profiles = list_profiles(home)
    if not profiles:
        return []
    if profile:
        chosen = home / "profiles" / profile
        if not chosen.is_dir():
            raise ViewError(f"DSH profile not found: {chosen}")
        return [chosen]

    rows = [p for p in profiles if row_present(p)]
    if all_profiles:
        return profiles
    running = detect_running_profile(home)
    if rows:
        if running and not any(p.name == running for p in rows):
            chosen = home / "profiles" / running
            if chosen.is_dir():
                rows.append(chosen)
        return rows
    if running:
        chosen = home / "profiles" / running
        if chosen.is_dir():
            return [chosen]
    default = home / "profiles" / "desktop"
    if default.is_dir():
        return [default]
    if len(profiles) == 1:
        return profiles
    raise ViewError(
        "could not choose a DSH profile; pass --profile or --all-profiles "
        "(available: " + ", ".join(p.name for p in profiles) + ")"
    )


def _is_our_package(path: Path) -> bool:
    try:
        data = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("name") == PLUGIN_PACKAGE


def ensure_plugin_link(profile: Path, payload: Path) -> Path:
    """Link the payload into a profile's node_modules, or pnpm-add it."""
    dest = profile_plugin_link(profile)
    pnpm = shutil.which("pnpm")
    if pnpm and (profile / "pnpm-workspace.yaml").is_file():
        proc = subprocess.run(
            [pnpm, "add", str(payload)],
            cwd=str(profile),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode == 0:
            return dest
        # Fall through to the symlink path; it is what a profile without pnpm
        # gets anyway.
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        if dest.resolve() == payload.resolve():
            return dest
        dest.unlink()
    elif dest.exists():
        if not _is_our_package(dest):
            raise ViewError(
                f"{dest} exists and is not {PLUGIN_PACKAGE}; refusing to replace it"
            )
        shutil.rmtree(dest)
    try:
        os.symlink(str(payload.resolve()), str(dest), target_is_directory=True)
    except OSError:
        shutil.copytree(payload, dest)
    return dest


def _line_indent(line: str) -> int:
    raw = line.rstrip("\n")
    return len(raw) - len(raw.lstrip(" "))


def _find_row(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*id:\s*['\"]?dsh-pha['\"]?\s*(#.*)?$", line):
            return i
    return None


def _row_end(lines: list[str], start: int) -> int:
    row_indent = _line_indent(lines[start])
    for j in range(start + 1, len(lines)):
        raw = lines[j].rstrip("\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _line_indent(lines[j]) <= row_indent:
            return j
    return len(lines)


def _find_config(lines: list[str], start: int, end: int) -> int | None:
    for j in range(start, end):
        if re.match(r"^\s*config:\s*(#.*)?$", lines[j]):
            return j
    return None


def _config_end(lines: list[str], start: int, end: int) -> int:
    config_indent = _line_indent(lines[start])
    for j in range(start + 1, end):
        raw = lines[j].rstrip("\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _line_indent(lines[j]) <= config_indent:
            return j
    return end


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value))


def _set_config_value(
    lines: list[str],
    start: int,
    end: int,
    key: str,
    value: object,
) -> list[str]:
    if value is None:
        return lines
    config_idx = _find_config(lines, start, end)
    if config_idx is None:
        name_idx = None
        for j in range(start, end):
            if re.match(r"^\s*name:\s*", lines[j]):
                name_idx = j
                break
        row_indent = _line_indent(lines[start])
        insert_at = (name_idx + 1) if name_idx is not None else (start + 1)
        config_indent = " " * (row_indent + 2)
        key_indent = " " * (row_indent + 4)
        block = [
            config_indent + "config:\n",
            key_indent + key + ": " + _yaml_scalar(value) + "\n",
        ]
        return lines[:insert_at] + block + lines[insert_at:]

    config_end = _config_end(lines, config_idx, end)
    key_re = re.compile(r"^(\s*)" + re.escape(key) + r"\s*:")
    for j in range(config_idx + 1, config_end):
        match = key_re.match(lines[j])
        if match:
            lines[j] = match.group(1) + key + ": " + _yaml_scalar(value) + "\n"
            return lines
    key_indent = " " * (_line_indent(lines[config_idx]) + 2)
    lines.insert(config_idx + 1, key_indent + key + ": " + _yaml_scalar(value) + "\n")
    return lines


def _new_row_block(project_root: str | None, archive_dir: str | None) -> str:
    out = [
        "- insert:\n",
        "    - id: dsh-pha\n",
        "      name: '" + PLUGIN_PACKAGE + "'\n",
    ]
    if project_root is not None or archive_dir is not None:
        out.append("      config:\n")
        if project_root is not None:
            out.append("        projectRoot: " + _yaml_scalar(project_root) + "\n")
        if archive_dir is not None:
            out.append("        archiveDir: " + _yaml_scalar(archive_dir) + "\n")
    return "".join(out)


def _flow_array_is_empty(text: str) -> bool:
    body = re.sub(r"#.*$", "", text, flags=re.M)
    return body.strip() in ("", "[]")


def patch_profile(
    profile: Path,
    project_root: str | None = None,
    archive_dir: str | None = None,
) -> bool:
    """Create or update the dsh-pha row in ``cordis.patch.yml``.

    Returns True when the file changed.  Existing comments and unrelated rows
    are preserved; only the dsh-pha row is touched.
    """
    path = profile_patch_path(profile)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if not path.exists() and _flow_array_is_empty(text):
        text = _PATCH_HEADER

    lines = text.splitlines(keepends=True)
    row_idx = _find_row(lines)
    if row_idx is None:
        if _flow_array_is_empty(text):
            new_text = _PATCH_HEADER + _new_row_block(project_root, archive_dir)
        else:
            separator = "" if not text or text.endswith("\n") else "\n"
            new_text = text + separator + "\n" + _new_row_block(project_root, archive_dir)
    else:
        end = _row_end(lines, row_idx)
        for key, value in (("projectRoot", project_root), ("archiveDir", archive_dir)):
            lines = _set_config_value(lines, row_idx, end, key, value)
            end = _row_end(lines, row_idx)
        new_text = "".join(lines)
    if new_text == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return True


def install(
    profile: str | None = None,
    all_profiles: bool = False,
    project_root: str | None = None,
    archive_dir: str | None = None,
    home: Path | None = None,
) -> dict:
    home = home or dsh_home()
    profiles = target_profiles(profile, all_profiles, home)
    if not profiles:
        return {
            "ok": True,
            "skipped": "no DeepSeek Harness profiles found",
            "home": str(home),
            "payload": None,
            "version": "",
            "profiles": [],
            "restart_required": False,
        }
    payload = plugin_payload()
    version = plugin_version(payload)
    installed = []
    for prof in profiles:
        link = ensure_plugin_link(prof, payload)
        changed = patch_profile(prof, project_root=project_root, archive_dir=archive_dir)
        installed.append({
            "profile": prof.name,
            "path": str(prof),
            "link": str(link),
            "row_updated": changed,
        })
    return {
        "ok": True,
        "home": str(home),
        "payload": str(payload),
        "version": version,
        "profiles": installed,
        "restart_required": True,
    }


def _text_value(text: str, key: str) -> str | None:
    match = re.search(r"^\s*" + re.escape(key) + r":\s*(.*?)\s*$", text, re.M)
    if not match:
        return None
    value = match.group(1).strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            return str(json.loads(value))
        except ValueError:
            return value
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def status(profile: str | None = None, home: Path | None = None) -> dict:
    home = home or dsh_home()
    try:
        payload = plugin_payload()
        version = plugin_version(payload)
        payload_error = None
    except ViewError as exc:
        payload = None
        version = ""
        payload_error = str(exc)
    if profile:
        chosen = home / "profiles" / profile
        profiles = [chosen] if chosen.is_dir() else []
    else:
        profiles = list_profiles(home)
    out = []
    for prof in profiles:
        link = profile_plugin_link(prof)
        try:
            link_target = str(link.resolve()) if link.exists() or link.is_symlink() else None
        except OSError:
            link_target = None
        try:
            patch_text = profile_patch_path(prof).read_text(encoding="utf-8")
        except OSError:
            patch_text = ""
        linked = False
        if payload is not None and link_target is not None:
            linked = link_target == str(payload)
        out.append({
            "profile": prof.name,
            "path": str(prof),
            "row_present": row_present(prof),
            "link": str(link),
            "link_target": link_target,
            "linked": linked,
            "projectRoot": _text_value(patch_text, "projectRoot"),
            "archiveDir": _text_value(patch_text, "archiveDir"),
        })
    return {
        "ok": payload_error is None,
        "error": payload_error,
        "home": str(home),
        "payload": str(payload) if payload is not None else None,
        "version": version,
        "profiles": out,
    }


def install_after_tool_update(archive_dir: str | None = None) -> str | None:
    """Ask the freshly installed pha to sync the view plugin.

    Returns a human line, or None when there is no DSH install to update.
    Raises ViewError when the fresh pha step fails.
    """
    home = dsh_home()
    if not (home / "profiles").is_dir():
        return None
    if not list_profiles(home):
        return None
    root = source_checkout_root()
    kwargs: dict = {}
    if root is not None:
        argv = [sys.executable, "-m", "personal_historical_archive"]
        kwargs["cwd"] = str(root)
    else:
        fresh = shutil.which("pha")
        argv = [fresh] if fresh else [sys.executable, "-m", "personal_historical_archive"]
    argv = [*argv, "view", "install", "--from-update"]
    if archive_dir:
        argv += ["--archive-dir", str(archive_dir)]
    env = dict(os.environ)
    env["PHA_NO_UPDATE_CHECK"] = "1"
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=300, **kwargs)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ViewError(detail or "pha view install failed")
    return (proc.stdout or "").strip() or "PHA view plugin updated; restart DSH to activate it."

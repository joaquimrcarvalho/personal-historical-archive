"""Leave a trace in the archive of WHERE the `pha` tool lives on this machine.

An archive is a self-contained data root, but the tool that manages it is
installed elsewhere — a virtualenv, a ``uv tool`` / pipx shim, a global entry
point. The archive itself knows nothing about it. That is fine on the machine
where pha was configured (the pointer lives in the tool's own ``.env`` /
``config.yaml``), but it is a dead end for an AI agent dropped into the archive
directory from a shell with a minimal PATH, where ``pha`` is simply "command
not found" — and the archive's README can only say "install it", not "it is
already installed *here*".

So every pha run writes a small, machine-specific record into the archive root:

  ``.pha/location.json``   machine-readable (command, python, version, MCP, serve)
  ``pha-location.md``      the same facts, readable by a human or an agent

Both are regenerated on every run, so they self-heal after a reinstall, a move
or an upgrade. ``.pha/`` is gitignored: it describes ONE machine, and a copied
or cloned archive must not inherit a stale path that looks authoritative. The
templates of the archive's own ``README.md`` / ``AGENTS.md`` point at
``pha-location.md``, which is what makes the trace discoverable.

The durable fallback recorded there is the interpreter form
``<python> -m personal_historical_archive``: it needs no PATH entry and no
activated virtualenv, so it works from any shell, cron job or agent runtime.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a config import cycle
    from .config import Config

SCHEMA = 1
PHA_DIRNAME = ".pha"
LOCATION_JSON = "location.json"
LOCATION_MD = "pha-location.md"

# A gitignore block appended to an archive that lacks it. Additive only: an
# archive owner's own .gitignore is never rewritten, just extended once.
MANAGED_IGNORE_MARKER = (
    "# pha: machine-local tool location (describes THIS machine, not archive content)"
)


def _abspath(p: os.PathLike | str) -> str:
    """Absolute, user-expanded, but NOT symlink-resolved (see `build_location`)."""
    return os.path.abspath(os.path.expanduser(str(p)))


def _version() -> str:
    from . import __version__

    return str(__version__)


def _pha_command() -> str | None:
    """The `pha` console script this process was launched by (or would be).

    Tried in order: what actually ran us (``argv[0]``, when it is the console
    script and not e.g. ``pytest``), the script sitting beside our interpreter
    in the same venv/bin, then whatever a PATH lookup finds. ``None`` when the
    install has no console script at all — the caller then falls back to the
    interpreter form, which always works.
    """
    argv0 = (sys.argv[0] if sys.argv else "") or ""
    if argv0 and os.path.splitext(os.path.basename(argv0))[0] == "pha":
        cand = _abspath(argv0)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand

    bindir = os.path.dirname(_abspath(sys.executable))
    for name in ("pha", "pha.exe"):
        cand = os.path.join(bindir, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand

    found = shutil.which("pha")
    return _abspath(found) if found else None


def _install_kind(python: str) -> str:
    """A coarse label for HOW pha is installed (uv tool / pipx / venv / system)."""
    p = python.replace("\\", "/").lower()
    if "/uv/tools/" in p or "/uv/tool/" in p:
        return "uv-tool"
    if "/pipx/" in p:
        return "pipx"
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "virtualenv"
    return "system"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_location(cfg: "Config") -> dict:
    """Build the machine record for `cfg`'s archive (pure: writes nothing).

    ``sys.executable`` is deliberately NOT symlink-resolved. In a virtualenv
    ``bin/python`` is a symlink to the base interpreter, and calling the
    *resolved* path would start that interpreter with the wrong ``sys.prefix``
    — so ``-m personal_historical_archive`` would fail to find the package.
    The venv-path form is the one that works, so it is the one recorded.
    """
    python = _abspath(sys.executable)
    command = _pha_command()
    archive_dir = str(cfg.archive_dir)
    # BOTH are recorded: PHA_ARCHIVE_DIR picks the data root, PHA_HOME picks the
    # project whose config.yaml defines the engines/models. Together they make a
    # recorded command independent of the shell's cwd and of any machine-wide
    # pha configuration — which is the whole point for an agent in a bare shell.
    env = {"PHA_HOME": str(cfg.root), "PHA_ARCHIVE_DIR": archive_dir}
    base = [command] if command else [python, "-m", "personal_historical_archive"]

    def entry(*args: str) -> dict:
        """An MCP-client-shaped command: program + args + env."""
        return {"command": base[0], "args": list(base[1:]) + list(args), "env": dict(env)}

    record: dict = {
        "schema": SCHEMA,
        "written_at": _now(),
        "hostname": platform.node(),
        "archive_dir": archive_dir,
        "pha": {
            "version": _version(),
            "command": command,
            "module": [python, "-m", "personal_historical_archive"],
            "python": python,
            "install_kind": _install_kind(python),
            "source_checkout": str(cfg.root),
            "env": env,
        },
        "mcp": {
            "stdio": entry("mcp"),
            "sse": entry("mcp", "--transport", "sse", "--host", "127.0.0.1", "--port", "8000"),
        },
        "serve": {**entry("serve"), "url": cfg.serve_base_url},
        "location_file": os.path.join(archive_dir, LOCATION_MD),
        "json_file": os.path.join(archive_dir, PHA_DIRNAME, LOCATION_JSON),
    }
    dsh = Path(cfg.root) / "dsh-pha"
    if dsh.is_dir():
        record["dsh_plugin"] = str(dsh)
    return record


def _quoted(value: str) -> str:
    return f'"{value}"' if " " in value else value


def _prefix(command: str | list[str]) -> str:
    """A shell-safe command prefix: a str, or program + args as a list.

    Only the executable itself is quoted (so a list's `-m …` stays plain args
    rather than being swallowed into one quoted word).
    """
    if isinstance(command, list):
        return " ".join([_quoted(command[0]), *command[1:]])
    return _quoted(command)


def render_markdown(rec: dict) -> str:
    """The human/agent-readable twin of ``.pha/location.json``."""
    pha = rec["pha"]
    archive_dir = rec["archive_dir"]
    project_root = pha["source_checkout"]
    command = pha["command"]
    # `primary` is what to run; `module` is the PATH-proof interpreter form that
    # always works, used as the documented fallback (and AS the primary command
    # when this install has no console script at all).
    primary = _prefix(command or pha["module"])
    module = _prefix(pha["module"])
    mcp_stdio = f"{primary} mcp"
    mcp_sse = f"{primary} mcp --transport sse --host 127.0.0.1 --port 8000"
    serve = f"{primary} serve"
    version = pha["version"]
    written = rec["written_at"].replace("T", " ")[:16]

    return f"""<!-- pha-location: machine-local record — written by pha {version} on {written}.
     Regenerated on every pha run; safe to delete. NOT part of the archive. -->

# Where `pha` is on this machine

This archive was last opened by **pha {version}** on **{rec['hostname']}**.

Archive path: `{archive_dir}`

## Running pha here

Set both of these first. Together they make every command below independent of
your shell's working directory and of any machine-wide pha configuration, so
they work from a bare shell, a cron job or an agent runtime:

```bash
export PHA_HOME="{project_root}"      # the pha project (its config.yaml)
export PHA_ARCHIVE_DIR="{archive_dir}"   # this archive
```

Then:

```bash
{primary} status
```

If that executable is missing (pha was reinstalled, moved, or lives in another
venv), use the interpreter form. It needs no PATH entry and no activated
virtualenv:

```bash
{module} status
```

## This machine's pha

| | |
| --- | --- |
| pha version | `{version}` |
| pha command | `{command or "— (no console script; use the interpreter form)"}` |
| python | `{pha['python']}` |
| install kind | `{pha['install_kind']}` |
| source checkout | `{project_root}` |

## MCP server (AI clients)

Run the two `export`s above, then:

```bash
# stdio — the MCP client spawns this itself:
{mcp_stdio}

# SSE over the LAN — run this on the archive machine, connect from elsewhere:
{mcp_sse}
```

A client's config wants the program, the args and BOTH env vars: take them from
`.pha/location.json` (`mcp.stdio` / `mcp.sse`).

## Reader service (page viewer)

```bash
{serve}        # -> {rec['serve']['url']}
```

## Notes for agents

- This file is **machine-local**: it records only the machine that most
  recently ran pha here. After a reinstall or a move the path may be stale — if
  a command fails, use the interpreter form above.
- Machine-readable twin: `{os.path.relpath(rec['json_file'], archive_dir)}`.
- `pha info` prints this same tool location from inside pha (`--json` for
  machines).
"""


def ensure_managed_ignores(archive_dir: str | Path) -> bool:
    """Make sure the archive's `.gitignore` hides the machine trace.

    Additive and idempotent: an archive owner's own `.gitignore` is never
    rewritten, only the missing managed block is appended. Creates the file
    when the archive is a git repo without one. Returns True when it wrote.
    """
    root = Path(archive_dir)
    gi = root / ".gitignore"
    if not gi.exists():
        if not (root / ".git").exists():
            return False
        gi.write_text(
            f"{MANAGED_IGNORE_MARKER}\n{PHA_DIRNAME}/\n{LOCATION_MD}\n", encoding="utf-8"
        )
        return True
    text = gi.read_text(encoding="utf-8")
    if MANAGED_IGNORE_MARKER in text:
        return False
    if not text or text.endswith("\n\n"):
        sep = ""
    elif text.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    gi.write_text(
        f"{text}{sep}{MANAGED_IGNORE_MARKER}\n{PHA_DIRNAME}/\n{LOCATION_MD}\n",
        encoding="utf-8",
    )
    return True


def write_location(cfg: "Config") -> dict:
    """Write/refresh the machine trace in `cfg`'s archive. Returns the record."""
    rec = build_location(cfg)
    archive_dir = Path(cfg.archive_dir)
    d = archive_dir / PHA_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    (d / LOCATION_JSON).write_text(
        json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (archive_dir / LOCATION_MD).write_text(render_markdown(rec), encoding="utf-8")
    ensure_managed_ignores(archive_dir)
    return rec

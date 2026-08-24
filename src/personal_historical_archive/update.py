"""pha self-update: detect and install newer versions of the tool.

Two entry points are used by the CLI:

- ``pha update`` — check GitHub for a newer release and, if one exists, offer
  to install it (``--check`` only reports, ``--yes`` skips the confirmation).
- a lightweight **daily startup check** — the first ``pha`` invocation of each
  day compares the installed version against the GitHub default branch and, if
  a newer one is available, prints a one-line notice. It is best-effort (never
  blocks or fails a command) and can be disabled with ``update.enabled: false``
  in config.yaml or ``PHA_NO_UPDATE_CHECK=1``.

How an update is applied depends on how pha was installed:

- **editable install from a git checkout** (the recommended ``uv tool install
  --editable .`` layout): the checkout is fast-forwarded with ``git pull
  --ff-only`` and the running code is updated in place — no reinstall needed.
- **non-editable (wheel) install**: pha is reinstalled from the GitHub
  repository with ``uv tool install --force`` (or ``pip`` as a fallback).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

DEFAULT_REPO = "joaquimrcarvalho/personal-historical-archive"
DEFAULT_BRANCH = "main"
DEFAULT_INTERVAL_H = 24
DEFAULT_TIMEOUT = 5  # seconds, kept short so a startup check never stalls a command

# path of the version string inside the GitHub copy of the package
_REMOTE_VERSION_FILE = "src/personal_historical_archive/__init__.py"


class UpdateError(RuntimeError):
    """Raised when an update cannot be applied (bad state, no git, conflicts)."""


# --------------------------------------------------------------------------- version handling

# PEP-440-ish ordering ranks (dev < a/alpha < b/beta < rc < final < post).
_RANK = {
    "dev": 1, "a": 2, "alpha": 2, "b": 3, "beta": 3, "rc": 4,
    "": 10, "final": 10, "post": 20,
}
_VER_RE = re.compile(r"^v?(\d+(?:\.\d+)*)(.*)$", re.IGNORECASE)


def _parse_version(text: str) -> tuple[list[int], tuple[int, int]]:
    """Parse a version string into ``(release, (rank, num))``.

    ``release`` is the dotted-number part as a list of ints; ``(rank, num)`` is
    the final qualifier (plain release = rank 10). Comparison is done pairwise
    by :func:`_compare`.
    """
    m = _VER_RE.match((text or "").strip())
    if not m:
        return [0], (10, 0)
    release = [int(x) for x in m.group(1).split(".")]
    rest = re.sub(r"[^a-z0-9.]", "", m.group(2).lower())
    if not rest:
        return release, (10, 0)
    marker = None
    for key in ("dev", "rc", "post", "alpha", "beta"):
        if key in rest:
            marker = key
            break
    if marker is None:
        for key in ("a", "b"):
            if key in rest:
                marker = key
                break
    if marker is None:
        return release, (10, 0)
    mm = re.search(re.escape(marker) + r"([0-9]+)?", rest)
    if mm and mm.group(1):
        num = int(mm.group(1))
    else:
        nums = re.findall(r"([0-9]+)", rest)
        num = int(nums[-1]) if nums else 1
    return release, (_RANK.get(marker, 10), num)


def _compare(a: tuple[list[int], tuple[int, int]],
             b: tuple[list[int], tuple[int, int]]) -> int:
    """Return -1, 0 or 1 for ``a < b``, ``a == b``, ``a > b``."""
    ra, rb = a[0], b[0]
    n = max(len(ra), len(rb))
    for x, y in zip(ra + [0] * (n - len(ra)), rb + [0] * (n - len(rb))):
        if x != y:
            return -1 if x < y else 1
    fa, fb = a[1], b[1]
    if fa != fb:
        return -1 if fa < fb else 1
    return 0


def version_greater(a: str, b: str) -> bool:
    """True when version ``a`` is newer than version ``b``."""
    return _compare(_parse_version(a), _parse_version(b)) > 0


def current_version() -> str:
    from . import __version__

    return str(__version__)


# --------------------------------------------------------------------------- remote check

def remote_version(repo: str = DEFAULT_REPO, branch: str = DEFAULT_BRANCH,
                   timeout: float = DEFAULT_TIMEOUT) -> str:
    """Fetch the latest ``__version__`` from the GitHub default branch.

    Reads the raw package ``__init__.py`` from the repo (more reliable than
    release tags, which this project does not always create). Raises on
    network/parse failure so callers can degrade gracefully.
    """
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{_REMOTE_VERSION_FILE}"
    with urlopen(url, timeout=timeout) as r:  # noqa: S310 - deliberate external fetch
        text = r.read().decode("utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise UpdateError(f"could not parse the version from {url}")
    return m.group(1)


def check(root: Path, repo: str = DEFAULT_REPO, branch: str = DEFAULT_BRANCH,
          timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Compare installed vs remote version. Returns a plain dict."""
    current = current_version()
    latest = remote_version(repo, branch, timeout)
    return {
        "current": current,
        "latest": latest,
        "remote_source": f"{repo}@{branch}",
        "update_available": version_greater(latest, current),
    }


# --------------------------------------------------------------------------- daily-check state

def state_path(root: Path) -> Path:
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "update_state.json"


def load_state(root: Path) -> dict:
    p = state_path(root)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def save_state(root: Path, state: dict) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _due(state: dict, interval_h: float) -> bool:
    last = state.get("last_check_ts")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last))
    except ValueError:
        return True
    # be lenient about naive timestamps
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return age >= interval_h * 3600


def check_and_notify(root: Path, repo: str = DEFAULT_REPO, branch: str = DEFAULT_BRANCH,
                     interval_h: float = DEFAULT_INTERVAL_H,
                     timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """Run the daily check and return a notice string if an update is pending.

    Marks the state timestamp before the network call so a slow or failed check
    does not repeat on every invocation. Never raises for network failures —
    it returns ``None`` instead.
    """
    state = load_state(root)
    if not _due(state, interval_h):
        return None
    # stamp now (before the fetch) to avoid hammering GitHub on every run
    state["last_check_ts"] = datetime.now(timezone.utc).isoformat()
    save_state(root, state)
    try:
        info = check(root, repo, branch, timeout)
    except Exception:  # noqa: BLE001 - offline / unreachable: stay silent
        return None
    state.update({
        "latest": info["latest"],
        "current": info["current"],
        "remote_source": info["remote_source"],
        "update_available": info["update_available"],
    })
    save_state(root, state)
    if not info["update_available"]:
        return None
    return (f"pha {info['latest']} is available (you have {info['current']}). "
            f"Run `pha update` to install it.")


def maybe_notify_update(cfg) -> None:
    """Print a daily update notice (if due + pending). Best-effort, silent."""
    if not getattr(cfg, "update_enabled", True):
        return
    if os.environ.get("PHA_NO_UPDATE_CHECK"):
        return
    try:
        notice = check_and_notify(
            cfg.root, cfg.update_repo, cfg.update_branch,
            interval_h=cfg.update_interval_h, timeout=cfg.update_timeout,
        )
    except Exception:  # noqa: BLE001 - the notice must never break a command
        return
    if notice:
        print(notice, flush=True)


# --------------------------------------------------------------------------- applying the update

def project_root() -> Path:
    """The pha source checkout (directory containing pyproject.toml)."""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "pyproject.toml").exists():
            return p
    return here.parents[2]


def _is_git_checkout(root: Path) -> bool:
    return (root / ".git").exists()


def _git_update(root: Path, branch: str) -> str:
    if not shutil.which("git"):
        raise UpdateError("git is not installed; cannot update the pha checkout")
    subprocess.run(
        ["git", "-C", str(root), "fetch", "origin", branch],
        check=True, capture_output=True, text=True,
    )
    r = subprocess.run(
        ["git", "-C", str(root), "pull", "--ff-only", "origin", branch],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise UpdateError(
            "could not fast-forward the pha checkout (local changes or diverged). "
            f"Update it manually:\n  cd {root} && git pull\n"
            + (r.stderr or "").strip()
        )
    return r.stdout or ""


def _reinstall(repo: str, branch: str) -> str:
    url = f"https://github.com/{repo}.git"
    spec = f"git+{url}@{branch}"
    if shutil.which("uv"):
        subprocess.run(["uv", "tool", "install", "--force", spec], check=True)
    else:
        subprocess.run(
            [sys_executable(), "-m", "pip", "install", "--upgrade", spec],
            check=True,
        )
    return "reinstalled pha from " + url


def sys_executable() -> str:
    import sys

    return sys.executable


def install_update(repo: str = DEFAULT_REPO, branch: str = DEFAULT_BRANCH) -> str:
    """Apply the update. Returns a human-readable summary.

    Raises :class:`UpdateError` (or ``subprocess.CalledProcessError``) on
    failure so the CLI can report it.
    """
    root = project_root()
    if _is_git_checkout(root):
        _git_update(root, branch)
        return (f"updated the pha source checkout at {root} (branch {branch}); "
                f"the new version is active now.")
    return _reinstall(repo, branch)

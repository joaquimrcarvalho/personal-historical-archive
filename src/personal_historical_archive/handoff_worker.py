"""The resident hand-over worker: receive a hand-over, work it, send it back.

`pha handoff recv` stages a hand-over and PRINTS what to run — right when a human
or an agent is watching. The machine a hand-over exists for is the always-on box
with nobody at the keyboard, so this module runs the whole job unattended:

    pha handoff worker --send-to <owner>            # foreground; Ctrl-C to stop
    pha handoff worker --send-to <owner> --install  # macOS user LaunchAgent
    pha handoff worker --status | --uninstall

Two deliberate choices:

- **It drives the documented CLI as a subprocess** (`in` → `work --resume` →
  `back`) rather than importing the pipeline. The hand-over is `pha`'s external
  interface; each stage then reports its own outcome, and the worker stays an
  orchestrator instead of becoming a second implementation of the pipeline that
  can drift from it. The archive's own rule — work through the interface, do not
  reach into internals — is what decides this.
- **Both machines are not always awake, and only one of them is a real error.**
  The worker retries while the owner is offline (`PeerUnavailable`), because the
  whole point is that the MacBook is closed and will come back; a misspelled or
  ambiguous peer is NOT retried forever, because no amount of waiting fixes it.

The LaunchAgent runs the PATH-proof form recorded in the archive's location
trace (`<python> -m personal_historical_archive`), so it works from the login
context with a minimal PATH — the reason that trace exists.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from . import location
from . import handoff_transport as T
from .config import Config
from .handoff import HandoffError

WORKER_LABEL = "com.personal-historical-archive.handoff-worker"

# `handoff work` exits 2 when its plan is empty (nothing left to do) — not a
# failure for the worker, which should go on and build the result.
_WORK_NOTHING_TO_DO = 2


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def log_path(cfg: Config) -> Path:
    return cfg.archive_dir / ".pha" / "handoff-worker.log"


def err_path(cfg: Config) -> Path:
    return cfg.archive_dir / ".pha" / "handoff-worker.err.log"


# --------------------------------------------------------------------------
# running the CLI
# --------------------------------------------------------------------------

def pha_base(cfg: Config) -> tuple[list[str], dict]:
    """The PATH-proof `pha` invocation and the env that pins this archive.

    Always the interpreter form (`<python> -m personal_historical_archive`), even
    when a `pha` shim was used to get here: a LaunchAgent starts with a minimal
    PATH, and this form needs no PATH entry, no activated venv and no shim.
    """
    record = location.build_location(cfg)
    return list(record["pha"]["module"]), dict(record["pha"]["env"])


def run_step(cfg: Config, args: list[str], base: list[str] | None = None,
             env: dict | None = None) -> int:
    """Run one `pha <args>` step, streaming its output to this process's."""
    base = base or pha_base(cfg)[0]
    env = env if env is not None else pha_base(cfg)[1]
    argv = [*base, *args]
    _log(f"$ pha {' '.join(args)}")
    full = {**os.environ, **env}
    try:
        return subprocess.call(argv, env=full)
    except KeyboardInterrupt:
        raise
    except OSError as e:
        _log(f"! could not run {argv[0]}: {e}")
        return 127


# --------------------------------------------------------------------------
# one hand-over, end to end
# --------------------------------------------------------------------------

def work_archive(cfg: Config, archive: Path, send_to: str, *,
                 poll_s: float = 60.0, max_retries: int = 0,
                 base: list[str] | None = None, env: dict | None = None) -> dict:
    """Stage, import, work, pack and send back ONE received archive.

    Returns a report; `ok` is False if any step failed. A failure leaves the
    work in the staging directory and says so — the worker never pretends a
    hand-over succeeded.
    """
    info = T.describe(cfg, archive)
    if info["kind"] != T.KIND_PAYLOAD:
        _log(f"  {archive.name} is a {info['kind']}, not a hand-out — ignoring")
        return {"ok": False, "archive": str(archive), "kind": info["kind"],
                "reason": "not a hand-out payload"}

    staged = Path(info["staged"])
    _log(f"hand-out {info['handoff_id'] or staged.name}: {info['documents']} "
         f"document(s) -> {staged}")

    rc = run_step(cfg, ["handoff", "in", str(staged)], base=base, env=env)
    if rc != 0:
        _log(f"! `handoff in` failed (exit {rc}); left staged at {staged}")
        return {"ok": False, "handoff_id": info["handoff_id"],
                "staged": str(staged), "step": "in", "rc": rc}

    rc = run_step(cfg, ["handoff", "work", str(staged), "--resume"],
                  base=base, env=env)
    if rc not in (0, _WORK_NOTHING_TO_DO):
        _log(f"! `handoff work` failed (exit {rc}); left staged at {staged}")
        return {"ok": False, "handoff_id": info["handoff_id"],
                "staged": str(staged), "step": "work", "rc": rc}
    if rc == _WORK_NOTHING_TO_DO:
        _log("  nothing to work (already complete)")

    rc = run_step(cfg, ["handoff", "back", str(staged)], base=base, env=env)
    if rc != 0:
        _log(f"! `handoff back` failed (exit {rc}); work is kept at {staged}")
        return {"ok": False, "handoff_id": info["handoff_id"],
                "staged": str(staged), "step": "back", "rc": rc}

    result_dir = staged.parent / f"{staged.name}-back"
    sent = send_back(cfg, result_dir, send_to, poll_s=poll_s,
                     max_retries=max_retries)
    return {"ok": bool(sent.get("sent")), "handoff_id": info["handoff_id"],
            "staged": str(staged), "result": str(result_dir), "send": sent}


def send_back(cfg: Config, result_dir: Path, peer_query: str, *,
              poll_s: float = 60.0, max_retries: int = 0) -> dict:
    """Taildrop a result to the owner, waiting for the owner to come back.

    The archive is packed ONCE and re-sent: re-packing per attempt would re-tar
    the whole result while waiting for a laptop to be opened.
    """
    archive = T.pack(result_dir, verbose=False)
    _log(f"  result packed: {archive.name} ({archive.stat().st_size} bytes)")
    attempt = 0
    while True:
        attempt += 1
        try:
            peer = T.resolve_peer(peer_query)
            T.send(archive, peer, verbose=False)
            _log(f"  sent {archive.name} to {peer.label}"
                 + (f" (attempt {attempt})" if attempt > 1 else ""))
            return {"sent": True, "to": peer.hostname, "to_ip": peer.ip,
                    "archive": str(archive), "attempts": attempt}
        except T.PeerUnavailable as e:
            # The laptop is closed. Waiting is the entire point of a resident
            # worker — but cap it when asked, so `--once` can finish.
            if max_retries and attempt >= max_retries:
                _log(f"  ! giving up after {attempt} attempt(s): {e}")
                return {"sent": False, "reason": str(e), "attempts": attempt,
                        "archive": str(archive)}
            _log(f"  owner not reachable yet ({e})")
            _log(f"  retrying in {poll_s:.0f}s — the result is safe at {archive}")
            time.sleep(poll_s)
        except T.TransportError as e:
            # Not a waiting problem: a bad peer name never fixes itself.
            _log(f"  ! cannot send the result: {e}")
            _log(f"    it is kept at {archive} — send it by hand with "
                 f"`pha handoff out`/`tailscale file cp` once resolved")
            return {"sent": False, "reason": str(e), "attempts": attempt,
                    "archive": str(archive)}


def worker_loop(cfg: Config, send_to: str, *, once: bool = False,
                poll_s: float = 60.0, max_retries: int = 0) -> list[dict]:
    """Watch the Tailscale inbox and work every hand-over that arrives.

    `once` drains what is waiting and returns (a cron-style run, and what makes
    this testable); without it the loop runs until Ctrl-C.
    """
    base, env = pha_base(cfg)
    _log(f"hand-over worker started — archive {cfg.archive_dir}, "
         f"results to {send_to!r}")
    _log(f"log: {log_path(cfg)}")
    reports: list[dict] = []
    while True:
        T.receive(cfg, wait=not once, verbose=False)
        archives = T.candidates(cfg)
        if not archives:
            if once:
                _log("nothing waiting")
                return reports
            continue
        for archive in archives:
            try:
                reports.append(work_archive(cfg, archive, send_to, poll_s=poll_s,
                                            max_retries=max_retries,
                                            base=base, env=env))
            except HandoffError as e:
                # One bad archive must not kill a resident worker.
                _log(f"! {archive.name}: {e}")
                reports.append({"ok": False, "archive": str(archive),
                                "reason": str(e)})
        if once:
            return reports


# --------------------------------------------------------------------------
# the LaunchAgent (macOS)
# --------------------------------------------------------------------------

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{WORKER_LABEL}.plist"


def worker_plist(cfg: Config, send_to: str) -> bytes:
    """The LaunchAgent definition (pure — nothing is written).

    `KeepAlive` plus a `ThrottleInterval` means a crash restarts the worker
    without spinning; `RunAtLoad` means it is up after a reboot with no login
    ritual.
    """
    base, env = pha_base(cfg)
    payload = {
        "Label": WORKER_LABEL,
        "ProgramArguments": [*base, "handoff", "worker", "--send-to", send_to],
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "StandardOutPath": str(log_path(cfg)),
        "StandardErrorPath": str(err_path(cfg)),
    }
    return plistlib.dumps(payload)


def _gui_target() -> str:
    return f"gui/{os.getuid()}"


def install_worker(cfg: Config, send_to: str, *, dry_run: bool = False) -> dict:
    """Install + start the worker as a user LaunchAgent.

    A per-user LaunchAgent needs **no administrator password** and changes only
    `~/Library/LaunchAgents` and the two log files under the archive's `.pha/`.
    """
    if sys.platform != "darwin":
        raise HandoffError(
            "`worker --install` writes a macOS LaunchAgent. On this platform, "
            "schedule the worker yourself — it is just:\n"
            f"  {' '.join(pha_base(cfg)[0])} handoff worker --send-to {send_to}\n"
            "with PHA_HOME and PHA_ARCHIVE_DIR set (see pha-location.md)."
        )
    if not send_to.strip():
        raise HandoffError(
            "--install needs --send-to <peer>: the worker must know which "
            "machine owns the documents (`pha handoff peers` lists them)."
        )

    path = plist_path()
    target = _gui_target()
    if dry_run:
        return {"installed": False, "dry_run": True, "plist": str(path),
                "label": WORKER_LABEL, "log": str(log_path(cfg)),
                "would_run": [*pha_base(cfg)[0], "handoff", "worker",
                              "--send-to", send_to]}

    path.parent.mkdir(parents=True, exist_ok=True)
    data = worker_plist(cfg, send_to)
    if path.exists():
        _bootstrap_remove(path)
    path.write_bytes(data)

    loaded = _bootstrap_load(path)
    return {"installed": True, "loaded": loaded, "plist": str(path),
            "label": WORKER_LABEL, "log": str(log_path(cfg)),
            "command": f"launchctl print {target}/{WORKER_LABEL}"}


def uninstall_worker() -> dict:
    path = plist_path()
    removed = _bootstrap_remove(path)
    existed = path.exists()
    if existed:
        path.unlink()
    return {"uninstalled": existed, "stopped": removed, "plist": str(path)}


def worker_status(cfg: Config) -> dict:
    path = plist_path()
    installed = path.exists()
    running = False
    detail = ""
    if installed and sys.platform == "darwin":
        proc = subprocess.run(
            ["launchctl", "print", f"{_gui_target()}/{WORKER_LABEL}"],
            capture_output=True, text=True)
        running = proc.returncode == 0
        if not running:
            lines = (proc.stderr or "").strip().splitlines()
            detail = lines[-1] if lines else ""
    return {"installed": installed, "running": running, "label": WORKER_LABEL,
            "plist": str(path), "log": str(log_path(cfg)),
            "err_log": str(err_path(cfg)), "detail": detail}


def _bootstrap_load(path: Path) -> bool:
    """`launchctl bootstrap` (macOS 10.11+) with the legacy `load -w` fallback."""
    new = subprocess.run(["launchctl", "bootstrap", _gui_target(), str(path)],
                         capture_output=True, text=True)
    if new.returncode == 0:
        return True
    legacy = subprocess.run(["launchctl", "load", "-w", str(path)],
                            capture_output=True, text=True)
    if legacy.returncode != 0:
        raise HandoffError(
            f"could not start the worker: {legacy.stderr.strip() or legacy.stdout.strip()}"
        )
    return True


def _bootstrap_remove(path: Path) -> bool:
    service = f"{_gui_target()}/{WORKER_LABEL}"
    new = subprocess.run(["launchctl", "bootout", service],
                         capture_output=True, text=True)
    if new.returncode == 0:
        return True
    legacy = subprocess.run(["launchctl", "unload", "-w", str(path)],
                            capture_output=True, text=True)
    return legacy.returncode == 0

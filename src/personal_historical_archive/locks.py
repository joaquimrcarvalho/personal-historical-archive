"""Model-server locks — one model per model-server, not one per archive.

pha talks to model servers, and a server that loads models just in time keeps
**one** of them resident (LM Studio's default Auto-Evict unloads the previous
JIT-loaded model). Two jobs that talk to the same server therefore evict each
other's models; jobs that talk to *different* servers do not compete at all.
The old rule ("one local-model job at a time", enforced by one lock file per
archive) was both too coarse and too narrow: it serialised remote-model work
that cannot collide, while two archives on one machine took two different locks
and could load two models into the same server.

A job now holds a lock on every **server key** it may talk to; two jobs may run
concurrently iff their key sets are disjoint.

Why the key is declared, not inferred
-------------------------------------
The endpoint URL cannot prove which device answers. LM Link serves a remote
model on ``localhost``; an SSH tunnel makes a remote look local; the same box
reached as a LAN IP looks remote. So:

* same normalised endpoint  → same instance → must serialise (sound)
* different endpoint        → different machine → safe to parallelise (UNSOUND)

An endpoint URL still *is* a key (the embed model has no model file to carry a
label), but it never proves locality — only that two jobs naming the same
endpoint share it. A model file with no ``server:`` is treated as belonging
anywhere, i.e. it takes the wildcard ``*``, which intersects every other key
set and reproduces today's global behaviour exactly.

Capacity
--------
A server admits ``slots`` concurrent jobs (default 1), declared in config.yaml::

    servers:
      mac-studio: {slots: 2}     # vision + embed pre-loaded by hand

The knob is declared because pha cannot read the server's auto-evict settings
and never pre-loads or unloads anything itself.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

WILDCARD = "*"

# A lock whose pid looks alive is still stolen past this age: pids get reused,
# and a stale lock must never wedge every future job (same rule as before).
STALE_AFTER_S = 6 * 3600

# Local engines run as subprocesses and touch no model server at all.
LOCAL_ENGINES = ("tesseract", "liteparse")


# --------------------------------------------------------------------------- paths and keys

def lock_dir() -> Path:
    """The user-global directory holding server lock files.

    User-global on purpose: two archives on one machine that share a model
    server must serialise, and archive-local locks cannot see each other.
    ``PHA_LOCK_DIR`` overrides it (tests, unusual setups).
    """
    env = os.environ.get("PHA_LOCK_DIR")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "pha" / "locks"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "pha" / "locks"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "pha" / "locks"
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "pha" / "locks"


def _expand_env(value: str) -> str:
    """Expand ``${VAR}``/``$VAR`` so two spellings of one endpoint agree."""
    try:
        return os.path.expandvars(value) if value else ""
    except Exception:  # noqa: BLE001 - a weird value must not break locking
        return value or ""


def normalise_endpoint(url: str) -> str:
    """``scheme://host:port`` for an endpoint URL, or "" when it names no host.

    Fills in the default port and unifies the loopback spellings, so
    ``http://localhost:1234/v1``, ``http://127.0.0.1:1234`` and
    ``http://127.0.0.1:80/v1`` (http) are each ONE key when they are genuinely
    the same endpoint. It does NOT try to decide locality — see the module
    docstring.
    """
    url = _expand_env((url or "").strip())
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    if host in ("localhost", "::1"):
        host = "127.0.0.1"  # the same machine, reached by name or by colon
    scheme = (parts.scheme or "http").lower()
    try:
        port = parts.port
    except ValueError:
        return ""
    if port is None:
        port = 443 if scheme == "https" else 80
    return f"{scheme}://{host}:{port}"


def stage_key(stage) -> str:
    """Server key for a resolved palaeographer/editor/encoder.

    ``""`` when the stage talks to no server (a local OCR/parse engine). An
    unlabelled stage takes the wildcard: its provenance is unknown, so assume
    it shares a server with everything (today's global behaviour).
    """
    if stage is None:
        return ""
    if (getattr(stage, "engine", "") or "").strip().lower() in LOCAL_ENGINES:
        return ""
    declared = (getattr(stage, "server", "") or "").strip()
    if declared:
        return declared
    return WILDCARD


def endpoint_key(url: str) -> str:
    """Server key for an endpoint that carries no model file (the embed model)."""
    return normalise_endpoint(url) or WILDCARD


def embed_key(cfg) -> str:
    """Server key for the embedding model.

    ``embeddings.server:`` labels it explicitly (the clean way to say "this
    embed model lives on mac-studio"); otherwise the endpoint is the key, which
    is enough to make two archives sharing one LM Studio serialise.
    """
    declared = (getattr(cfg, "embed_server", "") or "").strip()
    if declared:
        return declared
    return endpoint_key(getattr(cfg, "embed_base_url", "") or "")


def job_keys(*stages, cfg=None, embed: bool = False, extra: Iterable[str] | None = None) -> list[str]:  # noqa: F821
    """The sorted, de-duplicated key set for a job.

    Empty entries (local engines) are dropped. An empty result means the job
    touches no model server and needs no lock at all.
    """
    keys = {stage_key(s) for s in stages}
    if embed and cfg is not None:
        keys.add(embed_key(cfg))
    for k in (extra or ()):
        keys.add(k)
    keys.discard("")
    return sorted(keys)


# --------------------------------------------------------------------------- lock files

def _key_hash(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _slot_path(key: str, slot: int) -> Path:
    return lock_dir() / f"{_key_hash(key)}.{slot}.lock"


def _slot_paths(key: str) -> list[Path]:
    return sorted(lock_dir().glob(f"{_key_hash(key)}.*.lock"))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) would TERMINATE the process on Windows; probe
        # existence instead (OpenProcess with query access, stdlib only).
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False


def _holder(path: Path) -> tuple[int, str]:
    """(pid, label) recorded in a lock file; (0, "") when unreadable."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0, ""
    pid_s, _, label = raw.partition(" ")
    try:
        pid = int(pid_s)
    except ValueError:
        pid = 0
    return pid, label.strip()


def _stale(path: Path, pid: int) -> bool:
    """Is this lock file reclaimable?

    - A recorded pid that is no longer alive -> stale (its holder died).
    - A pid-less file (created but not yet written) -> stale only after the
      6 h age threshold: it may belong to a job that is still starting, and
      stealing it would let two jobs run together.
    - A lock OLDER than 6 h is always stale even when its pid looks alive
      (pids get reused; a stale lock must never wedge every future job).
    """
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = 0.0
    if pid > 0:
        return pid != os.getpid() and not _pid_alive(pid)
    return age > STALE_AFTER_S


@dataclass
class LockHandle:
    """The slots a job holds, or why it was refused."""

    held: list[Path] = field(default_factory=list)
    degraded: bool = False        # locking impossible: proceeding unlocked
    busy_key: str = ""
    busy_holder: str = ""
    waited_s: float = 0.0         # how long a `wait_s` queue actually waited
    timeout: bool = False         # the queue gave up at its ceiling

    @property
    def ok(self) -> bool:
        return not self.busy_key

    def reason(self) -> str:
        """A refusal message naming the busy server and its holder."""
        if not self.busy_key:
            return ""
        who = f" ({self.busy_holder})" if self.busy_holder else ""
        waited = (f" [waited {_fmt_dur(self.waited_s)} before giving up]"
                  if self.timeout and self.waited_s else "")
        if self.busy_key == WILDCARD:
            return (f"another job{who} is using a model with no declared server — "
                    "one model per model-server at a time; wait for it to finish, or "
                    "declare `server:` in the model files (and, if the machine really "
                    "holds both models, slots in config.yaml: servers: {<id>: {slots: 2}})"
                    + waited)
        return (f"model server '{self.busy_key}' is busy{who} — one model per "
                "model-server at a time; wait for it to finish, or declare more "
                "capacity in config.yaml: servers: {%s: {slots: 2}}" % self.busy_key
                + waited)


def _fmt_dur(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 10:
        return f"{s:.1f}s"          # a short queue must not print "0s"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.0f}m"
    return f"{s / 3600:.1f}h"


def _acquire_key(cfg, key: str, label: str) -> tuple[str, Path | None, str]:
    """Try to take one slot of `key`.

    Returns (status, path, holder) where status is "held" | "already" |
    "busy" | "degraded".
    """
    slots = max(1, cfg.server_slots(key)) if cfg is not None else 1
    for _attempt in range(20):
        reclaimed = False
        for slot in range(1, slots + 1):
            path = _slot_path(key, slot)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                pid, holder = _holder(path)
                if pid == os.getpid():
                    return "already", path, holder   # idempotent re-acquire
                if _stale(path, pid):
                    # Reclaim. A racer may replace the file between the staleness
                    # check and the unlink, so loop back to the atomic create.
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    reclaimed = True
                    break
                if slot == slots:
                    return "busy", None, holder      # every slot is held
                continue
            except OSError:
                return "degraded", None, ""
            else:
                try:
                    os.write(fd, f"{os.getpid()} {label}".encode("utf-8"))
                finally:
                    os.close(fd)
                return "held", path, ""
        if not reclaimed:
            return "busy", None, ""
    return "busy", None, ""


def _first_foreign(key: str | None) -> tuple[bool, str]:
    """(found, holder) for the first live lock we do NOT hold.

    `key=None` scans every key, which is how the wildcard cross-check below
    sees a job that holds named keys.
    """
    try:
        files = sorted(lock_dir().glob("*.lock")) if key is None else _slot_paths(key)
    except OSError:
        return False, ""
    for path in files:
        pid, label = _holder(path)
        if pid == os.getpid():
            continue
        if not _stale(path, pid):
            return True, label
    return False, ""


def acquire(cfg, keys, label: str = "pha job", wait_s: float = 0.0,
            verbose: bool = False) -> LockHandle:
    """Take a slot on every server key the job may touch.

    Keys are acquired in sorted order and released on refusal, so two jobs
    needing {A,B} and {B,A} cannot deadlock. Never raises: a refusal is a
    handle with `ok == False` (and `.reason()`), an unwritable lock directory
    is a `degraded` handle that proceeds without locking, as before.

    `wait_s` > 0 QUEUES instead of refusing at once: it retries until that
    ceiling, naming the holder and how long it has held the slot (`verbose`).
    A ceiling is mandatory — "waiting for the embedding server" on a machine
    where one LM Studio serves every model can mean waiting for a scan that runs
    for hours, so there is no unbounded form and `wait_s=0` (the default) keeps
    today's refuse-immediately behaviour. On timeout the refused handle carries
    `timeout=True` and `waited_s`.
    """
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return LockHandle()
    try:
        lock_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        return LockHandle(degraded=True)
    wait_s = max(0.0, float(wait_s or 0.0))
    started = time.monotonic()
    deadline = started + wait_s
    announced = False
    while True:
        handle = _take_all(cfg, wanted, label)
        if handle.ok or handle.degraded or not wait_s:
            if announced and verbose and handle.ok:
                print(f"  lock acquired after {_fmt_dur(time.monotonic() - started)}",
                      flush=True)
            return handle
        now = time.monotonic()
        if now >= deadline:
            handle.timeout = True
            handle.waited_s = now - started
            return handle
        if verbose and not announced:
            age = holder_age_s(handle.busy_key)
            if age is None:
                age = holder_age_s(None)
            who = f" ({handle.busy_holder})" if handle.busy_holder else ""
            held = f", held for {_fmt_dur(age)}" if age else ""
            print(f"  waiting up to {_fmt_dur(wait_s)} for model server "
                  f"'{handle.busy_key}'{who}{held} — Ctrl-C to give up", flush=True)
            announced = True
        time.sleep(min(2.0, max(0.1, deadline - now)))


def _take_all(cfg, wanted: list[str], label: str) -> LockHandle:
    """One attempt at every key (the body of `acquire`, factored out)."""
    handle = LockHandle()
    for key in wanted:
        status, path, holder = _acquire_key(cfg, key, label)
        if status == "busy":
            release(handle)
            return LockHandle(busy_key=key, busy_holder=holder)
        if status == "degraded":
            release(handle)
            return LockHandle(degraded=True)
        if path is not None and path not in handle.held:
            handle.held.append(path)
    # The wildcard means "unknown server, assume shared", so it intersects every
    # other key set — and every key set intersects it. Both directions are
    # checked AFTER we created our own slots, so whichever job creates last sees
    # the other and backs off; the residual race can only refuse both, never
    # admit both.
    if WILDCARD in wanted:
        found, holder = _first_foreign(None)
        if found:
            release(handle)
            return LockHandle(busy_key=WILDCARD, busy_holder=holder)
    else:
        found, holder = _first_foreign(WILDCARD)
        if found:
            release(handle)
            return LockHandle(busy_key=WILDCARD, busy_holder=holder)
    return handle


def holder_age_s(key: str | None = None) -> float | None:
    """Seconds the current holder of `key` (any key when None) has held a slot.

    Observation only, for the `--wait` message ("held for 1h12m"): the slot file's
    mtime is when the job took it."""
    try:
        files = sorted(lock_dir().glob("*.lock")) if key is None else _slot_paths(key)
    except OSError:
        return None
    ages: list[float] = []
    for path in files:
        pid, _label = _holder(path)
        if pid == os.getpid() or _stale(path, pid):
            continue
        try:
            ages.append(max(0.0, time.time() - path.stat().st_mtime))
        except OSError:
            continue
    return min(ages) if ages else None


def release(handle: LockHandle | None) -> None:
    """Release every slot this handle holds (only files recorded to our pid)."""
    if handle is None:
        return
    for path in handle.held:
        try:
            if path.exists() and _holder(path)[0] == os.getpid():
                path.unlink()
        except OSError:
            pass
    handle.held = []


# --------------------------------------------------------------------------- observation

def job_running(cfg, key: str | None = None, include_self: bool = False) -> bool:
    """Is a live job holding `key` (or any key, when None)?

    Observation only — this never takes a lock, so a read-only command (like
    `pha search`, which embeds the query) can decide to skip loading a model
    instead of blocking behind a two-hour scan.
    """
    try:
        files = sorted(lock_dir().glob("*.lock")) if key is None else _slot_paths(key)
    except OSError:
        return False
    for path in files:
        pid, _label = _holder(path)
        if not include_self and pid == os.getpid():
            continue
        if not _stale(path, pid):
            return True
    return False


def any_job_running(include_self: bool = False) -> bool:
    """Is any live job (any archive, any server) holding a slot?"""
    return job_running(None, include_self=include_self)


def holder_label(key: str | None = None) -> str | None:
    """The job label of a live holder of `key` (any key when None), or None.

    Used to name the holder in a degrade message ("a model job (pha scan) is
    using the embedding server").
    """
    found, label = _first_foreign(key)
    return label if found else None

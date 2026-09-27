"""Move a hand-over payload between two machines over the tailnet.

`handoff.py` merges work; this module only moves the bytes. `pha handoff out`
has always written a PAYLOAD DIRECTORY and left the trip to the user — in
practice ssh plus shared keys, three technical steps per hand-over (a key, an
address, a remote path), twice. This module replaces that with Taildrop, which
is peer-to-peer over WireGuard between devices already authenticated to the same
tailnet:

    pha handoff out  <targets> --send <peer>   # export, tar, send
    pha handoff recv                           # receive, unpack, say what to run
    pha handoff back <dir>     --send <peer>   # the return trip
    pha handoff peers                          # who can receive

Design constraints, in order of importance:

- **The payload format does not change.** A tar (`<id>.pha-handoff.tgz`) is a
  transport envelope only; it is unpacked back to a directory before
  `handoff.import_handoff` / `handoff.build_result` ever see it. `HANDOFF_VERSION`
  stays 1 and a USB stick or rsync keeps working.
- **A tar from the network is untrusted input, even on a tailnet.** `unpack`
  refuses absolute paths, `..` and every kind of link, so a malicious or merely
  buggy peer cannot write outside the staging directory.
- **No daemon, no port, no key.** Nothing here listens; `tailscale file` drives
  the local Tailscale daemon. The trust boundary is tailnet membership, which is
  Tailscale's job, not pha's.

Receiving is deliberately **stage-and-tell**: `recv` unpacks and prints the
`handoff in` / `handoff fetch` command rather than applying a merge on its own,
matching pha's dry-run discipline elsewhere.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import Config
from .handoff import (HANDOFF_FORMAT, MANIFEST_NAME, RESULT_NAME, HandoffError,
                      handoff_dir)
from .model_client import find_engine_binary

PAYLOAD_SUFFIX = ".pha-handoff.tgz"
RESULT_SUFFIX = ".pha-result.tgz"

KIND_PAYLOAD = "payload"
KIND_RESULT = "result"

# `tailscale file cp` needs a `<target>:` (trailing colon) target.
_TARGET_TIMEOUT_S = 15.0


class TransportError(HandoffError):
    """Anything that stops the payload moving — a missing CLI, an unknown or
    offline peer, a failed send, a refused archive. Subclasses `HandoffError`
    so the existing `pha handoff` error handling reports it the same way."""


class PeerUnavailable(TransportError):
    """The peer cannot receive *right now* — it is asleep, or Tailscale is not
    up on one end.

    Separate from a plain `TransportError` because it is the one failure that is
    worth WAITING on: the resident worker (`pha handoff worker`) retries this
    until the owner's machine comes back, and must not retry a genuine error
    (an ambiguous or misspelled peer) forever.
    """


# --------------------------------------------------------------------------
# the tailscale CLI
# --------------------------------------------------------------------------

def tailscale_bin() -> str | None:
    """Resolved `tailscale` executable, or None.

    Uses the same resolution as the OCR engines (`find_engine_binary`): pha's
    own PATH first, then the PATH the user's login shell would provide, then
    pha's interpreter bin dir — so a tailscale installed for the *user* is found
    even when pha runs from a GUI/agent context with a minimal PATH.
    """
    return find_engine_binary("tailscale")


def require_tailscale() -> str:
    exe = tailscale_bin()
    if exe:
        return exe
    raise TransportError(
        "tailscale is not installed or not on PATH. Install Tailscale on BOTH "
        "machines and log them into the same tailnet, or move the payload "
        "yourself and keep using the directory commands (`pha handoff in`)."
    )


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:  # racy uninstall between resolve and run
        raise TransportError(f"could not run {Path(cmd[0]).name}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise TransportError(f"{Path(cmd[0]).name} timed out after {timeout:g}s") from e


def _fail(what: str, proc: subprocess.CompletedProcess) -> TransportError:
    detail = (proc.stderr or proc.stdout or "").strip()
    return TransportError(f"{what} failed: {detail or f'exit {proc.returncode}'}")


# --------------------------------------------------------------------------
# peers
# --------------------------------------------------------------------------

@dataclass
class Peer:
    """One Taildrop target, as `tailscale file cp --targets` reports it."""

    ip: str
    hostname: str
    status: str = ""

    @property
    def online(self) -> bool:
        return not self.status.strip().lower().startswith("offline")

    @property
    def label(self) -> str:
        return f"{self.hostname} ({self.ip})" + (f" — {self.status}" if self.status else "")


def peers() -> list[Peer]:
    """Devices this machine can Taildrop to.

    `tailscale file cp --targets` is the authoritative list (it prints only
    devices that accept files) and it prints `IP<TAB>hostname<TAB>status`, which
    is also the handle `send` needs — so one call answers both "who" and "how to
    address them".
    """
    exe = require_tailscale()
    proc = _run([exe, "file", "cp", "--targets"], timeout=_TARGET_TIMEOUT_S)
    if proc.returncode != 0:
        raise _fail("listing tailnet devices", proc)

    out: list[Peer] = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if not parts or not parts[0]:
            continue
        ip = parts[0]
        host = parts[1] if len(parts) > 1 and parts[1] else ip
        # the status column is free text ("offline; last seen 3h ago") and may
        # itself contain tabs, so join whatever follows rather than index it.
        status = " ".join(p for p in parts[2:] if p)
        out.append(Peer(ip=ip, hostname=host, status=status))
    return out


def resolve_peer(query: str) -> Peer:
    """A peer from a name substring or an IP, with actionable failures.

    Ambiguity is an error rather than a guess: sending a hand-over to the wrong
    machine leases the documents out and moves real data.
    """
    query = (query or "").strip()
    if not query:
        raise TransportError("no peer given")
    known = peers()
    if not known:
        raise PeerUnavailable(
            "this machine sees no Taildrop targets. Is Tailscale running and "
            "logged in on both machines?"
        )

    for p in known:  # an address is unambiguous
        if p.ip == query:
            return _require_online(p)

    q = query.lower()
    exact = [p for p in known if p.hostname.lower() == q]
    hits = exact or [p for p in known if q in p.hostname.lower() or q in p.ip]
    if not hits:
        listing = "\n".join(f"  {p.label}" for p in known)
        raise TransportError(f"no tailnet device matches {query!r}. Devices:\n{listing}")
    if len(hits) > 1:
        listing = "\n".join(f"  {p.label}" for p in hits)
        raise TransportError(
            f"{query!r} matches {len(hits)} devices — be more specific:\n{listing}"
        )
    return _require_online(hits[0])


def _require_online(p: Peer) -> Peer:
    if not p.online:
        raise PeerUnavailable(
            f"{p.label} — Taildrop is a direct transfer, so the machine must be "
            f"awake to receive. Wake it and try again."
        )
    return p


# --------------------------------------------------------------------------
# the envelope: tar in, safe tar out
# --------------------------------------------------------------------------

def _manifest(directory: Path) -> tuple[str, dict] | None:
    """(kind, manifest payload) for a directory, or None if it carries neither.

    The manifest's `format` is checked, not just the filename: what makes an
    archive safe to stage is that pha wrote it, and a peer chooses the file it
    sends.
    """
    for name, kind in ((MANIFEST_NAME, KIND_PAYLOAD), (RESULT_NAME, KIND_RESULT)):
        p = directory / name
        if not p.is_file():
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        fmt = payload.get("format")
        if fmt and fmt != HANDOFF_FORMAT:
            raise TransportError(f"{p} is not a pha hand-off (format {fmt!r})")
        return kind, payload
    return None


def read_kind(directory: Path) -> str:
    """payload / result, from the manifest a directory carries."""
    manifest = _manifest(directory)
    if manifest:
        return manifest[0]
    raise TransportError(
        f"{directory} is neither a hand-out payload nor a result (no "
        f"{MANIFEST_NAME} or {RESULT_NAME})"
    )


def read_id(directory: Path) -> str:
    """The hand-off id a payload/result directory carries (never inferred)."""
    manifest = _manifest(directory)
    return str(manifest[1].get("handoff_id") or "") if manifest else ""


def pack(directory: Path, kind: str | None = None, verbose: bool = True) -> Path:
    """Tar a payload/result directory into ONE file beside it.

    One file, not a directory tree: Taildrop moves files, and a single archive
    makes the transfer atomic and gives `recv` something unambiguous to
    recognise. The suffix records the kind, which is only a hint — `recv`
    re-checks the manifest inside.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise TransportError(f"not a directory: {directory}")
    kind = kind or read_kind(directory)
    suffix = PAYLOAD_SUFFIX if kind == KIND_PAYLOAD else RESULT_SUFFIX
    hid = _safe_name(read_id(directory) or directory.name)
    archive = directory.parent / f"{hid}{suffix}"
    if archive.exists():
        archive.unlink()

    if verbose:
        print(f"  packing {directory.name} -> {archive.name} …")
    t0 = time.time()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(directory, arcname=".")
    if verbose:
        size = archive.stat().st_size
        print(f"    {_human(size)} in {time.time() - t0:.1f}s")
    return archive


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def unpack(archive: Path, dest: Path, verbose: bool = True) -> Path:
    """Extract an archive into `dest`, refusing anything that could escape it.

    A tar that arrives over a network is untrusted input even on a tailnet. A
    payload contains regular files and directories only, so anything else —
    an absolute path, a `..` component, a symlink/hardlink (how you escape a
    directory without writing `..` yourself) — is refused outright rather than
    sanitised. Refusing the whole archive keeps the failure legible.
    """
    archive = Path(archive)
    dest = Path(dest)
    if not archive.is_file():
        raise TransportError(f"no such archive: {archive}")
    dest.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts:
                raise TransportError(
                    f"refusing {archive.name}: member {m.name!r} escapes the "
                    f"staging directory"
                )
            if m.issym() or m.islnk():
                raise TransportError(
                    f"refusing {archive.name}: member {m.name!r} is a link"
                )
            if not (m.isfile() or m.isdir()):
                raise TransportError(
                    f"refusing {archive.name}: member {m.name!r} is neither a "
                    f"regular file nor a directory"
                )
        # `filter` (3.12, backported to 3.11.4) is belt-and-braces on top of the
        # checks above; older 3.11.x does not accept the keyword.
        try:
            tar.extractall(dest, members=members, filter="data")
        except TypeError:
            tar.extractall(dest, members=members)

    if verbose:
        print(f"  unpacked {archive.name} -> {dest}")
    return dest


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------

def staging_dir(cfg: Config) -> Path:
    """Where received archives land: machine-local, beside the leases."""
    return handoff_dir(cfg) / "incoming"


def _archives_in(directory: Path) -> list[Path]:
    """Hand-over archives in a directory, tolerating one we cannot read.

    `~/Downloads` is not always readable (macOS TCC, a sandboxed/agent context,
    a restrictive umask) and that must not fail a receive — a missing source is
    simply nothing to stage.
    """
    if not directory.is_dir():
        return []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    found: list[Path] = []
    for p in entries:
        try:
            if p.is_file() and p.name.endswith((PAYLOAD_SUFFIX, RESULT_SUFFIX)):
                found.append(p)
        except OSError:
            continue
    return sorted(found, key=lambda p: p.stat().st_mtime)


def _downloads_dir() -> Path | None:
    """macOS/Windows Tailscale apps may deliver into Downloads instead of the
    CLI inbox; returns it when it exists."""
    d = Path.home() / "Downloads"
    return d if d.is_dir() else None


def _safe_name(value: str) -> str:
    """A path-safe stem from a name that may have come from another machine.

    A hand-off id or archive filename is attacker-influenced input (a peer
    chooses the filename it Taildrops). Without this, an id of `"../../evil"`
    makes `pack` write outside the payload's directory, and a filename of
    `.pha-handoff.tgz` yields an empty stem whose staging directory resolves to
    the staging directory itself — `describe` would then `rmtree` it.
    """
    stem = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in value)
    stem = stem.strip(".")
    return stem or "handoff"


def _stem(archive: Path) -> str:
    name = archive.name
    for suffix in (PAYLOAD_SUFFIX, RESULT_SUFFIX):
        if name.endswith(suffix):
            return _safe_name(name[: -len(suffix)])
    return _safe_name(Path(name).stem)


def _unstage_dest(cfg: Config, archive: Path) -> Path:
    return staging_dir(cfg) / _stem(archive)


def _already_staged(cfg: Config, archive: Path) -> bool:
    """Idempotency without a state file: an archive whose unpack directory
    already exists has been received. `--from` re-processing is explicit."""
    return _unstage_dest(cfg, archive).exists()


def receive(cfg: Config, wait: bool = False, loop: bool = False,
            verbose: bool = True) -> list[Path]:
    """Move files out of the Tailscale inbox into the staging directory.

    Returns the archives that were in the inbox. `wait` blocks until one
    arrives; `loop` keeps a drop box open — the always-on machine can leave
    `pha handoff recv --loop` running and a hand-over lands as it is sent.
    """
    exe = require_tailscale()
    staging = staging_dir(cfg)
    try:
        staging.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise TransportError(
            f"cannot create the staging directory {staging}: {e}") from e
    cmd = [exe, "file", "get", "--conflict=rename", str(staging)]
    if loop:
        cmd.insert(3, "--loop")
    elif wait:
        cmd.insert(3, "--wait")
    if verbose:
        print(f"  waiting for a hand-over{' (drop box)' if loop else ''} …"
              if (wait or loop) else "  checking the Tailscale inbox …")
    proc = _run(cmd)
    if proc.returncode != 0:
        raise _fail("receiving", proc)
    if verbose and proc.stdout.strip():
        for line in proc.stdout.strip().splitlines():
            print(f"    {line}")
    return _archives_in(staging)


def candidates(cfg: Config, include_downloads: bool = True) -> list[Path]:
    """Archives worth staging: the inbox/staging dir, plus Downloads.

    Downloads is included because the macOS app can deliver there and bypass the
    CLI inbox, but a Downloads archive is only a candidate while its unpack
    directory is absent — so a manual `pha handoff in` on a file you copied
    yourself never gets replayed here.
    """
    seen: list[Path] = []
    for a in _archives_in(staging_dir(cfg)):
        if not _already_staged(cfg, a):
            seen.append(a)
    if include_downloads:
        d = _downloads_dir()
        if d:
            for a in _archives_in(d):
                if not _already_staged(cfg, a) and a not in seen:
                    seen.append(a)
    return seen


def describe(cfg: Config, archive: Path, verbose: bool = True) -> dict:
    """Unpack one archive into staging and report what it is and what to run.

    Returns the staged dir, the hand-off id, the kind and the command that
    applies it. Applying is NOT done here — a merge stays an explicit command.
    """
    dest = _unstage_dest(cfg, archive)
    if dest.exists():
        shutil.rmtree(dest)
    unpack(archive, dest, verbose=verbose)
    manifest = _manifest(dest)
    if manifest is None:
        raise TransportError(
            f"{archive.name} unpacked to a directory that is neither a hand-out "
            f"payload nor a result")
    kind, payload = manifest
    hid = str(payload.get("handoff_id") or "")
    documents = payload.get("documents") or []

    command = (f"pha handoff in {dest}" if kind == KIND_PAYLOAD
               else f"pha handoff fetch {dest}")
    return {
        "archive": str(archive),
        "staged": str(dest),
        "handoff_id": hid,
        "kind": kind,
        "documents": len(documents),
        "command": command,
        "from_downloads": _downloads_dir() is not None
        and archive.parent == _downloads_dir(),
    }


# --------------------------------------------------------------------------
# send
# --------------------------------------------------------------------------

def send(archive: Path, peer: Peer | str, verbose: bool = True) -> dict:
    """Taildrop one archive to a tailnet device."""
    exe = require_tailscale()
    if not isinstance(peer, Peer):
        peer = resolve_peer(str(peer))
    archive = Path(archive)
    if not archive.is_file():
        raise TransportError(f"no such archive: {archive}")

    if verbose:
        print(f"  sending {archive.name} ({_human(archive.stat().st_size)}) "
              f"to {peer.label} …")
    proc = _run([exe, "file", "cp", str(archive), f"{peer.ip}:"])
    if proc.returncode != 0:
        raise _fail(f"sending to {peer.label}", proc)
    if verbose and proc.stderr.strip():
        for line in proc.stderr.strip().splitlines()[-1:]:
            print(f"    {line.strip()}")
    return {"archive": str(archive), "to": peer.hostname, "to_ip": peer.ip}


def send_dir(directory: Path, peer_query: str, verbose: bool = True) -> dict:
    """Pack a payload/result directory and send it — the `--send` path."""
    peer = resolve_peer(peer_query)  # resolve BEFORE packing a huge archive
    directory = Path(directory)
    archive = pack(directory, verbose=verbose)
    res = send(archive, peer, verbose=verbose)
    res["handoff_id"] = read_id(directory)
    res["kind"] = read_kind(directory)
    return res

"""`pha handoff --send` / `recv` — moving the payload over the tailnet.

The move itself is Taildrop, so these tests fake the `tailscale` CLI and assert
the two properties that matter:

- **what we hand the daemon** — the right subcommand, the peer's IP with the
  trailing colon Taildrop requires, and a single archive (never a directory);
- **what we accept back** — a tar arriving from another machine is untrusted
  input, so a member that escapes the staging directory (absolute path, `..`,
  a symlink) must be refused rather than sanitised.

The payload FORMAT is deliberately not re-tested here (`test_handoff.py` owns
that): the archive is a transport envelope, and `unpack` hands
`import_handoff`/`build_result` exactly the directory they already expect.
"""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import handoff_transport as T
from personal_historical_archive.handoff import HandoffError

TARGETS = (
    "100.68.155.125\tmac-mini-de-joaquim\n"
    "100.80.158.78\tmacbook-air-de-margarida\n"
    "100.71.238.113\tipad-pro\t\toffline; last seen 227h15m0s ago\n"
)


def _fake_tailscale(monkeypatch, *, targets: str = TARGETS, on_get=None,
                    rc: int = 0):
    """Install a fake `tailscale`: record the commands, script the targets."""
    monkeypatch.setattr(T, "find_engine_binary", lambda name: "/usr/bin/tailscale")
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=None):
        calls.append(list(cmd))
        if cmd[1:4] == ["file", "cp", "--targets"]:
            return subprocess.CompletedProcess(cmd, rc, targets, "")
        if cmd[1:3] == ["file", "get"] and on_get is not None:
            on_get(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, rc, "", "")
    monkeypatch.setattr(T, "_run", fake_run)
    return calls


def _payload(directory: Path, handoff_id: str = "DI-20260926", docs=2) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "handoff.json").write_text(json.dumps({
        "format": "pha-handoff", "handoff_id": handoff_id,
        "documents": [{"relpath": f"c/v{i}.pdf"} for i in range(docs)],
    }), encoding="utf-8")
    (directory / "dropbox" / "c").mkdir(parents=True, exist_ok=True)
    (directory / "dropbox" / "c" / "v1.pdf").write_bytes(b"%PDF-1.4 body")
    return directory


def _result(directory: Path, handoff_id: str = "DI-20260926", docs=1) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "handoff-result.json").write_text(json.dumps({
        "format": "pha-handoff", "handoff_id": handoff_id,
        "documents": [{"relpath": f"c/v{i}.pdf"} for i in range(docs)],
    }), encoding="utf-8")
    (directory / "library").mkdir(parents=True, exist_ok=True)
    (directory / "library" / "p1.md").write_text("text", encoding="utf-8")
    return directory


# --------------------------------------------------------------------------
# peers
# --------------------------------------------------------------------------

def test_peers_parses_the_target_list(monkeypatch):
    _fake_tailscale(monkeypatch)
    got = T.peers()
    assert [p.hostname for p in got] == [
        "mac-mini-de-joaquim", "macbook-air-de-margarida", "ipad-pro"]
    assert got[0].ip == "100.68.155.125" and got[0].online
    # an empty status column means online; "offline; …" does not
    assert not got[2].online
    assert got[2].status.startswith("offline")


def test_resolve_peer_by_name_ip_and_substring(monkeypatch):
    _fake_tailscale(monkeypatch)
    assert T.resolve_peer("mac-mini-de-joaquim").ip == "100.68.155.125"
    assert T.resolve_peer("100.80.158.78").hostname == "macbook-air-de-margarida"
    assert T.resolve_peer("mini").hostname == "mac-mini-de-joaquim"


def test_resolve_peer_refuses_ambiguous_offline_and_unknown(monkeypatch):
    _fake_tailscale(monkeypatch)
    with pytest.raises(HandoffError, match="matches 2 devices"):
        T.resolve_peer("mac")          # mac-mini AND macbook-air
    with pytest.raises(HandoffError, match="must be awake"):
        T.resolve_peer("ipad-pro")     # never guess: an offline send just fails later
    with pytest.raises(HandoffError, match="no tailnet device matches"):
        T.resolve_peer("nonexistent")
    assert "mac-mini-de-joaquim" in str(
        pytest.raises(HandoffError, T.resolve_peer, "nonexistent").value)


def test_resolve_peer_reports_a_missing_tailscale_clearly(monkeypatch):
    monkeypatch.setattr(T, "find_engine_binary", lambda name: None)
    with pytest.raises(HandoffError, match="not installed or not on PATH"):
        T.peers()


# --------------------------------------------------------------------------
# the envelope
# --------------------------------------------------------------------------

def test_pack_then_unpack_round_trips_a_payload(tmp_path):
    src = _payload(tmp_path / "DI.pha-handoff")
    archive = T.pack(src, verbose=False)
    assert archive.name == "DI-20260926.pha-handoff.tgz"
    assert archive.is_file() and archive.parent == src.parent

    dest = tmp_path / "staged"
    T.unpack(archive, dest, verbose=False)
    assert (dest / "handoff.json").is_file()
    assert (dest / "dropbox" / "c" / "v1.pdf").read_bytes() == b"%PDF-1.4 body"


def test_pack_names_a_result_differently(tmp_path):
    archive = T.pack(_result(tmp_path / "r"), verbose=False)
    assert archive.name == "DI-20260926.pha-result.tgz"


def test_read_kind_and_id_come_from_the_manifest_not_the_name(tmp_path):
    assert T.read_kind(_payload(tmp_path / "p")) == T.KIND_PAYLOAD
    assert T.read_kind(_result(tmp_path / "r")) == T.KIND_RESULT
    assert T.read_id(_payload(tmp_path / "p2", "abc-1")) == "abc-1"
    with pytest.raises(HandoffError, match="neither a hand-out payload nor a result"):
        T.read_kind(tmp_path / "empty")


def test_read_kind_refuses_a_manifest_pha_did_not_write(tmp_path):
    """A peer picks the filename, so the manifest's `format` is what vouches for
    an archive — a foreign `handoff.json` is not a hand-over."""
    d = tmp_path / "x"
    d.mkdir()
    (d / "handoff.json").write_text(
        json.dumps({"format": "not-pha", "handoff_id": "x"}), encoding="utf-8")
    with pytest.raises(HandoffError, match="not a pha hand-off"):
        T.read_kind(d)


def _tar_with(tmp_path: Path, name: str, *, link: str | None = None) -> Path:
    import tarfile
    archive = tmp_path / "evil.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        if link is not None:
            info.type = tarfile.SYMTYPE
            info.linkname = link
            tar.addfile(info)
        else:
            data = b"x"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return archive


@pytest.mark.parametrize("member,link", [
    ("../escaped.txt", None),          # classic traversal
    ("a/../../escaped.txt", None),     # traversal in the middle
    ("/tmp/escaped.txt", None),        # absolute path
    ("link", "/etc/passwd"),           # a symlink is how you escape without ".."
])
def test_unpack_refuses_a_member_that_escapes_the_staging_dir(tmp_path, member, link):
    archive = _tar_with(tmp_path, member, link=link)
    dest = tmp_path / "staged"
    with pytest.raises(HandoffError, match="refusing"):
        T.unpack(archive, dest, verbose=False)
    assert not (tmp_path / "escaped.txt").exists()


def test_pack_keeps_a_hostile_handoff_id_inside_the_payload_dir(tmp_path):
    """The id comes from a manifest another machine wrote. It must not steer
    the archive path (or, via an empty stem, the staging `rmtree`) anywhere."""
    src = _payload(tmp_path / "p", handoff_id="../../evil")
    archive = T.pack(src, verbose=False)
    assert archive.parent == src.parent
    assert "/" not in archive.name and archive.name.endswith(T.PAYLOAD_SUFFIX)


@pytest.mark.parametrize("name", [".pha-handoff.tgz", "...pha-handoff.tgz",
                                  "../../escape.pha-handoff.tgz"])
def test_stem_is_never_empty_or_a_parent_reference(cfg, name):
    assert T._stem(Path("/inbox") / name) not in ("", ".", "..")
    dest = T._unstage_dest(cfg, Path("/inbox") / name)
    assert T.staging_dir(cfg) in dest.parents


# --------------------------------------------------------------------------
# send
# --------------------------------------------------------------------------

def test_send_targets_the_peer_ip_with_a_colon(monkeypatch, tmp_path):
    calls = _fake_tailscale(monkeypatch)
    archive = T.pack(_payload(tmp_path / "p"), verbose=False)
    res = T.send(archive, T.resolve_peer("mac-mini-de-joaquim"), verbose=False)
    cp = [c for c in calls if c[1:3] == ["file", "cp"] and "--targets" not in c]
    assert cp == [["/usr/bin/tailscale", "file", "cp", str(archive),
                   "100.68.155.125:"]]
    assert res["to"] == "mac-mini-de-joaquim" and res["to_ip"] == "100.68.155.125"


def test_send_dir_resolves_the_peer_before_packing(monkeypatch, tmp_path):
    """A bad peer name must fail before a multi-GB tar is built."""
    _fake_tailscale(monkeypatch)
    src = _payload(tmp_path / "p")
    with pytest.raises(HandoffError, match="no tailnet device matches"):
        T.send_dir(src, "nope", verbose=False)
    assert not list(tmp_path.glob("*.tgz"))


def test_send_reports_a_failed_transfer(monkeypatch, tmp_path):
    _fake_tailscale(monkeypatch, rc=1)
    archive = T.pack(_payload(tmp_path / "p"), verbose=False)
    monkeypatch.setattr(T, "_run", lambda cmd, timeout=None: subprocess.CompletedProcess(
        cmd, 1, "", "peer is offline"))
    with pytest.raises(HandoffError, match="peer is offline"):
        T.send(archive, T.resolve_peer("mac-mini-de-joaquim"), verbose=False)


# --------------------------------------------------------------------------
# receive / staging
# --------------------------------------------------------------------------

def test_receive_stages_an_archive_from_the_inbox(monkeypatch, tmp_path, cfg):
    def on_get(staging: Path):
        arc = T.pack(_payload(tmp_path / "src"), verbose=False)
        (staging / arc.name).write_bytes(arc.read_bytes())

    _fake_tailscale(monkeypatch, on_get=on_get)
    got = T.receive(cfg, verbose=False)
    assert [p.name for p in got] == ["DI-20260926.pha-handoff.tgz"]
    assert T.staging_dir(cfg) == cfg.archive_dir / ".pha" / "handoffs" / "incoming"


def test_describe_unpacks_and_names_the_command_without_applying(monkeypatch, tmp_path, cfg):
    _fake_tailscale(monkeypatch)
    staging = T.staging_dir(cfg)
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / "DI-20260926.pha-handoff.tgz"
    archive.write_bytes(T.pack(_payload(tmp_path / "src"), verbose=False).read_bytes())

    info = T.describe(cfg, archive, verbose=False)
    assert info["kind"] == T.KIND_PAYLOAD
    assert info["handoff_id"] == "DI-20260926" and info["documents"] == 2
    assert info["command"] == f"pha handoff in {staging / 'DI-20260926'}"
    # staging must NOT import or merge — the merge stays an explicit command
    assert not cfg.db_path.exists()


def test_candidates_does_not_replay_an_already_staged_archive(monkeypatch, tmp_path, cfg):
    """Idempotency without a state file: an unpacked archive is not a candidate
    again, so `recv` twice is safe (and a Downloads copy is not replayed)."""
    _fake_tailscale(monkeypatch)
    staging = T.staging_dir(cfg)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "DI-20260926.pha-handoff.tgz").write_bytes(
        T.pack(_payload(tmp_path / "src"), verbose=False).read_bytes())
    assert len(T.candidates(cfg, include_downloads=False)) == 1
    (staging / "DI-20260926").mkdir()          # as `describe` would leave it
    assert T.candidates(cfg, include_downloads=False) == []


def test_candidates_survives_an_unreadable_downloads(monkeypatch, tmp_path, cfg):
    """`~/Downloads` is not always readable (macOS TCC, a sandboxed/agent
    context). A source pha cannot read is 'nothing to stage', not a crash —
    measured: a real `pha handoff recv` died with PermissionError before this."""
    _fake_tailscale(monkeypatch)
    blocked = tmp_path / "Downloads"
    blocked.mkdir()
    monkeypatch.setattr(T, "_downloads_dir", lambda: blocked)

    real_iterdir = Path.iterdir

    def guarded(self):
        if self == blocked:
            raise PermissionError(13, "Operation not permitted")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", guarded)
    assert T.candidates(cfg) == []


def test_receive_loop_flags_reach_the_tailscale_cli(monkeypatch, cfg):
    calls = _fake_tailscale(monkeypatch)
    T.receive(cfg, wait=True, verbose=False)
    assert "--wait" in calls[-1]
    T.receive(cfg, loop=True, verbose=False)
    assert "--loop" in calls[-1]
    assert "--conflict=rename" in calls[-1]


# --------------------------------------------------------------------------
# the CLI surface
# --------------------------------------------------------------------------

def test_cli_peers_lists_devices(monkeypatch, cfg, capsys):
    monkeypatch.setattr(T, "peers", lambda: [
        T.Peer("100.68.155.125", "mac-mini-de-joaquim"),
        T.Peer("100.71.238.113", "ipad-pro", "offline; last seen 227h15m0s ago"),
    ])
    cli.cmd_handoff(cfg, SimpleNamespace(handoff_cmd="peers", json=True))
    data = json.loads(capsys.readouterr().out)
    assert data[0] == {"hostname": "mac-mini-de-joaquim", "ip": "100.68.155.125",
                       "online": True, "status": ""}
    assert data[1]["online"] is False

    cli.cmd_handoff(cfg, SimpleNamespace(handoff_cmd="peers", json=False))
    out = capsys.readouterr().out
    assert "mac-mini-de-joaquim" in out and "online" in out


def test_cli_recv_from_an_unpacked_directory_prints_the_command(tmp_path, cfg, capsys):
    src = _payload(tmp_path / "src")
    cli.cmd_handoff(cfg, SimpleNamespace(handoff_cmd="recv", from_=str(src),
                                         wait=False, loop=False, no_downloads=True))
    out = capsys.readouterr().out
    assert "already unpacked" in out
    assert f"next: pha handoff in {src}" in out
    assert not cfg.db_path.exists()

"""`pha handoff worker` — the unattended worker and its LaunchAgent.

Two properties matter here, and they pull in opposite directions:

- the worker must **wait** while the owner's machine is asleep (that is the
  entire reason a resident worker exists — the MacBook is closed and will come
  back), and
- it must **not** wait forever on a failure that waiting cannot fix (a
  misspelled or ambiguous peer), because that silently parks a finished
  hand-over.

So `PeerUnavailable` is retried and every other `TransportError` is not. The
LaunchAgent is tested as a pure document plus the write path — tests must never
touch the real `~/Library/LaunchAgents`.
"""
from __future__ import annotations

import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import handoff_transport as T
from personal_historical_archive import handoff_worker as W
from personal_historical_archive.handoff import HandoffError

from tests.test_handoff_transport import _payload, _result


@pytest.fixture(autouse=True)
def _isolated_launch_agents(tmp_path, monkeypatch):
    """Never let a test write to (or read) the real ~/Library/LaunchAgents."""
    monkeypatch.setattr(W, "plist_path",
                        lambda: tmp_path / "LaunchAgents" / f"{W.WORKER_LABEL}.plist")


def _staged(cfg, tmp_path, hid="DI-20260926") -> Path:
    """A received hand-over archive sitting in the staging directory."""
    staging = T.staging_dir(cfg)
    staging.mkdir(parents=True, exist_ok=True)
    src = T.pack(_payload(tmp_path / "src", hid), verbose=False)
    archive = staging / src.name
    archive.write_bytes(src.read_bytes())
    return archive


def _fake_run_step(monkeypatch, *, rc_by_stage=None, on_back=None):
    """Record the CLI steps the worker runs; optionally script their exit codes."""
    calls: list[list[str]] = []
    rc_by_stage = rc_by_stage or {}

    def fake(cfg, args, base=None, env=None):
        calls.append(list(args))
        stage = args[1] if len(args) > 1 and args[0] == "handoff" else args[0]
        if stage == "back" and on_back is not None:
            # the real `handoff back` writes <staged>-back beside the payload
            staged = Path(args[2])
            on_back(staged.parent / f"{staged.name}-back")
        return rc_by_stage.get(stage, 0)
    monkeypatch.setattr(W, "run_step", fake)
    return calls


def _no_network(monkeypatch, *, peer_ok=True):
    """Fake peer resolution + send, so nothing touches the tailnet."""
    def resolve(query):
        if not peer_ok:
            raise T.TransportError(f"no tailnet device matches {query!r}")
        return T.Peer("100.68.155.125", "macbook-air-de-margarida")
    monkeypatch.setattr(W.T, "resolve_peer", resolve)
    monkeypatch.setattr(W.T, "send", lambda archive, peer, verbose=True: {
        "to": peer.hostname, "to_ip": peer.ip, "archive": str(archive)})


# --------------------------------------------------------------------------
# the invocation the worker runs
# --------------------------------------------------------------------------

def test_pha_base_is_the_path_proof_interpreter_form(cfg):
    """A LaunchAgent starts with a minimal PATH, so the worker must not depend
    on a `pha` shim being on it."""
    base, env = W.pha_base(cfg)
    assert base[1:] == ["-m", "personal_historical_archive"]
    assert base[0].endswith("python") or "python" in Path(base[0]).name
    assert env["PHA_ARCHIVE_DIR"] == str(cfg.archive_dir)
    assert env["PHA_HOME"] == str(cfg.root)


def test_work_archive_runs_in_then_work_resume_then_back(cfg, tmp_path, monkeypatch):
    calls = _fake_run_step(monkeypatch, on_back=_result)
    _no_network(monkeypatch)
    archive = _staged(cfg, tmp_path)

    report = W.work_archive(cfg, archive, "macbook-air-de-margarida")

    assert calls == [
        ["handoff", "in", report["staged"]],
        ["handoff", "work", report["staged"], "--resume"],
        ["handoff", "back", report["staged"]],
    ]
    assert report["ok"] and report["send"]["sent"]


def test_work_archive_treats_nothing_to_work_as_success(cfg, tmp_path, monkeypatch):
    """`handoff work` exits 2 when the plan is empty (an already-finished
    hand-over). That is not a failure — the result must still be built."""
    calls = _fake_run_step(monkeypatch, rc_by_stage={"work": W._WORK_NOTHING_TO_DO},
                           on_back=_result)
    _no_network(monkeypatch)
    report = W.work_archive(cfg, _staged(cfg, tmp_path), "owner")
    assert [c[1] for c in calls] == ["in", "work", "back"]
    assert report["ok"]


def test_work_archive_reports_a_failed_step_and_stops(cfg, tmp_path, monkeypatch):
    """A failed import must NOT go on to build and return a result — that would
    send the owner an empty hand-over presented as finished work."""
    calls = _fake_run_step(monkeypatch, rc_by_stage={"in": 1})
    _no_network(monkeypatch)
    report = W.work_archive(cfg, _staged(cfg, tmp_path), "owner")
    assert [c[1] for c in calls] == ["in"]
    assert not report["ok"] and report["step"] == "in" and report["rc"] == 1

    calls = _fake_run_step(monkeypatch, rc_by_stage={"work": 1})
    _no_network(monkeypatch)
    report = W.work_archive(cfg, _staged(cfg, tmp_path, "DI-2"), "owner")
    assert [c[1] for c in calls] == ["in", "work"]
    assert not report["ok"] and report["step"] == "work"


def test_work_archive_ignores_a_result_that_arrives_here(cfg, tmp_path, monkeypatch):
    """Only the owner applies a result; a worker that received one must not
    re-work it."""
    calls = _fake_run_step(monkeypatch)
    _no_network(monkeypatch)
    staging = T.staging_dir(cfg)
    staging.mkdir(parents=True, exist_ok=True)
    src = T.pack(_result(tmp_path / "r"), verbose=False)
    archive = staging / src.name
    archive.write_bytes(src.read_bytes())

    report = W.work_archive(cfg, archive, "owner")
    assert calls == [] and not report["ok"] and report["kind"] == T.KIND_RESULT


# --------------------------------------------------------------------------
# waiting for the owner — the reason this exists
# --------------------------------------------------------------------------

def test_send_back_waits_for_the_owner_to_come_back(cfg, tmp_path, monkeypatch):
    """The MacBook is closed; the worker waits, then sends when it returns."""
    attempts = {"n": 0}

    def resolve(query):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise T.PeerUnavailable("macbook-air-de-margarida — must be awake")
        return T.Peer("100.80.158.78", "macbook-air-de-margarida")
    monkeypatch.setattr(W.T, "resolve_peer", resolve)
    monkeypatch.setattr(W.T, "send", lambda a, p, verbose=True: {
        "to": p.hostname, "to_ip": p.ip, "archive": str(a)})
    slept: list[float] = []
    monkeypatch.setattr(W.time, "sleep", lambda s: slept.append(s))

    res = W.send_back(cfg, _result(tmp_path / "res"), "macbook", poll_s=5.0)
    assert res["sent"] and res["attempts"] == 3
    assert slept == [5.0, 5.0]          # waited between attempts, not before the first


def test_send_back_does_not_wait_forever_on_a_bad_peer_name(cfg, tmp_path, monkeypatch):
    """A misspelled peer never fixes itself: fail loudly and keep the result."""
    monkeypatch.setattr(W.T, "resolve_peer", lambda q: (_ for _ in ()).throw(
        T.TransportError(f"no tailnet device matches {q!r}")))
    monkeypatch.setattr(W.time, "sleep",
                        lambda s: pytest.fail("must not sleep on a bad peer name"))

    res = W.send_back(cfg, _result(tmp_path / "res"), "typo")
    assert not res["sent"] and "no tailnet device" in res["reason"]
    assert Path(res["archive"]).is_file()      # the work is not lost


def test_send_back_honours_max_retries(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(W.T, "resolve_peer", lambda q: (_ for _ in ()).throw(
        T.PeerUnavailable("offline")))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    res = W.send_back(cfg, _result(tmp_path / "res"), "owner", max_retries=2)
    assert not res["sent"] and res["attempts"] == 2


def test_send_back_packs_once_and_resends(cfg, tmp_path, monkeypatch):
    """Re-packing per attempt would re-tar the whole result while waiting for a
    laptop to be opened."""
    packs = {"n": 0}
    real_pack = W.T.pack

    def counting_pack(directory, **kw):
        packs["n"] += 1
        return real_pack(directory, **kw)
    monkeypatch.setattr(W.T, "pack", counting_pack)
    calls = {"n": 0}

    def resolve(q):
        calls["n"] += 1
        if calls["n"] < 2:
            raise T.PeerUnavailable("offline")
        return T.Peer("100.80.158.78", "owner")
    monkeypatch.setattr(W.T, "resolve_peer", resolve)
    monkeypatch.setattr(W.T, "send", lambda a, p, verbose=True: {"to": p.hostname})
    monkeypatch.setattr(W.time, "sleep", lambda s: None)

    W.send_back(cfg, _result(tmp_path / "res"), "owner")
    assert packs["n"] == 1


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

def test_worker_loop_once_drains_and_returns(cfg, tmp_path, monkeypatch):
    _fake_run_step(monkeypatch, on_back=_result)
    _no_network(monkeypatch)
    _staged(cfg, tmp_path)
    got: list[bool] = []
    monkeypatch.setattr(W.T, "receive",
                        lambda cfg, wait=False, loop=False, verbose=True: got.append(wait) or [])

    reports = W.worker_loop(cfg, "owner", once=True)
    assert got == [False]                 # --once must not block waiting
    assert len(reports) == 1 and reports[0]["ok"]


def test_worker_loop_survives_one_bad_archive(cfg, tmp_path, monkeypatch):
    """A resident worker must not die because one hand-over could not be staged."""
    _fake_run_step(monkeypatch, on_back=_result)
    _no_network(monkeypatch)
    _staged(cfg, tmp_path)
    monkeypatch.setattr(W.T, "receive",
                        lambda cfg, wait=False, loop=False, verbose=True: [])
    real_describe = W.T.describe
    calls = {"n": 0}

    def flaky(cfg, archive, verbose=True):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HandoffError("refusing evil.tgz: member is a link")
        return real_describe(cfg, archive, verbose=verbose)
    monkeypatch.setattr(W.T, "describe", flaky)

    reports = W.worker_loop(cfg, "owner", once=True)
    assert len(reports) == 1 and not reports[0]["ok"]


# --------------------------------------------------------------------------
# the LaunchAgent
# --------------------------------------------------------------------------

def test_worker_plist_is_runnable_and_needs_no_admin(cfg):
    data = plistlib.loads(W.worker_plist(cfg, "macbook-air-de-margarida"))
    assert data["Label"] == W.WORKER_LABEL
    assert data["ProgramArguments"][1:] == [
        "-m", "personal_historical_archive", "handoff", "worker",
        "--send-to", "macbook-air-de-margarida"]
    assert data["EnvironmentVariables"]["PHA_ARCHIVE_DIR"] == str(cfg.archive_dir)
    assert data["RunAtLoad"] is True and data["KeepAlive"] is True
    assert data["ThrottleInterval"] == 60          # a crash must not spin
    assert data["StandardOutPath"] == str(W.log_path(cfg))
    assert data["StandardErrorPath"] == str(W.err_path(cfg))


def test_install_writes_the_plist_and_starts_it(cfg, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    monkeypatch.setattr(W, "_bootstrap_load", lambda path: True)
    monkeypatch.setattr(W, "_bootstrap_remove", lambda path: False)

    res = W.install_worker(cfg, "owner")
    assert res["installed"] and res["loaded"]
    written = plistlib.loads(Path(res["plist"]).read_bytes())
    assert written["Label"] == W.WORKER_LABEL


def test_install_dry_run_changes_nothing(cfg, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    monkeypatch.setattr(W, "_bootstrap_load",
                        lambda path: pytest.fail("dry run must not start anything"))
    res = W.install_worker(cfg, "owner", dry_run=True)
    assert res["dry_run"] and not Path(res["plist"]).exists()
    assert res["would_run"][-2:] == ["--send-to", "owner"]


def test_install_refuses_without_a_peer_and_off_macos(cfg, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    with pytest.raises(HandoffError, match="needs --send-to"):
        W.install_worker(cfg, "")

    monkeypatch.setattr(W.sys, "platform", "linux")
    with pytest.raises(HandoffError, match="macOS LaunchAgent"):
        W.install_worker(cfg, "owner")


def test_uninstall_removes_the_plist(cfg, monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "darwin")
    monkeypatch.setattr(W, "_bootstrap_load", lambda path: True)
    monkeypatch.setattr(W, "_bootstrap_remove", lambda path: True)
    W.install_worker(cfg, "owner")
    res = W.uninstall_worker()
    assert res["uninstalled"] and res["stopped"]
    assert not Path(res["plist"]).exists()


# --------------------------------------------------------------------------
# the CLI surface
# --------------------------------------------------------------------------

def test_cli_worker_status_reports_not_installed(cfg, capsys):
    cli.cmd_handoff(cfg, SimpleNamespace(handoff_cmd="worker", status=True,
                                         json=False))
    out = capsys.readouterr().out
    assert "not installed" in out


def test_cli_worker_requires_send_to(cfg):
    with pytest.raises(SystemExit) as e:
        cli.cmd_handoff(cfg, SimpleNamespace(handoff_cmd="worker", status=False,
                                             uninstall=False, install=False,
                                             send_to=None))
    assert e.value.code == 2


def test_cli_worker_status_json(cfg, capsys):
    cli.cmd_handoff(cfg, SimpleNamespace(handoff_cmd="worker", status=True,
                                         json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["installed"] is False and data["label"] == W.WORKER_LABEL

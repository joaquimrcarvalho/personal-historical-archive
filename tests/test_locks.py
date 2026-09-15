from __future__ import annotations

"""Model-server locks: one model per model-server, not one per archive.

The rule under test: a job holds one slot on every server key it may talk to;
two jobs may run concurrently iff their key sets are disjoint. See locks.py.
"""

import os
import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import locks
from personal_historical_archive.config import Config


def _cfg(tmp_path, servers_yaml: str = "", embed: str = "http://127.0.0.1:1234/v1") -> Config:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
        f"embeddings:\n  base_url: {embed}\n"
        f"{servers_yaml}"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


# --------------------------------------------------------------------------- key derivation

def test_normalise_endpoint_unifies_spellings():
    assert locks.normalise_endpoint("http://localhost:1234/v1") == "http://127.0.0.1:1234"
    assert locks.normalise_endpoint("http://127.0.0.1:1234") == "http://127.0.0.1:1234"
    assert locks.normalise_endpoint("http://127.0.0.1:1234/") == "http://127.0.0.1:1234"
    assert locks.normalise_endpoint("127.0.0.1:1234/v1") == "http://127.0.0.1:1234"
    # the default port is filled in, so :80 and no-port are one server
    assert locks.normalise_endpoint("http://127.0.0.1/v1") == "http://127.0.0.1:80"
    assert locks.normalise_endpoint("https://box.example/v1") == "https://box.example:443"
    # a genuinely different port/host is a different key
    assert locks.normalise_endpoint("http://127.0.0.1:1235") != locks.normalise_endpoint(
        "http://127.0.0.1:1234")
    # nothing usable -> the wildcard, never a false "different server"
    assert locks.normalise_endpoint("") == ""
    assert locks.endpoint_key("") == locks.WILDCARD


def test_normalise_endpoint_expands_env(monkeypatch):
    monkeypatch.setenv("MY_HOST", "box.example")
    assert locks.normalise_endpoint("http://${MY_HOST}:1234/v1") == "http://box.example:1234"


def test_stage_key_declared_wildcard_and_local_engines():
    declared = SimpleNamespace(server="mac-studio", base_url="http://127.0.0.1:1234/v1",
                               engine="")
    assert locks.stage_key(declared) == "mac-studio"
    # unlabelled: unknown provenance, so assume it shares a server with anything
    unlabelled = SimpleNamespace(server="", base_url="http://127.0.0.1:1234/v1", engine="")
    assert locks.stage_key(unlabelled) == locks.WILDCARD
    # a local OCR/parse engine runs as a subprocess and touches no server
    ocr = SimpleNamespace(server="", base_url="", engine="liteparse")
    assert locks.stage_key(ocr) == ""
    assert locks.stage_key(None) == ""


def test_embed_key_uses_declared_server_then_endpoint(tmp_path):
    cfg = _cfg(tmp_path)
    assert locks.embed_key(cfg) == "http://127.0.0.1:1234"
    cfg2 = _cfg(tmp_path / "two", embed="http://127.0.0.1:1234/v1")
    cfg2.embed_server = "mac-studio"
    assert locks.embed_key(cfg2) == "mac-studio"


def test_job_keys_unions_stages_and_embed(tmp_path):
    cfg = _cfg(tmp_path)
    pal = SimpleNamespace(server="mac-studio", base_url="", engine="")
    ocr = SimpleNamespace(server="", base_url="", engine="tesseract")
    assert locks.job_keys(pal, ocr, cfg=cfg) == ["mac-studio"]
    assert locks.job_keys(pal, cfg=cfg, embed=True) == [
        "http://127.0.0.1:1234", "mac-studio"]
    # a job that touches nothing needs no lock at all
    assert locks.job_keys(ocr, cfg=cfg) == []


# --------------------------------------------------------------------------- acquire / release

def _foreign(cfg, key: str, slot: int = 1, pid: int = 999999, label: str = "pha scan"):
    """Plant a lock file as if another live process held it."""
    path = locks._slot_path(key, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid} {label}", encoding="utf-8")
    return path


def test_acquire_and_release_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    h = locks.acquire(cfg, ["mac-studio"], label="pha scan")
    assert h.ok and len(h.held) == 1
    assert h.held[0].exists()
    # idempotent for the same process: a job that re-acquires does not deadlock
    again = locks.acquire(cfg, ["mac-studio"], label="pha scan")
    assert again.ok and again.held[0] == h.held[0]
    locks.release(h)
    locks.release(again)
    assert not locks._slot_path("mac-studio", 1).exists()


def test_refuses_when_another_live_job_holds_the_key(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    planted = _foreign(cfg, "mac-studio", label="pha scan")
    h = locks.acquire(cfg, ["mac-studio"], label="pha edit")
    assert not h.ok
    assert "mac-studio" in h.reason()
    assert "busy" in h.reason()
    assert "pha scan" in h.reason()          # names the holder's job
    assert planted.read_text().endswith("pha scan")   # never stolen


def test_reclaims_a_dead_holders_lock(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: False)
    _foreign(cfg, "mac-studio")
    h = locks.acquire(cfg, ["mac-studio"], label="pha edit")
    assert h.ok
    assert locks._holder(h.held[0])[0] == os.getpid()
    locks.release(h)


def test_pidless_fresh_lock_is_respected_but_old_is_stale(tmp_path):
    """A pid-less file may be a job still starting: never steal it fresh. Past
    the age threshold it is stale, so it can never wedge future jobs."""
    cfg = _cfg(tmp_path)
    path = _foreign(cfg, "mac-studio", pid=0)
    assert not locks.acquire(cfg, ["mac-studio"]).ok
    old = time.time() - 7 * 3600
    os.utime(path, (old, old))
    h = locks.acquire(cfg, ["mac-studio"])
    assert h.ok
    locks.release(h)


# --------------------------------------------------------------------------- concurrency rule

def test_disjoint_servers_run_concurrently(tmp_path, monkeypatch):
    """The whole point: two jobs on different model servers do not block."""
    cfg_a = _cfg(tmp_path / "a")
    cfg_b = _cfg(tmp_path / "b")
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _foreign(cfg_a, "mac-studio", pid=999999, label="pha scan")
    # b's palaeographer is on another box; it embeds on its own server too
    cfg_b.embed_server = "other-box"
    h = locks.acquire(cfg_b, ["other-box"], label="pha scan")
    assert h.ok
    locks.release(h)


def test_shared_server_serialises_across_archives(tmp_path, monkeypatch):
    """Two archives on one machine that share a server must serialise — the
    archive-local lock could not see this."""
    cfg_a = _cfg(tmp_path / "a")
    cfg_b = _cfg(tmp_path / "b")
    h_a = locks.acquire(cfg_a, ["mac-studio"], label="pha scan")
    assert h_a.ok
    # simulate a different process holding it
    locks.release(h_a)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _foreign(cfg_b, "mac-studio", pid=999999, label="pha scan (archive A)")
    h_b = locks.acquire(cfg_b, ["mac-studio"], label="pha scan")
    assert not h_b.ok
    assert "archive A" in h_b.reason()


def test_unlabelled_model_intersects_everything(tmp_path, monkeypatch):
    """An unlabelled model takes the wildcard, so an un-migrated archive keeps
    today's global behaviour exactly: it conflicts with any other job, and any
    other job conflicts with it."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    # a job on a named server already runs -> a wildcard job must refuse
    _foreign(cfg, "mac-studio", pid=999999, label="pha scan")
    h = locks.acquire(cfg, [locks.WILDCARD], label="pha edit")
    assert not h.ok
    assert "pha scan" in h.reason()
    # and the reverse: a wildcard job running blocks a named-server job
    for p in locks.lock_dir().glob("*.lock"):
        p.unlink()
    _foreign(cfg, locks.WILDCARD, pid=999999, label="pha reindex")
    h2 = locks.acquire(cfg, ["mac-studio"], label="pha scan")
    assert not h2.ok
    assert "pha reindex" in h2.reason()


def test_declared_capacity_admits_two_jobs(tmp_path, monkeypatch):
    """`servers: {mac-studio: {slots: 2}}` lets a prepared machine admit a
    second job once its models are pre-loaded."""
    cfg = _cfg(tmp_path, servers_yaml="servers:\n  mac-studio: {slots: 2}\n")
    assert cfg.server_slots("mac-studio") == 2
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _foreign(cfg, "mac-studio", slot=1, label="pha scan")
    h = locks.acquire(cfg, ["mac-studio"], label="pha edit")
    assert h.ok, "slot 2 is free, so a second job is admitted"
    assert h.held[0] == locks._slot_path("mac-studio", 2)
    # both slots taken -> a third job is refused
    _foreign(cfg, "mac-studio", slot=2, label="pha reindex")
    h3 = locks.acquire(cfg, ["mac-studio"], label="pha test")
    assert not h3.ok
    locks.release(h)


def test_release_only_removes_our_own_slots(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    foreign = _foreign(cfg, "mac-studio", pid=999999, label="pha scan")
    # nothing of ours to release: the foreign lock must survive
    locks.release(locks.LockHandle(held=[foreign]))
    assert foreign.exists()


def test_unwritable_lock_dir_degrades_instead_of_blocking(tmp_path, monkeypatch):
    """If the lock directory cannot be created, jobs proceed unlocked (the old
    best-effort behaviour) rather than refusing every run."""
    cfg = _cfg(tmp_path)
    # a path that exists as a FILE: mkdir() raises OSError -> degraded
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("x")
    monkeypatch.setattr(locks, "lock_dir", lambda: not_a_dir)
    h = locks.acquire(cfg, ["mac-studio"], label="pha scan")
    assert h.ok and h.degraded


# --------------------------------------------------------------------------- observation

def test_job_running_observes_without_taking(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    assert not locks.job_running(cfg, "mac-studio")
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _foreign(cfg, "mac-studio", pid=999999, label="pha scan")
    assert locks.job_running(cfg, "mac-studio")
    assert not locks.job_running(cfg, "other-box")
    assert locks.any_job_running()
    # our own lock is not "another job" for this process
    h = locks.acquire(cfg, ["other-box"], label="pha edit")
    assert not locks.job_running(cfg, "other-box")
    assert locks.job_running(cfg, "other-box", include_self=True)
    locks.release(h)


def test_job_running_ignores_dead_and_stale(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: False)
    _foreign(cfg, "mac-studio", pid=999999)
    assert not locks.job_running(cfg, "mac-studio")
    pidless = _foreign(cfg, "mac-studio", slot=1, pid=0)
    assert locks.job_running(cfg, "mac-studio")
    old = time.time() - 7 * 3600
    os.utime(pidless, (old, old))
    assert not locks.job_running(cfg, "mac-studio")

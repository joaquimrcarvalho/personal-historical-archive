"""`--wait[=SECONDS]` — queue for a busy model server, never unbounded (G3).

Locks are refuse-not-queue by design, which is what made applying a finished
hand-over demand stopping a running scan. The waiting form is deliberately
bounded: on a typical machine the embedding endpoint is the SAME LM Studio that
serves a scan's vision model, so "wait for the embed server" can mean "wait for a
job that runs for hours" — an unbounded `--wait` would hang an agent forever.

`wait_s=0` (the default) keeps today's behaviour exactly.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli, locks
from personal_historical_archive.config import Config


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
        "embeddings:\n  base_url: http://127.0.0.1:1234/v1\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _plant(cfg, key: str, label: str = "pha scan") -> None:
    """A live foreign holder of `key` (as another pha job would leave)."""
    p = locks._slot_path(key, 1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"999999 {label}", encoding="utf-8")


# --------------------------------------------------------------------------- the queue

def test_wait_zero_refuses_at_once(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _plant(cfg, "srv-a")
    started = time.monotonic()
    h = locks.acquire(cfg, ["srv-a"], wait_s=0)
    assert not h.ok and h.timeout is False and h.waited_s == 0.0
    assert time.monotonic() - started < 0.5
    assert "busy" in h.reason()


def test_wait_queues_until_the_slot_frees(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _plant(cfg, "srv-a")
    planted = locks._slot_path("srv-a", 1)

    import threading
    threading.Timer(0.4, lambda: planted.unlink(missing_ok=True)).start()
    h = locks.acquire(cfg, ["srv-a"], label="pha handoff fetch", wait_s=5, verbose=True)

    assert h.ok, h.reason()
    assert planted.exists(), "the waiter must hold the slot it waited for"
    out = capsys.readouterr().out
    assert "waiting up to 5.0s for model server 'srv-a'" in out
    assert "pha scan" in out                 # names the holder
    assert "lock acquired after" in out
    locks.release(h)


def test_wait_gives_up_at_its_ceiling_and_says_so(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _plant(cfg, "srv-a")
    started = time.monotonic()
    h = locks.acquire(cfg, ["srv-a"], wait_s=0.3)
    waited = time.monotonic() - started

    assert not h.ok and h.timeout is True
    assert waited >= 0.25
    assert h.waited_s > 0
    assert "giving up" in h.reason()
    assert "busy" in h.reason()              # still names the server and holder


def test_wait_never_exceeds_the_ceiling_even_while_free_again(tmp_path, monkeypatch):
    """The ceiling is the promise: no wait path returns early or runs long."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: True)
    _plant(cfg, "srv-a")
    started = time.monotonic()
    locks.acquire(cfg, ["srv-a"], wait_s=0.2)
    assert time.monotonic() - started < 3.0


def test_a_degraded_lock_dir_still_does_not_wait(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    # a lock dir under a FILE cannot be created -> degraded, and no waiting loop
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(locks, "lock_dir", lambda: blocked / "locks")
    started = time.monotonic()
    h = locks.acquire(cfg, ["srv-a"], wait_s=5)
    assert h.degraded is True
    assert time.monotonic() - started < 0.5


# --------------------------------------------------------------------------- CLI plumbing

@pytest.mark.parametrize("argv,env,expected", [
    (["--wait"], {}, cli.LOCK_WAIT_DEFAULT_S),
    (["--wait", "12"], {}, 12.0),
    (["--wait", "0"], {}, 0.0),
    ([], {"PHA_LOCK_WAIT": "45"}, 45.0),
    ([], {"PHA_LOCK_WAIT": "not-a-number"}, 0.0),
    (["--no-wait"], {"PHA_LOCK_WAIT": "45"}, 0.0),
    (["--wait", "7"], {"PHA_LOCK_WAIT": "45"}, 7.0),      # the flag wins
    ([], {}, 0.0),                                        # today's behaviour
])
def test_lock_wait_seconds_from_args_and_env(monkeypatch, capsys, argv, env, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    args = SimpleNamespace(wait=None, no_wait=False)
    if "--wait" in argv:
        args.wait = (float(argv[argv.index("--wait") + 1])
                     if len(argv) > argv.index("--wait") + 1 else cli.LOCK_WAIT_DEFAULT_S)
    args.no_wait = "--no-wait" in argv
    assert cli._lock_wait_s(args) == expected
    capsys.readouterr()


def test_scan_edit_and_reindex_forward_the_wait(tmp_path, monkeypatch):
    """The ceiling must reach `locks.acquire`, or `--wait` is decoration."""
    from personal_historical_archive import ingest

    seen: list[float] = []
    real = locks.acquire

    def spy(cfg, keys, label="pha job", wait_s=0.0, verbose=False):
        seen.append(wait_s)
        return locks.LockHandle(busy_key="srv-a", busy_holder="pha scan")

    monkeypatch.setattr(locks, "acquire", spy)
    cfg = _cfg(tmp_path)
    src = cfg.dropbox / "collections" / "DI"
    src.mkdir(parents=True, exist_ok=True)
    (src / "d.pdf").write_bytes(b"%PDF-1.4 d")
    pal = cfg.get_palaeographer()

    ingest.scan_once(cfg, object(), pal, path="collections/DI", verbose=False, wait_s=3)
    ingest.edit_all(cfg, verbose=False, wait_s=4)
    ingest.edit_documents_under(cfg, "collections/DI", verbose=False, wait_s=5)

    monkeypatch.setattr(locks, "acquire", real)
    assert 3 in seen and 4 in seen and 5 in seen, seen

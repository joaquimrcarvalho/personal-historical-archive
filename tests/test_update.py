from __future__ import annotations

import json
from pathlib import Path

import pytest

import personal_historical_archive.update as u
from personal_historical_archive.update import check_and_notify, version_greater


# --------------------------------------------------------------------------- version comparison

def test_parse_basic():
    rel, qual = u._parse_version("0.1.0")
    assert rel == [0, 1, 0]
    assert qual == (10, 0)  # plain release


def test_parse_leading_v_and_suffix():
    assert u._parse_version("v1.2.3") == ([1, 2, 3], (10, 0))
    rel, qual = u._parse_version("1.0rc1")
    assert rel == [1, 0]
    assert qual == (4, 1)  # rc rank


def test_version_greater_simple():
    assert version_greater("0.2.0", "0.1.9")
    assert version_greater("1.0.0", "0.9.9")
    assert not version_greater("0.1.0", "0.1.0")
    assert not version_greater("0.1.0", "0.1.1")


def test_version_greater_qualifiers():
    # a release beats its own pre-release
    assert version_greater("1.0.0", "1.0.0rc1")
    assert not version_greater("1.0.0rc1", "1.0.0")
    # rc beats beta beats dev
    assert version_greater("1.0rc1", "1.0b1")
    assert version_greater("1.0b1", "1.0a1")
    assert version_greater("1.0a1", "1.0.dev1")


def test_version_greater_post():
    assert version_greater("1.0.0.post1", "1.0.0")


# --------------------------------------------------------------------------- check (network mocked)

def test_check(monkeypatch):
    monkeypatch.setattr(u, "current_version", lambda: "0.1.0")
    monkeypatch.setattr(u, "remote_version", lambda repo, branch, timeout: "0.2.0")
    info = u.check(Path("/tmp/x"), repo="owner/repo", branch="main", timeout=1)
    assert info["current"] == "0.1.0"
    assert info["latest"] == "0.2.0"
    assert info["update_available"] is True
    assert info["remote_source"] == "owner/repo@main"


def test_check_no_update(monkeypatch):
    monkeypatch.setattr(u, "current_version", lambda: "0.2.0")
    monkeypatch.setattr(u, "remote_version", lambda repo, branch, timeout: "0.2.0")
    assert u.check(Path("/tmp/x"), timeout=1)["update_available"] is False


# --------------------------------------------------------------------------- state + daily notice

def _state_file(root: Path):
    return root / "data" / "update_state.json"


def test_due_when_no_state(tmp_path):
    assert u._due({}, 24) is True


def test_not_due_recently(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    state = {"last_check_ts": (now - timedelta(hours=1)).isoformat()}
    assert u._due(state, 24) is False


def test_due_after_interval(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    state = {"last_check_ts": (now - timedelta(hours=25)).isoformat()}
    assert u._due(state, 24) is True


def test_check_and_notify_returns_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(u, "current_version", lambda: "0.1.0")
    monkeypatch.setattr(u, "remote_version", lambda repo, branch, timeout: "0.2.0")
    notice = check_and_notify(tmp_path, timeout=1)
    assert notice is not None
    assert "0.2.0" in notice and "0.1.0" in notice
    # state was persisted
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert state["update_available"] is True


def test_check_and_notify_silent_when_current(monkeypatch, tmp_path):
    monkeypatch.setattr(u, "current_version", lambda: "0.2.0")
    monkeypatch.setattr(u, "remote_version", lambda repo, branch, timeout: "0.2.0")
    assert check_and_notify(tmp_path, timeout=1) is None


def test_check_and_notify_once_per_day(monkeypatch, tmp_path):
    monkeypatch.setattr(u, "current_version", lambda: "0.1.0")
    monkeypatch.setattr(u, "remote_version", lambda repo, branch, timeout: "0.2.0")
    first = check_and_notify(tmp_path, timeout=1)
    assert first is not None
    # immediately again -> not due -> None even though an update exists
    assert check_and_notify(tmp_path, timeout=1) is None


def test_check_and_notify_silent_on_network_error(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(u, "remote_version", boom)
    assert check_and_notify(tmp_path, timeout=1) is None
    # timestamp still stamped so we do not hammer GitHub every run
    assert _state_file(tmp_path).exists()


# --------------------------------------------------------------------- install location


def _fake_installed_layout(tmp_path, monkeypatch):
    """Point config.__file__ at a site-packages layout: no checkout above it."""
    import personal_historical_archive.config as config

    pkg = (tmp_path / "venv" / "lib" / "python3.13"
           / "site-packages" / "personal_historical_archive")
    pkg.mkdir(parents=True)
    (pkg / "config.py").write_text("")
    monkeypatch.setattr(config, "__file__", str(pkg / "config.py"))


def test_project_root_is_this_checkout():
    root = u.project_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "personal_historical_archive").is_dir()


def test_project_root_raises_clearly_when_installed(tmp_path, monkeypatch):
    """No bogus <env>/lib/python3.13 path: say there is no checkout."""
    _fake_installed_layout(tmp_path, monkeypatch)
    with pytest.raises(u.UpdateError, match="not running from a source checkout"):
        u.project_root()


def test_install_update_reinstalls_when_not_a_checkout(tmp_path, monkeypatch):
    _fake_installed_layout(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(
        u, "_reinstall",
        lambda repo, branch: seen.append((repo, branch)) or "reinstalled",
    )
    assert u.install_update("owner/repo", "main") == "reinstalled"
    assert seen == [("owner/repo", "main")]


def test_install_update_fast_forwards_a_checkout(tmp_path, monkeypatch):
    import personal_historical_archive.config as config

    root = tmp_path / "checkout"
    pkg = root / "src" / "personal_historical_archive"
    pkg.mkdir(parents=True)
    (pkg / "config.py").write_text("")
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (root / ".git").mkdir()
    monkeypatch.setattr(config, "__file__", str(pkg / "config.py"))
    seen = []
    monkeypatch.setattr(u, "_git_update", lambda r, b: seen.append((str(r), b)) or "pulled")
    monkeypatch.setattr(u, "_reinstall", lambda repo, branch: "reinstalled")
    msg = u.install_update("owner/repo", "main")
    assert seen == [(str(root.resolve()), "main")]
    assert "updated the pha source checkout" in msg
    assert str(root.resolve()) in msg

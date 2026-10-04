"""The per-user settings directory, and the writes Config.load must never do.

Two failures are pinned here:

1. A **built/installed** pha launched with no project around it (an MCP/agent
   wrapper runs `pha mcp` from "/") resolved the default `archive_dir: .` to the
   filesystem root and died in `mkdir('/models')` — a write during a read. It
   must instead come back unconfigured, and `pha mcp` must refuse to serve.
2. The pointer for that case lives in a **per-user** settings file
   (`~/.config/pha/config.yaml`, `%APPDATA%\\pha\\config.yaml`), written by
   `pha set archive-dir --global`, with env/.env/project config still outranking
   it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personal_historical_archive import config as c
from personal_historical_archive.config import Config


def _project(tmp_path: Path, archive: str = ".") -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {archive}\n", encoding="utf-8"
    )
    return root


def _wheel_like(monkeypatch, cwd: Path) -> None:
    """Make pha look like a built install launched with no project at all:
    no source checkout to fall back to, and a cwd with no config.yaml above it
    (find_project_root then returns the cwd)."""
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(c, "source_checkout_root", lambda: None)
    monkeypatch.chdir(cwd)


# --------------------------------------------------------------------- locations

def test_user_config_dir_honours_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PHA_CONFIG_DIR", str(tmp_path / "explicit"))
    assert c.user_config_dir() == tmp_path / "explicit"
    assert c.user_settings_file() == tmp_path / "explicit" / "config.yaml"


def test_user_config_dir_uses_xdg_when_set(monkeypatch, tmp_path):
    monkeypatch.delenv("PHA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(c.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert c.user_config_dir() == tmp_path / "xdg" / "pha"


def test_user_config_dir_uses_appdata_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("PHA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(c.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    assert c.user_config_dir() == tmp_path / "AppData" / "Roaming" / "pha"


# --------------------------------------------------------------------- the write gate

def test_load_never_seeds_into_the_filesystem_root(monkeypatch):
    """Loading with an archive resolved to "/" must not write anything."""
    written: list = []
    monkeypatch.setattr(c, "_seed_default", lambda *a, **k: written.append(a))
    monkeypatch.setattr(c, "_seed_default_model", lambda *a, **k: written.append(a))
    monkeypatch.setattr(c, "_migrate_legacy_defs", lambda *a, **k: written.append(a))

    cfg = Config.load(Path("/"))

    assert cfg.archive_dir == Path("/")
    assert cfg.archive_source_kind == "default"
    assert written == [], "a load must not seed definitions under the filesystem root"


def test_archive_is_seedable_refuses_a_root_and_a_readonly_dir(tmp_path):
    assert c._archive_is_seedable(Path("/"), pointed_at=True) is False
    assert c._archive_is_seedable(tmp_path / "new-archive", pointed_at=True) is True
    # nobody pointed pha here: never seed an arbitrary cwd
    assert c._archive_is_seedable(tmp_path / "new-archive", pointed_at=False) is False

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        ro = tmp_path / "readonly"
        ro.mkdir()
        ro.chmod(0o555)
        try:
            assert c._archive_is_seedable(ro / "archive", pointed_at=True) is False
        finally:
            ro.chmod(0o755)


def test_ensure_dirs_builds_nothing_in_a_directory_that_is_not_a_pha_project(
    monkeypatch, tmp_path
):
    """The reported case: an installed pha run from an arbitrary cwd must not
    create dropbox/library/models/... there."""
    cwd = tmp_path / "empty"
    _wheel_like(monkeypatch, cwd)

    cfg = Config.load()
    assert cfg.owns_archive() is False
    cfg.ensure_dirs()

    assert list(cwd.iterdir()) == [], "an unowned directory must stay untouched"


# --------------------------------------------------------------------- precedence

def test_user_pointer_is_used_when_no_project_and_no_checkout(monkeypatch, tmp_path):
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    archive = tmp_path / "the-archive"
    archive.mkdir()
    (user_dir / "config.yaml").write_text(
        f"paths:\n  archive_dir: {archive}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PHA_CONFIG_DIR", str(user_dir))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    _wheel_like(monkeypatch, tmp_path / "empty")

    cfg = Config.load()

    assert cfg.archive_source_kind == "user"
    assert cfg.archive_dir == archive.resolve()


def test_project_config_beats_the_user_pointer(monkeypatch, tmp_path):
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    (user_dir / "config.yaml").write_text(
        f"paths:\n  archive_dir: {tmp_path / 'user-archive'}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PHA_CONFIG_DIR", str(user_dir))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    project_archive = tmp_path / "project-archive"
    root = _project(tmp_path, str(project_archive))
    monkeypatch.chdir(root)

    cfg = Config.load()

    assert cfg.archive_source_kind == "config"
    assert cfg.archive_dir == project_archive.resolve()


def test_env_pointer_beats_the_user_pointer(monkeypatch, tmp_path):
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    (user_dir / "config.yaml").write_text(
        f"paths:\n  archive_dir: {tmp_path / 'user-archive'}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PHA_CONFIG_DIR", str(user_dir))
    env_archive = tmp_path / "env-archive"
    monkeypatch.setenv("PHA_ARCHIVE_DIR", str(env_archive))
    _wheel_like(monkeypatch, tmp_path / "empty")

    cfg = Config.load()

    assert cfg.archive_source_kind == "env"
    assert cfg.archive_dir == env_archive.resolve()


def test_an_explicit_root_ignores_the_user_pointer(monkeypatch, tmp_path):
    """`Config.load(root)` names a tree; a machine-wide setting must not
    redirect it (the same isolation rule as find_project_root's explicit
    start)."""
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    (user_dir / "config.yaml").write_text(
        f"paths:\n  archive_dir: {tmp_path / 'user-archive'}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PHA_CONFIG_DIR", str(user_dir))
    root = _project(tmp_path, ".")

    cfg = Config.load(root)

    assert cfg.archive_source_kind == "default"
    assert cfg.archive_dir == root.resolve()


# --------------------------------------------------------------------- set --global

def test_set_archive_dir_global_writes_the_file_and_is_picked_up(
    monkeypatch, tmp_path, capsys
):
    from personal_historical_archive import cli

    user_dir = tmp_path / "user-config"
    monkeypatch.setenv("PHA_CONFIG_DIR", str(user_dir))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg = Config.load(_project(tmp_path))
    archive = tmp_path / "my-archive"
    archive.mkdir()

    cli._set_archive_dir_in_user_config(cfg, str(archive))

    f = user_dir / "config.yaml"
    assert f.is_file()
    assert f"archive_dir: {archive}" in f.read_text(encoding="utf-8")
    assert "stored paths.archive_dir" in capsys.readouterr().out

    _wheel_like(monkeypatch, tmp_path / "empty")
    assert Config.load().archive_dir == archive.resolve()
    assert Config.load().archive_source_kind == "user"


# --------------------------------------------------------------------- mcp refusal

def test_mcp_refuses_to_serve_an_unconfigured_archive(monkeypatch, tmp_path, capsys):
    from personal_historical_archive import mcp_server

    root = _project(tmp_path, ".")
    cfg = Config.load(root)
    assert cfg.archive_unconfigured() is True

    monkeypatch.setattr(mcp_server.Config, "load", classmethod(lambda cls, *a, **k: cfg))
    with pytest.raises(SystemExit) as ei:
        mcp_server.main("stdio")

    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "no pha archive is configured" in err
    # and it stopped BEFORE ensure_dirs(): nothing was created under the root
    assert not (root / "library").exists()
    assert not (root / "renders").exists()

"""Per-user archive registry: active pointer plus alternatives."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personal_historical_archive import archive_registry as registry
from personal_historical_archive import cli
from personal_historical_archive.config import Config, user_settings_file


def _args(path=None, **kwargs):
    values = {
        "path": path,
        "global_": False,
        "user_": False,
        "project_": False,
        "list_": False,
        "remove": None,
        "use": None,
        "force": False,
        "json": False,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PHA_CONFIG_DIR", str(tmp_path / "ucfg"))
    root = tmp_path / "proj"
    root.mkdir()
    return tmp_path, root, Config.load(root)


def test_registry_register_activate_remove(project):
    tmp_path, _, _ = project
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()

    registry.activate(first)
    assert registry.active() == str(first)
    assert registry.archives() == [str(first)]

    assert registry.register(second) is True
    assert registry.register(second) is False
    assert registry.archives() == [str(first), str(second)]

    ok, message = registry.remove(first)
    assert ok is False and "active" in message
    ok, message = registry.remove(first, force=True)
    assert ok is True
    assert registry.active() == str(second)
    assert registry.archives() == [str(second)]

    ok, message = registry.remove(second)
    assert ok is False and "active" in message
    ok, message = registry.remove(second, force=True)
    assert ok is True
    assert registry.active() == ""
    assert registry.archives() == []
    settings = user_settings_file()
    assert settings.is_file()
    assert "archive_dir" not in settings.read_text(encoding="utf-8")


def test_set_archive_dir_registers_alternative_instead_of_switching(project, capsys):
    tmp_path, root, cfg = project
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    cli.cmd_set_archive_dir(cfg, _args(first))
    assert registry.active() == str(first)
    assert not (root / "config.yaml").exists()

    cli.cmd_set_archive_dir(cfg, _args(second))
    out = capsys.readouterr().out
    assert registry.active() == str(first)
    assert registry.archives() == [str(first), str(second)]
    assert "--use" in out


def test_set_archive_dir_use_switches_active(project):
    tmp_path, _, cfg = project
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    cli.cmd_set_archive_dir(cfg, _args(first))
    cli.cmd_set_archive_dir(cfg, _args(second))

    rc = cli._archive_dir_use(cfg, str(second))

    assert rc == 0
    assert registry.active() == str(second)


def test_set_archive_dir_remove_refuses_active_and_removes_alternative(project, capsys):
    tmp_path, _, cfg = project
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    cli.cmd_set_archive_dir(cfg, _args(first))
    cli.cmd_set_archive_dir(cfg, _args(second))

    assert cli._archive_dir_remove(cfg, str(first)) == 1
    assert "active" in capsys.readouterr().err

    assert cli._archive_dir_remove(cfg, str(second)) == 0
    assert registry.archives() == [str(first)]


def test_set_archive_dir_keeps_project_scope_when_project_config_exists(project):
    tmp_path, root, cfg = project
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n", encoding="utf-8")
    cfg = Config.load(root)
    archive = tmp_path / "archive"
    archive.mkdir()

    cli.cmd_set_archive_dir(cfg, _args(archive))

    assert str(archive) in (root / "config.yaml").read_text(encoding="utf-8")
    assert not user_settings_file().exists()


def test_list_archive_dir_json(project, capsys):
    tmp_path, _, cfg = project
    first = tmp_path / "first"
    first.mkdir()
    cli.cmd_set_archive_dir(cfg, _args(first))
    capsys.readouterr()  # drop the "stored ..." line from setup

    cli.cmd_list_archive_dir(cfg, SimpleNamespace(json=True))

    report = json.loads(capsys.readouterr().out)
    assert report["per_user_default"] == str(first)
    assert report["archives"][0]["active"] is True
    assert report["archives"][0]["exists"] is True

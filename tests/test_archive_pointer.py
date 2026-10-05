"""The archive pointer — D3 and D5 of
`enhancements/pha-archive-pointer-loss-bug-report.md`.

**D3** — `pha info` is the discovery command: it must ANSWER "is an archive
configured?" (structured, no prompt) instead of being intercepted by the
fresh-install guard, which on a non-interactive run exited 1 with prose on
stderr. That is what killed the DSH PHA View on every page click.

**D5** — an EMPTY value is evidence of an accident, not a configuration: it is
treated as unset (the only safe reading) but WARNED about, because silently
falling back is how an emptied `PHA_ARCHIVE_DIR` made every run resolve the
source checkout as the archive.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive.config import Config


def _proj(tmp_path, *, name="proj", archive_dir=".", dotenv=None, env=None,
          monkeypatch=None) -> Config:
    """A project dir with `config.yaml` (and optionally `.env`), loaded with the
    environment forced to a known state — the ambient PHA_ARCHIVE_DIR of the
    developer's own shell must not decide what these tests assert."""
    if monkeypatch is not None:
        monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
        if env is not None:
            monkeypatch.setenv("PHA_ARCHIVE_DIR", env)
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {archive_dir}\n")
    if dotenv is not None:
        (root / ".env").write_text(dotenv)
    return Config.load(root)


def _info(cfg, capsys):
    cli.cmd_info(cfg, SimpleNamespace(json=True))
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- D5

def test_empty_env_pointer_is_ignored_and_warned(tmp_path, monkeypatch, capsys):
    cfg = _proj(tmp_path, env="", monkeypatch=monkeypatch)
    assert cfg.archive_source_kind == "default"
    assert cfg.archive_dir == cfg.root  # the project-root default
    err = capsys.readouterr().err
    assert "PHA_ARCHIVE_DIR" in err and "EMPTY" in err


def test_whitespace_only_pointer_is_empty_too(tmp_path, monkeypatch, capsys):
    cfg = _proj(tmp_path, env="   ", monkeypatch=monkeypatch)
    assert cfg.archive_source_kind == "default"
    assert "EMPTY" in capsys.readouterr().err


def test_empty_dotenv_line_is_ignored_and_warned(tmp_path, monkeypatch, capsys):
    cfg = _proj(tmp_path, dotenv="PHA_ARCHIVE_DIR=\n", monkeypatch=monkeypatch)
    assert cfg.archive_source_kind == "default"
    err = capsys.readouterr().err
    assert "EMPTY" in err and ".env" in err
    # ...and it is NOT a pointer, so nothing downstream may treat it as one
    assert cli._archive_explicitly_set(cfg) is False
    assert cli._dotenv_archive_dir(cfg) is None


def test_empty_dotenv_line_does_not_shadow_a_config_pointer(tmp_path, monkeypatch,
                                                            capsys):
    """The precedence is env > .env > config.yaml > '.', and an EMPTY .env line
    is not a value — so a real `paths.archive_dir` still wins."""
    real = tmp_path / "real-archive"
    cfg = _proj(tmp_path, archive_dir=real, dotenv="PHA_ARCHIVE_DIR=\n",
                monkeypatch=monkeypatch)
    assert cfg.archive_source_kind == "config"
    assert cfg.archive_dir == real
    assert cli._archive_explicitly_set(cfg) is True


def test_empty_env_value_falls_through_to_a_good_dotenv(tmp_path, monkeypatch):
    real = tmp_path / "from-dotenv"
    cfg = _proj(tmp_path, dotenv=f"PHA_ARCHIVE_DIR={real}\n", env="",
                monkeypatch=monkeypatch)
    assert cfg.archive_source_kind == "dotenv"
    assert cfg.archive_dir == real


# --------------------------------------------------------------------------- D3

def test_info_answers_unconfigured_instead_of_refusing(tmp_path, monkeypatch, capsys):
    cfg = _proj(tmp_path, monkeypatch=monkeypatch)
    capsys.readouterr()
    data = _info(cfg, capsys)
    assert data["configured"] is False
    assert data["archive_source_kind"] == "default"
    assert data["archive_source"] == "default (project root)"
    assert data["archive_dir"] == str(cfg.root)


@pytest.mark.parametrize("how", ["env", "dotenv", "config"])
def test_info_reports_the_recorded_source(tmp_path, monkeypatch, capsys, how):
    real = tmp_path / f"from-{how}"
    if how == "env":
        cfg = _proj(tmp_path, env=str(real), monkeypatch=monkeypatch)
    elif how == "dotenv":
        cfg = _proj(tmp_path, dotenv=f"PHA_ARCHIVE_DIR={real}\n", monkeypatch=monkeypatch)
    else:
        cfg = _proj(tmp_path, archive_dir=real, monkeypatch=monkeypatch)
    capsys.readouterr()
    data = _info(cfg, capsys)
    assert data["archive_source_kind"] == how
    assert data["configured"] is True
    assert data["archive_dir"] == str(real)


def test_main_info_json_survives_an_unconfigured_archive(tmp_path, monkeypatch,
                                                         capsys):
    """The regression that killed the view: exit 1, no JSON. `pha info --json`
    must now succeed and say so in the payload."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    monkeypatch.setenv("PHA_HOME", str(root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)

    cli.main(["info", "--json"])  # no SystemExit: this is a REPORT, not a failure

    data = json.loads(capsys.readouterr().out)
    assert data["configured"] is False
    assert data["archive_source_kind"] == "default"


def test_main_info_creates_no_archive_layout(tmp_path, monkeypatch, capsys):
    """`info` is dispatched before `ensure_dirs()`, so a discovery call cannot
    invent an archive in whatever directory resolution fell back to (the view
    runs it with cwd='/' on every load)."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    monkeypatch.setenv("PHA_HOME", str(root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)

    cli.main(["info", "--json"])
    capsys.readouterr()

    for created in ("library", "dropbox", "renders", "notes", "data", "inbox",
                    ".pha", "pha-location.md", "archive.db"):
        assert not (root / created).exists(), f"pha info created {created}"


def test_commands_that_need_an_archive_still_refuse(tmp_path, monkeypatch, capsys):
    """Only `info` was exempted: the fresh-install guard still stops a command
    that would otherwise work on an empty fallback archive."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    monkeypatch.setenv("PHA_HOME", str(root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    # non-interactive (no tty): the guard instructs and stops instead of asking
    monkeypatch.setattr("sys.stdin", io.StringIO())

    with pytest.raises(SystemExit) as exc:
        cli.main(["status"])
    assert exc.value.code == 1
    assert "No pha archive is configured or found." in capsys.readouterr().err


def _archive_dir_args(path, *, global_=False, user_=False, project_=False):
    return SimpleNamespace(path=str(path), global_=global_, user_=user_, project_=project_)


def test_set_archive_dir_defaults_to_user_settings_without_project_config(tmp_path, monkeypatch):
    from personal_historical_archive.config import user_settings_file

    monkeypatch.setenv("PHA_CONFIG_DIR", str(tmp_path / "ucfg"))
    root = tmp_path / "proj"
    root.mkdir()
    cfg = Config.load(root)
    archive = tmp_path / "archive"
    archive.mkdir()

    cli.cmd_set_archive_dir(cfg, _archive_dir_args(archive))

    settings = user_settings_file()
    assert settings.is_file()
    assert f"archive_dir: {archive}" in settings.read_text(encoding="utf-8")
    assert not (root / "config.yaml").exists()


def test_set_archive_dir_uses_existing_project_config(tmp_path, monkeypatch):
    from personal_historical_archive.config import user_settings_file

    monkeypatch.setenv("PHA_CONFIG_DIR", str(tmp_path / "ucfg"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n", encoding="utf-8")
    cfg = Config.load(root)
    archive = tmp_path / "archive"
    archive.mkdir()

    cli.cmd_set_archive_dir(cfg, _archive_dir_args(archive))

    assert f"archive_dir: {archive}" in (root / "config.yaml").read_text(encoding="utf-8")
    assert not user_settings_file().exists()


def test_set_archive_dir_user_flag_wins_over_project_config(tmp_path, monkeypatch):
    from personal_historical_archive.config import user_settings_file

    monkeypatch.setenv("PHA_CONFIG_DIR", str(tmp_path / "ucfg"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n", encoding="utf-8")
    cfg = Config.load(root)
    archive = tmp_path / "archive"
    archive.mkdir()

    cli.cmd_set_archive_dir(cfg, _archive_dir_args(archive, user_=True))

    settings = user_settings_file()
    assert settings.is_file()
    assert f"archive_dir: {archive}" in settings.read_text(encoding="utf-8")

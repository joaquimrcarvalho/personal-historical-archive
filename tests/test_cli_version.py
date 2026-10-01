"""`pha version` — which pha is this, and where did it come from?

The command has to answer before anything else runs: a version number is
ambiguous on a machine with more than one install (a `uv tool` shim and an
editable checkout both put a `pha` on PATH), and an agent in a bare shell needs
it with no archive configured at all.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personal_historical_archive import __version__, cli
from personal_historical_archive.config import Config


def _make_cfg(tmp_path, name="proj", archive_dir=".") -> Config:
    """`archive_dir: .` is the shipped default — i.e. NOT an explicitly
    configured archive, which is what the fresh-install guard keys on."""
    root = tmp_path / name
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {archive_dir}\n")
    return Config.load(root)


def _args(**kw):
    base = {"short": False, "json": False}
    base.update(kw)
    return SimpleNamespace(**base)


def test_version_prints_version_and_install(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cli.cmd_version(cfg, _args())
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert first == f"pha {__version__}"
    # where it came from, so two installs on one machine are distinguishable
    assert "command:" in out and "python:" in out and "source:" in out


def test_version_short_prints_only_the_number(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cli.cmd_version(cfg, _args(short=True))
    assert capsys.readouterr().out == f"{__version__}\n"


def test_version_json_is_machine_readable(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cli.cmd_version(cfg, _args(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == __version__
    assert data["command"] and data["python"]
    assert data["archive_configured"] is False  # tmp archive has no documents


def test_version_json_reports_a_configured_archive(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PHA_ARCHIVE_DIR", str(tmp_path / "real"))
    cfg = _make_cfg(tmp_path)
    cli.cmd_version(cfg, _args(json=True))
    assert json.loads(capsys.readouterr().out)["archive_configured"] is True


def test_version_itself_creates_no_archive_output(tmp_path, capsys):
    """It opens no DB and writes no location file — `pha info` (which does touch
    the location trace) is the command for the archive's paths."""
    cfg = _make_cfg(tmp_path)
    cli.cmd_version(cfg, _args())
    capsys.readouterr()
    assert not cfg.db_path.exists()
    assert not (cfg.archive_dir / "pha-location.md").exists()
    assert not (cfg.archive_dir / ".pha").exists()


def test_main_version_runs_with_no_archive_configured(tmp_path, monkeypatch, capsys):
    """The fresh-install guard prompts for an archive; `version` must not reach
    it (nor the daily update notice — two calls must both answer)."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    monkeypatch.setenv("PHA_HOME", str(root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)

    cli.main(["version"])
    first = capsys.readouterr().out
    assert first.splitlines()[0] == f"pha {__version__}"

    cli.main(["version", "--short"])
    assert capsys.readouterr().out == f"{__version__}\n"


def test_version_flag_on_the_top_level_parser(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"pha {__version__}"

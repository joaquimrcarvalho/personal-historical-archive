from __future__ import annotations

import json
from types import SimpleNamespace

from personal_historical_archive import cli
from personal_historical_archive.config import Config


def _make_cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  inbox: inbox\n  library: library\n  renders: renders\n"
        "  notes: notes\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    return Config.load(root)


def test_info_json_exposes_the_archive_paths(tmp_path, capsys):
    """The PHA view locates the archive with `pha info` — a config-only command
    (no DB open, no engine probing)."""
    cfg = _make_cfg(tmp_path)
    cli.cmd_info(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)

    assert data["archive_dir"] == str(cfg.archive_dir)
    assert data["db_path"] == str(cfg.db_path)
    assert data["notes"] == str(cfg.notes)
    assert data["models"] == str(cfg.models_dir)
    assert data["palaeographers"] == str(cfg.palaeographers_dir)
    # discovery must not have created or touched the database
    assert not cfg.db_path.exists()


def test_info_human_output(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cli.cmd_info(cfg, SimpleNamespace(json=False))
    out = capsys.readouterr().out
    assert out.startswith("archive_dir: ")
    assert "library: " in out


def test_info_names_the_legacy_dotenv_as_the_winner(monkeypatch, tmp_path, capsys):
    """When a legacy .env line and a non-default paths.archive_dir both exist,
    `pha info` must name the LEGACY line as the source — the precedence is
    environment > legacy .env > config.yaml > "." (see AGENTS.md / config.yaml
    header / README). A wrong `archive_source` here is exactly the silent
    "which archive am I on?" confusion the line exists to prevent."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {tmp_path / 'from-yaml'}\n")
    (root / ".env").write_text(f"PHA_ARCHIVE_DIR={tmp_path / 'from-dotenv'}\n")
    cfg = Config.load(root)

    cli.cmd_info(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["archive_dir"] == str((tmp_path / "from-dotenv").resolve())
    assert data["archive_source"] == "PHA_ARCHIVE_DIR in .env (legacy)"

    # the one-line resolver `pha status`-style notices use says the same
    line = cli._resolve_archive_dir_line(cfg)
    assert "from the legacy PHA_ARCHIVE_DIR line in .env" in line

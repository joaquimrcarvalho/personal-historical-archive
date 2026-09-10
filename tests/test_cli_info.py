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

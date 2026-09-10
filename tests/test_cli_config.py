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


def test_config_generates_pha_yaml_from_legacy_selection(tmp_path, capsys):
    """A collection configured by the legacy `palaeographer` / `editor` files and
    with no pha.yaml gets one generated from the resolved configuration."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    d = cfg.dropbox / "collections" / "COLX"
    d.mkdir(parents=True)
    (d / "palaeographer").write_text("default\n")
    (d / "editor").write_text("default\n")

    cli.cmd_config(cfg, SimpleNamespace(doc=None, path="collections/COLX", write=True, json=True))
    data = json.loads(capsys.readouterr().out)

    assert data["generated"] is True
    assert data["path"].endswith("collections/COLX/pha.yaml")
    assert sorted(data["legacy_files"]) == ["editor", "palaeographer"]
    written = (d / "pha.yaml").read_text(encoding="utf-8")
    assert "palaeographer:" in written and "rules: default" in written
    assert "editor:" in written
    # the resolved config is reported too (what the view shows alongside the file)
    assert data["resolved"]["palaeographer"]["id"] == "default"
    assert data["resolved"]["editor"]["id"] == "default"


def test_config_does_not_overwrite_an_existing_pha_yaml(tmp_path, capsys):
    """An existing pha.yaml is shown as-is — never regenerated (so hand edits and
    writes stay idempotent)."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    d = cfg.dropbox / "collections" / "COLX"
    d.mkdir(parents=True)
    d.joinpath("pha.yaml").write_text(
        "# hand edited — must not be overwritten\n"
        "palaeographer:\n  rules: default\n  model: some-model\n", encoding="utf-8")

    cli.cmd_config(cfg, SimpleNamespace(doc=None, path="collections/COLX", write=True, json=True))
    data = json.loads(capsys.readouterr().out)

    assert data["generated"] is False
    assert "hand edited" in data["content"]
    assert "hand edited" in (d / "pha.yaml").read_text(encoding="utf-8")
    assert data["resolved"]["palaeographer"]["id"] == "default"


def test_config_without_write_does_not_create_a_file(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    d = cfg.dropbox / "collections" / "COLX"
    d.mkdir(parents=True)

    cli.cmd_config(cfg, SimpleNamespace(doc=None, path="collections/COLX", write=False, json=True))
    data = json.loads(capsys.readouterr().out)

    assert data["generated"] is False
    assert data["content"] is None
    assert not (d / "pha.yaml").exists()


def test_config_human_output(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    (cfg.dropbox / "collections" / "COLX").mkdir(parents=True)
    cli.cmd_config(cfg, SimpleNamespace(doc=None, path="collections/COLX", write=True, json=False))
    out = capsys.readouterr().out
    assert "generated:" in out and "pha.yaml" in out


def test_config_never_creates_a_sidecar_when_one_is_inherited(tmp_path, capsys):
    """A collection that inherits its pha.yaml from an upper folder gets NO local
    sidecar — the inherited file is shown, flagged as inherited."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    upper = cfg.dropbox / "collections"
    upper.mkdir(parents=True, exist_ok=True)
    upper.joinpath("pha.yaml").write_text(
        "palaeographer:\n  rules: default\n  model: default\n", encoding="utf-8")
    d = upper / "COLX"
    d.mkdir(parents=True)

    cli.cmd_config(cfg, SimpleNamespace(doc=None, path="collections/COLX", write=True, json=True))
    data = json.loads(capsys.readouterr().out)

    assert not (d / "pha.yaml").exists()          # nothing generated here
    assert data["generated"] is False
    assert data["inherited"] is True
    assert data["path"].endswith("collections/pha.yaml")   # the in-scope file
    assert "rules: default" in data["content"]


def test_config_own_sidecar_is_not_flagged_inherited(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    d = cfg.dropbox / "collections" / "COLX"
    d.mkdir(parents=True)
    d.joinpath("pha.yaml").write_text("palaeographer:\n  rules: default\n  model: default\n",
                                      encoding="utf-8")
    cli.cmd_config(cfg, SimpleNamespace(doc=None, path="collections/COLX", write=False, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["inherited"] is False


def test_config_resolves_collection_encoders(tmp_path, capsys):
    """The resolved config reports the encoders that actually run for the
    document — here discovered from the collection's encoders/ folder."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "COLX"
    (col / "encoders").mkdir(parents=True)
    (col / "encoders" / "table.md").write_text(
        "---\nid: table\ndescription: tabular records\n---\n\nrules here\n", encoding="utf-8")
    (col / "encoders" / "_sample.md").write_text("ignored\n", encoding="utf-8")

    cli.cmd_config(cfg, SimpleNamespace(doc=None, path="collections/COLX", write=False, json=True))
    data = json.loads(capsys.readouterr().out)

    encs = data["resolved"]["encoders"]
    assert [e["id"] for e in encs] == ["table"]          # _sample.md ignored
    assert encs[0]["path"].endswith("encoders/table.md")
    assert encs[0]["source"] == "directory"

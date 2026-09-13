from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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


def _park(cfg, rel: str, body: bytes = b"%PDF x"):
    p = cfg.inbox / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return p


def _args(**kw):
    base = dict(path=None, move=False, dry_run=False, json=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_inbox_json_groups_units_by_directory(tmp_path, capsys):
    """The listing the view renders: one group per directory holding parked units,
    with the documents in it and the target a move would use."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    _park(cfg, "collections/CAT/a.pdf")
    _park(cfg, "collections/CAT/b.pdf")
    (cfg.inbox / "collections" / "CAT" / "prompt.md").write_text("sidecar")
    _park(cfg, "loose.pdf")

    cli.cmd_inbox(cfg, _args(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["exists"] is True
    assert data["documents"] == 3 and data["files"] == 4
    groups = {g["rel_path"]: g for g in data["collections"]}
    assert set(groups) == {"collections/CAT", ""}
    cat = groups["collections/CAT"]
    assert cat["label"] == "CAT" and cat["documents"] == 2 and cat["files"] == 3
    assert cat["move_target"] == "dropbox/collections/CAT"
    assert sorted(u["name"] for u in cat["units"]) == ["a.pdf", "b.pdf"]  # sidecars are not units
    assert groups[""]["label"] == "(inbox root)" and groups[""]["move_target"] == "dropbox/"


def test_inbox_move_one_document_only(tmp_path, capsys):
    """A scoped move relocates just that document, leaving its neighbours parked."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    _park(cfg, "collections/CAT/a.pdf")
    _park(cfg, "collections/CAT/b.pdf")
    _park(cfg, "loose.pdf")

    cli.cmd_inbox(cfg, _args(path="collections/CAT/a.pdf", move=True))
    out = capsys.readouterr().out
    assert "moved 1 file(s)" in out and "dropbox/collections/CAT/a.pdf" in out
    assert (cfg.dropbox / "collections" / "CAT" / "a.pdf").exists()
    assert (cfg.inbox / "collections" / "CAT" / "b.pdf").exists()
    assert (cfg.inbox / "loose.pdf").exists()


def test_inbox_move_one_collection_merges_and_keeps_layout(tmp_path, capsys):
    """Moving a collection directory preserves the relative layout, carries its
    sidecars, and merges into an existing dropbox collection."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    _park(cfg, "collections/CAT/a.pdf")
    (cfg.inbox / "collections" / "CAT" / "prompt.md").write_text("sidecar")
    _park(cfg, "collections/OTHER/z.pdf")
    (cfg.dropbox / "collections" / "CAT").mkdir(parents=True, exist_ok=True)
    (cfg.dropbox / "collections" / "CAT" / "already.pdf").write_bytes(b"%PDF keep")

    cli.cmd_inbox(cfg, _args(path="collections/CAT", move=True))
    assert (cfg.dropbox / "collections" / "CAT" / "a.pdf").exists()
    assert (cfg.dropbox / "collections" / "CAT" / "prompt.md").exists()
    assert (cfg.dropbox / "collections" / "CAT" / "already.pdf").exists()  # merge, not replace
    assert not (cfg.inbox / "collections" / "CAT").exists()
    assert (cfg.inbox / "collections" / "OTHER" / "z.pdf").exists()  # untouched


def test_inbox_scoped_dry_run_plans_without_moving(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    _park(cfg, "collections/CAT/a.pdf")
    (cfg.inbox / "collections" / "CAT" / "prompt.md").write_text("sidecar")

    cli.cmd_inbox(cfg, _args(path="collections/CAT", dry_run=True, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["files"] == 2
    assert data["would_move"] == [{
        "from": "inbox/collections/CAT",
        "to": "dropbox/collections/CAT",
        "files": 2,
    }]
    assert (cfg.inbox / "collections" / "CAT" / "a.pdf").exists()


@pytest.mark.parametrize("bad", ["../outside.pdf", "collections/../../outside.pdf", "/etc/hosts", ".hidden.pdf"])
def test_inbox_refuses_paths_outside_or_dotted(tmp_path, capsys, bad):
    """A caller-supplied path can never reach outside the inbox (the view must not
    become a general filesystem mover), and dot-paths are left alone."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    (tmp_path / "outside.pdf").write_bytes(b"%PDF out")
    _park(cfg, "a.pdf")

    with pytest.raises(SystemExit) as exc:
        cli.cmd_inbox(cfg, _args(path=bad, move=True))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "outside the inbox" in err or "dot-path" in err
    assert (cfg.inbox / "a.pdf").exists()
    assert (tmp_path / "outside.pdf").exists()


def test_inbox_reports_a_missing_path(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    _park(cfg, "a.pdf")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_inbox(cfg, _args(path="collections/NOPE", move=True))
    assert exc.value.code == 1
    assert "nothing in the inbox" in capsys.readouterr().err
    assert (cfg.inbox / "a.pdf").exists()


def test_inbox_json_empty(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    cli.cmd_inbox(cfg, _args(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["documents"] == 0 and data["collections"] == []

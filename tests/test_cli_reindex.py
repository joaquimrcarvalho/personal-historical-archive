from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive.config import Config


def _make_cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {root / 'archive'}\n")
    return Config.load(root)


def test_reindex_page_requires_doc(tmp_path, capsys):
    """`pha reindex --page P` alone would hit page P of EVERY document — refuse."""
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_reindex(cfg, SimpleNamespace(path=None, doc=None, page=3, force=False))
    assert exc.value.code == 2
    assert "--page requires --doc" in capsys.readouterr().err


def test_reindex_doc_and_path_are_mutually_exclusive(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_reindex(cfg, SimpleNamespace(path="collections/COLX", doc=4, page=None,
                                             force=False))
    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_reindex_repeated_doc_flags_accumulate(monkeypatch, tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    seen = {}

    class _DummyClient:
        def close(self):
            pass

    monkeypatch.setattr(cli, "_client", lambda *a, **k: _DummyClient())

    def fake_reindex(cfg_, client, **kwargs):
        seen.update(kwargs)
        return {"reindexed": 2, "chunks": {}, "failed": [], "skipped_not_done": []}

    monkeypatch.setattr(cli, "reindex_all", fake_reindex)

    cli.cmd_reindex(cfg, SimpleNamespace(path=None, doc=[110, 111], page=None,
                                         force=False))

    assert seen["doc"] == [110, 111]
    assert "#110" in capsys.readouterr().out


def test_reindex_page_needs_exactly_one_doc(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_reindex(cfg, SimpleNamespace(path=None, doc=[110, 111], page=[3],
                                             force=False))
    assert exc.value.code == 2
    assert "exactly one --doc" in capsys.readouterr().err

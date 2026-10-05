"""`pha encode --path/--doc/--dry-run` targeting."""
from __future__ import annotations

import time
from pathlib import Path

from personal_historical_archive import db as _db
from personal_historical_archive import ingest
from personal_historical_archive.config import Config


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n", encoding="utf-8")
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _doc(cfg: Config, rel: str) -> int:
    src = cfg.dropbox / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF fake")
    conn = _db.connect(cfg.db_path)
    try:
        doc_id = _db.add_document(
            conn, filename=src.name, path=str(src), sha256=rel, size_bytes=1,
            mtime=1, kind="pdf", now=time.time(),
            dir_path=str(src.parent.relative_to(cfg.dropbox)),
        )
        conn.commit()
    finally:
        conn.close()
    return doc_id


def _fake_encode(calls: list[int]):
    def run(cfg, conn, doc_id, **kwargs):
        calls.append(doc_id)
        return {"action": "encoded", "filename": str(doc_id), "encoder": "e", "records": 0}
    return run


def test_encode_path_targets_only_the_subtree(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    colx = _doc(cfg, "collections/COLX/a.pdf")
    _doc(cfg, "collections/COLY/b.pdf")
    calls: list[int] = []
    monkeypatch.setattr(ingest, "encode_document", _fake_encode(calls))

    result = ingest.encode_documents(cfg, path="collections/COLX")

    assert calls == [colx]
    assert [r["action"] for r in result["results"]] == ["encoded"]


def test_encode_doc_targets_one_document(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _doc(cfg, "collections/COLX/a.pdf")
    second = _doc(cfg, "collections/COLY/b.pdf")
    calls: list[int] = []
    monkeypatch.setattr(ingest, "encode_document", _fake_encode(calls))

    result = ingest.encode_documents(cfg, doc_id=second)

    assert calls == [second]
    assert result["results"][0]["action"] == "encoded"


def test_encode_dry_run_plans_without_calling_the_model(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    first = _doc(cfg, "collections/COLX/a.pdf")
    calls: list[int] = []
    monkeypatch.setattr(ingest, "encode_document", _fake_encode(calls))

    result = ingest.encode_documents(cfg, dry_run=True)

    assert calls == []
    planned = [r for r in result["results"] if r["action"] == "planned"]
    assert planned
    assert any(str(first) in str(r["filename"]) or r["path"].endswith("a.pdf") for r in planned)


def test_encode_path_outside_dropbox_is_empty(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _doc(cfg, "collections/COLX/a.pdf")
    calls: list[int] = []
    monkeypatch.setattr(ingest, "encode_document", _fake_encode(calls))

    result = ingest.encode_documents(cfg, path="/definitely/not/the/dropbox")

    assert result["results"] == []
    assert calls == []


def test_cmd_encode_reports_lost_windows(monkeypatch, capsys):
    from types import SimpleNamespace

    from personal_historical_archive import cli

    monkeypatch.setattr(cli, "encode_documents", lambda *a, **k: {"results": [{
        "action": "encoded", "filename": "d.pdf", "encoder": "letters",
        "records": 174, "windows": 56,
        "lost_windows": [{"pages": "pages 779-796",
                          "reason": "truncated at the output cap"}],
    }]})
    args = SimpleNamespace(path="collections/COLX", doc=None, reprocess=False,
                           dry_run=False, include_leased=False)

    cli.cmd_encode(None, args)

    out, err = capsys.readouterr()
    assert "WINDOW(S) LOST" in out
    assert "lost pages 779-796" in err
    assert "incomplete" in err

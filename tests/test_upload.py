from __future__ import annotations

from pathlib import Path

import pytest

import personal_historical_archive.config as c
from personal_historical_archive.config import Config
from personal_historical_archive.upload import (
    _resolve_dest_b64,
    classify,
    save_upload,
    upload,
)


def _cfg(tmp_path: Path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  palaeographers: palaeographers\n"
        "  editors: editors\n  encoders: encoders\n  prompts: prompts\n"
    )
    # guard: tmp_path has no .env, but clear in case the runner shares one
    (root / ".env").unlink(missing_ok=True)
    return Config.load(root)


def test_classify_file(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    assert classify(f) == "document"


def test_classify_imagedir(tmp_path):
    d = tmp_path / "ms"
    d.mkdir()
    (d / "p01.png").write_bytes(b"x")
    assert classify(d) == "document"


def test_classify_collection(tmp_path):
    d = tmp_path / "col"
    d.mkdir()
    (d / "a.txt").write_text("a")
    sub = d / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("b")
    assert classify(d) == "collection"


def test_upload_document_file(tmp_path):
    cfg = _cfg(tmp_path)
    drop = cfg.dropbox
    src = tmp_path / "src.pdf"
    src.write_bytes(b"DATA")
    res = upload(cfg, str(src), "document")
    assert res["kind"] == "document"
    assert (drop / "documents" / "src.pdf").read_bytes() == b"DATA"


def test_upload_is_doc_dir(tmp_path):
    # an image-directory is a document, goes to documents/<name>/
    cfg = _cfg(tmp_path)
    drop = cfg.dropbox
    ms = tmp_path / "ms123"
    ms.mkdir()
    (ms / "p01.png").write_bytes(b"P")
    upload(cfg, str(ms), "document")
    assert (drop / "documents" / "ms123" / "p01.png").exists()
    assert not (drop / "collections" / "ms123").exists()


def test_upload_exists_refused(tmp_path):
    cfg = _cfg(tmp_path)
    drop = cfg.dropbox
    src = tmp_path / "src.pdf"
    src.write_bytes(b"V1")
    upload(cfg, str(src), "document")
    src.write_bytes(b"V2")
    with pytest.raises(FileExistsError):
        upload(cfg, str(src), "document")
    assert (drop / "documents" / "src.pdf").read_bytes() == b"V1"


def test_upload_replace(tmp_path):
    cfg = _cfg(tmp_path)
    drop = cfg.dropbox
    src = tmp_path / "src.pdf"
    src.write_bytes(b"V1")
    upload(cfg, str(src), "document")
    src.write_bytes(b"V2")
    upload(cfg, str(src), "document", replace=True)
    assert (drop / "documents" / "src.pdf").read_bytes() == b"V2"


def test_upload_merge_file(tmp_path):
    cfg = _cfg(tmp_path)
    drop = cfg.dropbox
    src = tmp_path / "src.pdf"
    src.write_bytes(b"V1")
    upload(cfg, str(src), "document")
    src.write_bytes(b"V2")
    upload(cfg, str(src), "document", merge=True)
    assert (drop / "documents" / "src.pdf").read_bytes() == b"V2"


def test_upload_collection(tmp_path):
    cfg = _cfg(tmp_path)
    drop = cfg.dropbox
    col = tmp_path / "col"
    col.mkdir()
    (col / "p1.pdf").write_bytes(b"P1")
    (col / "prompt.md").write_text("prompt")
    sub = col / "sub"
    sub.mkdir()
    (sub / "x.txt").write_text("x")
    res = upload(cfg, str(col), "collection")
    assert res["kind"] == "collection"
    assert (drop / "collections" / "col" / "p1.pdf").exists()
    assert (drop / "collections" / "col" / "prompt.md").exists()
    assert (drop / "collections" / "col" / "sub" / "x.txt").exists()


def test_resolve_dest_b64(tmp_path):
    cfg = _cfg(tmp_path)
    d = _resolve_dest_b64(cfg, "document", "doc.pdf")
    assert d == cfg.dropbox / "documents" / "doc.pdf"
    c2 = _resolve_dest_b64(cfg, "collection", "pfister/extra.pdf")
    assert c2 == cfg.dropbox / "collections" / "pfister" / "extra.pdf"


def test_save_upload_refuses_existing(tmp_path):
    cfg = _cfg(tmp_path)
    dest = cfg.dropbox / "documents" / "x.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"A")
    with pytest.raises(FileExistsError):
        save_upload(cfg, "document", "x.pdf", b"B", dest, True, False, False)
    assert dest.read_bytes() == b"A"


def test_save_upload_writes(tmp_path):
    cfg = _cfg(tmp_path)
    dest = cfg.dropbox / "documents" / "y.pdf"
    res = save_upload(cfg, "document", "y.pdf", b"DATA", dest, False, False, False)
    assert dest.read_bytes() == b"DATA"
    assert res["destination"] == str(dest)

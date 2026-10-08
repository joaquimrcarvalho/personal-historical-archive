from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive.structure import (
    StructureError,
    default_structure_path,
    parse_pages,
    resolve_encoder_pages,
    write_pass_copy,
)


def _encoder(pages: str, structure: str = ""):
    return SimpleNamespace(pages=pages, structure=structure)


def _doc(source: Path, sha256: str = "abc", page_count: int = 20) -> dict:
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-1.4 fake")
    return {"path": str(source), "sha256": sha256, "page_count": page_count}


def _write_register(path: Path, pages: dict, sha256: str = "abc") -> None:
    path.write_text(json.dumps({
        "schema": 1,
        "document": path.name,
        "source_sha256": sha256,
        "pages_total": 20,
        "pages": pages,
    }), encoding="utf-8")


def test_default_structure_path_for_file_and_directory(tmp_path):
    assert default_structure_path(tmp_path / "doc.pdf") == tmp_path / "doc.structure.json"
    assert default_structure_path(tmp_path / "pages") == tmp_path / "pages.structure.json"


def test_literal_pages_still_work_without_a_register(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source)
    resolved = resolve_encoder_pages(cfg, doc, _encoder("1-3"), "documents")
    assert resolved.pages == "1-3"
    assert resolved.page_filter == frozenset({1, 2, 3})
    assert resolved.register_path is None


def test_structure_pages_resolve_group_from_register(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source)
    _write_register(default_structure_path(source), {
        "documents": "4-8",
        "apparatus": "1-3",
    })
    resolved = resolve_encoder_pages(
        cfg, doc, _encoder("@structure:documents"), "documents", page_count=20,
    )
    assert resolved.pages == "4-8"
    assert resolved.page_filter == frozenset({4, 5, 6, 7, 8})
    assert resolved.group == "documents"
    assert resolved.register_path == default_structure_path(source)


def test_structure_group_defaults_to_encoder_id(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source)
    _write_register(default_structure_path(source), {"documents": "4-8"})
    resolved = resolve_encoder_pages(
        cfg, doc, _encoder("@structure"), "documents", page_count=20,
    )
    assert resolved.pages == "4-8"


def test_missing_register_refuses_and_suggests_prescan(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source)
    with pytest.raises(StructureError) as err:
        resolve_encoder_pages(cfg, doc, _encoder("@structure:documents"), "documents")
    assert "structure prescan" in str(err.value)


def test_missing_group_refuses(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source)
    _write_register(default_structure_path(source), {"apparatus": "1-3"})
    with pytest.raises(StructureError) as err:
        resolve_encoder_pages(cfg, doc, _encoder("@structure:documents"), "documents")
    assert "no page group" in str(err.value)


def test_source_hash_mismatch_refuses(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source, sha256="new")
    _write_register(default_structure_path(source), {"documents": "1-3"}, sha256="old")
    with pytest.raises(StructureError) as err:
        resolve_encoder_pages(cfg, doc, _encoder("@structure:documents"), "documents")
    assert "source_sha256 mismatch" in str(err.value)


def test_page_range_outside_document_refuses(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source, page_count=10)
    _write_register(default_structure_path(source), {"documents": "1-999"})
    with pytest.raises(StructureError) as err:
        resolve_encoder_pages(
            cfg, doc, _encoder("@structure:documents"), "documents", page_count=10,
        )
    assert "outside 1..10" in str(err.value)


def test_explicit_structure_path(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source)
    explicit = tmp_path / "layouts" / "special.json"
    explicit.parent.mkdir()
    _write_register(explicit, {"documents": "6-9"})
    resolved = resolve_encoder_pages(
        cfg, doc, _encoder("@structure:documents", structure=str(explicit)),
        "documents", page_count=20,
    )
    assert resolved.pages == "6-9"
    assert resolved.register_path == explicit


def test_write_pass_copy_writes_structure_json(tmp_path):
    cfg = SimpleNamespace(dropbox=tmp_path)
    source = tmp_path / "doc.pdf"
    doc = _doc(source)
    register = default_structure_path(source)
    _write_register(register, {"documents": "1-3"})
    resolved = resolve_encoder_pages(
        cfg, doc, _encoder("@structure:documents"), "documents", page_count=20,
    )
    lib = tmp_path / "library" / "doc"
    lib.mkdir(parents=True)
    copied = write_pass_copy(resolved, lib)
    assert copied == lib / "structure.json"
    assert json.loads(copied.read_text(encoding="utf-8"))["pages"]["documents"] == "1-3"


def test_encode_needed_uses_structure_hash(tmp_path):
    import time as _t
    from personal_historical_archive import db
    from personal_historical_archive.ingest import _encode_needed

    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    from personal_historical_archive.config import Config
    cfg = Config.load(root)
    src = cfg.dropbox / "collections" / "tcol" / "doc.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"%PDF-1.4 fake")
    conn = db.connect(cfg.db_path)
    doc_id = db.add_document(
        conn, filename="doc.pdf", path=str(src), sha256="a", size_bytes=10,
        mtime=1, kind="pdf", dir_path="collections/tcol", now=_t.time(),
    )
    pid = db.add_page(conn, doc_id, 1)
    db.set_page_result(conn, pid, raw_text="TEXT")
    db.add_record(conn, doc_id, "documents", "document", '{"text": "x"}')
    conn.commit()
    lib = cfg.library / "collections" / "tcol" / "doc"
    lib.mkdir(parents=True)
    stamp = lib / ".structure-documents.sha256"
    stamp.write_text("abc", encoding="utf-8")
    encoder = SimpleNamespace(prompt_file=None)
    common = dict(
        cfg=cfg, conn=conn, doc_id=doc_id, encoder=encoder, resolved="documents",
        doc_path=src, reprocess=False, enc_file=None,
        structure_sha="abc", library_dir=lib,
    )
    assert _encode_needed(**common) is False
    stamp.write_text("old", encoding="utf-8")
    assert _encode_needed(**common) is True
    conn.close()

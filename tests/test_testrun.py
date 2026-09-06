from __future__ import annotations

from pathlib import Path

import fitz  # pymupdf

import personal_historical_archive.testrun as tr
from personal_historical_archive.config import Config
from personal_historical_archive.extract import render_document


def _make_cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _col_doc(cfg: Config, name="doc.pdf") -> Path:
    col = cfg.dropbox / "collections" / "colx"
    col.mkdir(parents=True, exist_ok=True)
    doc = col / name
    doc.write_bytes(b"%PDF-1.4 fake")
    return doc


# --------------------------------------------------------------------------- page selection

def test_select_pages_sequential():
    assert tr._select_pages(5, 3, False, None) == [1, 2, 3]
    assert tr._select_pages(2, 5, False, None) == [1, 2]      # clamped to total
    assert tr._select_pages(0, 3, False, None) == []
    assert tr._select_pages(5, 1, False, None) == [1]
    assert tr._select_pages(5, 5, False, None) == [1, 2, 3, 4, 5]


def test_select_pages_random_reproducible():
    a = tr._select_pages(50, 7, True, 42)
    b = tr._select_pages(50, 7, True, 42)
    assert a == b
    assert len(a) == 7
    assert all(1 <= x <= 50 for x in a)
    assert tr._select_pages(50, 7, True, 43) != a   # different seed -> different sample


# --------------------------------------------------------------------------- config resolution

def test_resolve_palaeographer_from_sidecar(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = _col_doc(cfg)
    (doc.parent / "pha.yaml").write_text(
        "palaeographer:\n  rules: default\n  model: default\n"
    )
    sc = tr.resolve_sidecar(cfg.dropbox, doc.parent, stem="doc")
    pal, pal_id, src = tr._resolve_palaeographer(cfg, doc, sc, None, None, None, None)
    assert pal_id == "default"
    assert "pha.yaml" in src
    assert pal.model  # bound to the default model interface


def test_resolve_palaeographer_override_flag(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = _col_doc(cfg)
    sc = tr.resolve_sidecar(cfg.dropbox, doc.parent, stem="doc")
    pal, pal_id, src = tr._resolve_palaeographer(cfg, doc, sc, "default", None, None, None)
    assert pal_id == "default"
    assert src.startswith("flag:")


def test_resolve_palaeographer_temperature_override(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = _col_doc(cfg)
    sc = tr.resolve_sidecar(cfg.dropbox, doc.parent, stem="doc")
    pal, _pid, _src = tr._resolve_palaeographer(cfg, doc, sc, "default", None, 0.7, 1234)
    assert pal.temperature == 0.7
    assert pal.max_tokens == 1234


def test_resolve_editor_none_and_null(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = _col_doc(cfg)
    sc = tr.resolve_sidecar(cfg.dropbox, doc.parent, stem="doc")
    plan = tr._resolve_editor(cfg, doc, sc, None, None, None, None)
    assert plan.kind == "none"

    plan2 = tr._resolve_editor(cfg, doc, sc, "passthrough", None, None, None)
    assert plan2.kind == "null"
    assert plan2.id == "passthrough"

    plan3 = tr._resolve_editor(cfg, doc, sc, "none", None, None, None)
    assert plan3.kind == "none"


def test_resolve_editor_from_sidecar(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = _col_doc(cfg)
    (doc.parent / "pha.yaml").write_text(
        "editor:\n  rules: default\n  model: default\n"
    )
    sc = tr.resolve_sidecar(cfg.dropbox, doc.parent, stem="doc")
    plan = tr._resolve_editor(cfg, doc, sc, None, None, None, None)
    assert plan.kind == "editor"
    assert plan.id == "default"
    assert plan.editor.model  # bound to default model interface


def test_resolve_encoders_from_sidecar(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = _col_doc(cfg)
    (doc.parent / "pha.yaml").write_text(
        "encoders:\n  - {rules: default, model: default}\n"
    )
    sc = tr.resolve_sidecar(cfg.dropbox, doc.parent, stem="doc")
    encs = tr._resolve_encoders(cfg, doc, sc, None, None, None, None)
    assert len(encs) == 1
    assert encs[0][2] == "default"
    assert encs[0][0].model  # the sidecar's own model: was bound to the encoder


def test_resolve_encoders_override(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = _col_doc(cfg)
    sc = tr.resolve_sidecar(cfg.dropbox, doc.parent, stem="doc")
    encs = tr._resolve_encoders(cfg, doc, sc, "default", None, None, None)
    assert len(encs) == 1
    assert encs[0][2] == "default"
    assert encs[0][3].startswith("flag:")


# --------------------------------------------------------------------------- page rendering

def test_render_document_pages_filter(tmp_path):
    pdf = tmp_path / "doc.pdf"
    d = fitz.open()
    for _ in range(3):
        d.new_page()
    d.save(str(pdf))
    d.close()
    out = tmp_path / "r"
    files = render_document(pdf, out, 72, 1800, 88, pages={1, 3})
    names = sorted(p.name for p in files)
    assert names == ["p001.jpg", "p003.jpg"]  # absolute page indices preserved


def test_page_units_dir_selected(tmp_path):
    cfg = _make_cfg(tmp_path)
    leaf = cfg.dropbox / "docsv"
    leaf.mkdir()
    for i, name in enumerate(["a.jpg", "b.jpg", "c.jpg"]):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
        pix.set_rect(fitz.IRect(0, 0, 8, 8), (i, i, i))
        pix.save(str(leaf / name))
    units = tr._page_units(cfg, leaf, [1, 3], tmp_path / "renders")
    assert [u[0] for u in units] == [1, 3]     # selected pages
    assert [u[2] for u in units] == ["a", "c"]  # source stems


# --------------------------------------------------------------------------- target resolution + skip

def test_resolve_target_single_file(tmp_path):
    cfg = _make_cfg(tmp_path)
    doc = cfg.dropbox / "documents" / "x.pdf"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(b"%PDF-1.4 fake")
    docs = tr._resolve_target(cfg, "documents/x.pdf")
    assert [d.name for d in docs] == ["x.pdf"]


def test_resolve_target_whole_dropbox(tmp_path):
    cfg = _make_cfg(tmp_path)
    _col_doc(cfg)
    docs = tr._resolve_target(cfg, None)
    assert len(docs) >= 1


def test_run_test_no_match_skips(tmp_path):
    cfg = _make_cfg(tmp_path)
    res = tr.run_test(cfg, "collections/ghost", pages=2, verbose=False)
    assert res.get("skipped") is True

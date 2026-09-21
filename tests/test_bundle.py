from __future__ import annotations

import json
import time
from pathlib import Path

from personal_historical_archive import db as _db
from personal_historical_archive.bundle import export_bundle, import_bundle
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import (
    _raw_sha,
    sha256_of,
    write_document_pages,
    write_edited_pages,
    write_records_file,
)
from personal_historical_archive.model_client import ModelError


class _NoEmbed:
    """Stub embedding client: no server in tests -> text-only indexing."""

    def __init__(self, *a, **k):
        pass

    def embed(self, *a, **k):
        raise ModelError("no embed server in tests")

    def close(self):
        pass


def _make_cfg(tmp_path: Path, name: str) -> Config:
    root = tmp_path / name
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    return Config.load(root)


def _seed_archive_a(cfg: Config, tmp_path: Path) -> Config:
    """A collection COLX with one fully scanned+edited document (palaeographer
    p1, editor e1, one reviewed page, encoder records, renders). Returns a
    reloaded Config so the freshly written defs are known."""
    cfg.ensure_dirs()
    (cfg.palaeographers_dir / "p1.md").write_text(
        "---\ndescription: test pal\nmodel: fake-vision\n---\nTranscribe faithfully.\n"
    )
    (cfg.editors_dir / "e1.md").write_text(
        "---\ndescription: test editor\nmodel: fake-text\n---\nModernize.\n"
    )
    cfg = Config.load(cfg.root)
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake content")

    sha = sha256_of(src)
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(
        conn, filename="doc.pdf", path=str(src), sha256=sha,
        size_bytes=src.stat().st_size, mtime=src.stat().st_mtime, kind="pdf",
        now=time.time(), dir_path="collections/COLX",
        palaeographer="p1", editor="e1",
    )
    raw = {1: "PAGE ONE RAW TEXT Lorem ipsum dolor.", 2: "PAGE TWO RAW TEXT Consectetur."}
    for pno, text in raw.items():
        pid = _db.add_page(conn, doc_id, pno)
        _db.set_page_result(conn, pid, raw_text=text)
    # page 1 is human-reviewed
    _db.mark_page_reviewed(conn, _db.get_pages(conn, doc_id)[0]["id"],
                           "PAGE ONE RAW TEXT (historian fixed).")
    for pno, text in raw.items():
        pid = conn.execute(
            "SELECT id FROM pages WHERE document_id=? AND page_no=?", (doc_id, pno)
        ).fetchone()["id"]
        _db.set_page_edit(conn, pid, "e1", text=f"EDITED {pno}: {text}", raw_sha=_raw_sha(text))
    _db.add_record(conn, doc_id, "enc1", "letter",
                   json.dumps({"kind": "letter", "text": "Carta ao mosteiro"}))
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    write_document_pages(cfg, conn, doc_id)
    write_edited_pages(cfg, conn, doc_id, "e1", model=None)
    write_records_file(cfg, conn, doc_id, "enc1")
    conn.close()

    # renders folder keyed by sha (as ingest produces)
    rdir = cfg.renders / sha
    rdir.mkdir(parents=True)
    (rdir / "p001.jpg").write_bytes(b"jpeg")
    return {"cfg": cfg, "doc_id": doc_id, "sha": sha, "path": str(src)}


def test_bundle_unbundle_roundtrip_into_populated_archive(tmp_path, monkeypatch):
    """A scanned+edited collection moves from A into B, which already has its
    own documents: new ids, no re-scan/edit, search works, reviewed stamps and
    records survive, defs travel."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)

    cfg_a = _make_cfg(tmp_path, "projA")
    seeded = _seed_archive_a(cfg_a, tmp_path)
    cfg_a, a = seeded["cfg"], seeded
    bundle_dir = tmp_path / "bundle"

    # bare collection name resolution + export
    res = export_bundle(cfg_a, ["COLX"], out=bundle_dir, verbose=False)
    assert res["documents"] == 1
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "dropbox/collections/COLX/doc.pdf").exists()
    assert (bundle_dir / "defs/palaeographers/p1.md").exists()
    assert (bundle_dir / "defs/editors/e1.md").exists()
    assert (bundle_dir / f"renders/{a['sha']}/p001.jpg").exists()
    lib_files = list((bundle_dir / "library").rglob("transcription-*/page-*.md"))
    assert len(lib_files) == 2

    # --- archive B already has its own document
    cfg_b = _make_cfg(tmp_path, "projB")
    cfg_b.ensure_dirs()
    other = cfg_b.dropbox / "collections" / "OTHER"
    other.mkdir(parents=True)
    keep = other / "keep.pdf"
    keep.write_bytes(b"%PDF-1.4 other")
    conn_b = _db.connect(cfg_b.db_path)
    keep_id = _db.add_document(
        conn_b, filename="keep.pdf", path=str(keep), sha256="keepsha",
        size_bytes=1, mtime=1, kind="pdf", now=time.time(), dir_path="collections/OTHER",
    )
    _db.set_document_status(conn_b, keep_id, "done")
    conn_b.commit()
    conn_b.close()

    res = import_bundle(cfg_b, bundle_dir, verbose=False)
    assert res["action"] == "imported"
    assert len(res["imported"]) == 1
    assert res["skipped_documents"] == []
    new_id = res["imported"][0]["id"]
    assert new_id != a["doc_id"]
    assert new_id != keep_id

    conn = _db.connect(cfg_b.db_path)
    try:
        docs = _db.list_documents(conn, limit=100)
        assert len(docs) == 2
        doc = _db.get_document(conn, new_id)
        assert doc["path"] == str(cfg_b.dropbox / "collections/COLX/doc.pdf")
        assert doc["sha256"] == a["sha"]
        assert doc["status"] == "done"
        assert doc["palaeographer"] == "p1"
        assert doc["editor"] == "e1"
        # keep.pdf untouched
        assert _db.get_document(conn, keep_id)["dir_path"] == "collections/OTHER"

        pages = _db.get_pages(conn, new_id)
        assert len(pages) == 2
        assert pages[0]["raw_text"] == "PAGE ONE RAW TEXT (historian fixed)."
        assert pages[1]["raw_text"] == "PAGE TWO RAW TEXT Consectetur."
        assert pages[0]["reviewed_at"] is not None
        assert pages[1]["reviewed_at"] is None

        edits = _db.edits_for_document(conn, new_id, "e1")
        assert len(edits) == 2
        assert all(e["status"] == "done" for e in edits)
        assert edits[0]["text"].startswith("EDITED 1")

        recs = _db.records_for_document(conn, new_id)
        assert len(recs) == 1
        assert recs[0]["encoder"] == "enc1"
        assert recs[0]["kind"] == "letter"

        # search finds the imported text (FTS; embeddings stubbed to text-only)
        hits = _db.keyword_search(conn, "historian fixed", limit=5)
        assert any(h["document_id"] == new_id for h in hits)
        hits = _db.keyword_search(conn, "EDITED", limit=5)
        assert any(h["document_id"] == new_id for h in hits)
    finally:
        conn.close()

    # library regenerated in B with B's ids
    b_col_lib = cfg_b.library / "collections/COLX"
    slug_dirs = [d for d in b_col_lib.glob("*") if d.is_dir()]
    assert len(slug_dirs) == 1
    b_lib = slug_dirs[0]
    t_file = b_lib / "transcription-p1" / "page-001.md"
    assert t_file.exists()
    fm = t_file.read_text(encoding="utf-8").split("---", 2)[1]
    assert f"document_id: {new_id}" in fm
    assert f"source: {cfg_b.dropbox / 'collections/COLX/doc.pdf'}" in fm
    assert (b_lib / "edited-e1" / "page-001.md").exists()
    assert (b_lib / "records-enc1.json").exists()

    # defs travelled and were installed only because B lacked them
    assert (cfg_b.palaeographers_dir / "p1.md").exists()
    assert (cfg_b.editors_dir / "e1.md").exists()

    # B must now RESOLVE the same palaeographer/editor for the imported doc
    # (pinned selection files), so `pha scan`/`pha edit` will skip it.
    from personal_historical_archive.extract import resolve_palaeographer_id, resolve_editor_id
    fdir = cfg_b.dropbox / "collections" / "COLX"
    rpal, _s = resolve_palaeographer_id("doc", fdir, cfg_b.dropbox)
    red, _s = resolve_editor_id("doc", fdir, cfg_b.dropbox)
    assert rpal == "p1"
    assert red == "e1"
    assert (fdir / "palaeographer").exists()
    assert (fdir / "editor").exists()

    # re-import is idempotent: doc skipped, nothing duplicated
    res2 = import_bundle(cfg_b, bundle_dir, verbose=False)
    assert res2["skipped_documents"] == ["collections/COLX/doc.pdf"]
    conn = _db.connect(cfg_b.db_path)
    try:
        assert len(_db.list_documents(conn, limit=100)) == 2
    finally:
        conn.close()


def test_unbundle_skips_mismatched_file_and_force_replaces(tmp_path, monkeypatch):
    """A different file already at the same dropbox path is not clobbered
    without --force; with --force the bundle version wins."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)

    cfg_a = _make_cfg(tmp_path, "projA")
    seeded = _seed_archive_a(cfg_a, tmp_path)
    cfg_a, a = seeded["cfg"], seeded
    bundle_dir = tmp_path / "bundle"
    export_bundle(cfg_a, ["collections/COLX"], out=bundle_dir, verbose=False)

    cfg_b = _make_cfg(tmp_path, "projB")
    cfg_b.ensure_dirs()
    target = cfg_b.dropbox / "collections/COLX/doc.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.4 DIFFERENT VERSION")  # sha mismatch

    # no force: content differs -> document skipped
    res = import_bundle(cfg_b, bundle_dir, verbose=False)
    assert res["imported"] == []
    assert res["skipped_documents"] == ["collections/COLX/doc.pdf"]

    # force: bundle copy overwrites, document imported
    res = import_bundle(cfg_b, bundle_dir, force=True, verbose=False)
    assert len(res["imported"]) == 1
    conn = _db.connect(cfg_b.db_path)
    try:
        doc = _db.get_document(conn, res["imported"][0]["id"])
        assert doc["sha256"] == a["sha"]
        assert len(_db.get_pages(conn, doc["id"])) == 2
    finally:
        conn.close()


def test_bundle_move_removes_from_source(tmp_path, monkeypatch):
    """--move deletes the bundled documents from A (DB + library + dropbox)
    after the bundle is written; the bundle still imports into B. Files that
    were NOT bundled (no archive record) survive."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg_a = _make_cfg(tmp_path, "projA")
    seeded = _seed_archive_a(cfg_a, tmp_path)
    cfg_a = seeded["cfg"]
    # a stray file with no archive record must survive the move
    stray = cfg_a.dropbox / "collections" / "COLX" / "notes.txt"
    stray.write_text("not a document\n")

    bundle_dir = tmp_path / "bundle"
    res = export_bundle(cfg_a, ["COLX"], out=bundle_dir, move=True, verbose=False)
    assert res["documents"] == 1
    assert res["moved"]["documents"] == 1
    assert res["moved"]["dropbox_paths"] >= 1

    conn = _db.connect(cfg_a.db_path)
    try:
        assert _db.list_documents(conn, limit=100) == []
    finally:
        conn.close()
    assert not (cfg_a.dropbox / "collections/COLX/doc.pdf").exists()
    assert not list((cfg_a.library / "collections/COLX").rglob("*.md"))  # library artifact gone
    assert stray.exists()  # untouched: it was not bundled
    # the collection dir survives (still holds the stray file)
    assert (cfg_a.dropbox / "collections/COLX").exists()

    # the bundle is complete and imports into B
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)
    cfg_b = _make_cfg(tmp_path, "projB")
    res = import_bundle(cfg_b, bundle_dir, verbose=False)
    assert len(res["imported"]) == 1
    conn = _db.connect(cfg_b.db_path)
    try:
        doc = _db.get_document(conn, res["imported"][0]["id"])
        assert doc["palaeographer"] == "p1"
        assert len(_db.get_pages(conn, doc["id"])) == 2
    finally:
        conn.close()


def test_bundle_move_single_doc_keeps_shared_sidecars(tmp_path, monkeypatch):
    """Moving ONE document leaves shared collection files (selection files,
    encoders/) in A for sibling documents."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg_a = _make_cfg(tmp_path, "projA")
    cfg_a.ensure_dirs()
    (cfg_a.palaeographers_dir / "p1.md").write_text(
        "---\ndescription: test pal\nmodel: fake-vision\n---\nTranscribe faithfully.\n"
    )
    cfg_a = Config.load(cfg_a.root)
    col = cfg_a.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    (col / "palaeographer").write_text("p1\n")
    (col / "encoders").mkdir()
    (col / "encoders" / "table.md").write_text("---\nmodel: fake\n---\nExtract.\n")
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 single")
    conn = _db.connect(cfg_a.db_path)
    doc_id = _db.add_document(
        conn, filename="doc.pdf", path=str(src), sha256=sha256_of(src),
        size_bytes=1, mtime=1, kind="pdf", now=time.time(), dir_path="collections/COLX",
        palaeographer="p1", editor=None,
    )
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="ONLY PAGE")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    write_document_pages(cfg_a, conn, doc_id)
    conn.close()

    bundle_dir = tmp_path / "bundle"
    res = export_bundle(cfg_a, ["collections/COLX/doc.pdf"], out=bundle_dir,
                        move=True, verbose=False)
    assert res["documents"] == 1
    assert res["moved"]["documents"] == 1
    # the document itself is gone, the shared selection/encoder files remain
    assert not src.exists()
    assert (col / "palaeographer").exists()
    assert (col / "encoders" / "table.md").exists()
    conn = _db.connect(cfg_a.db_path)
    try:
        assert _db.list_documents(conn, limit=100) == []
    finally:
        conn.close()


def test_bundle_single_document_target(tmp_path, monkeypatch):
    """A single file (not a whole collection) can be bundled: its selection
    files and encoders travel too."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg_a = _make_cfg(tmp_path, "projA")
    cfg_a.ensure_dirs()
    (cfg_a.palaeographers_dir / "p1.md").write_text(
        "---\ndescription: test pal\nmodel: fake-vision\n---\nTranscribe faithfully.\n"
    )
    cfg_a = Config.load(cfg_a.root)
    col = cfg_a.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    (col / "palaeographer").write_text("p1\n")
    (col / "encoders").mkdir()
    (col / "encoders" / "table.md").write_text(
        "---\ndescription: tables\nmodel: fake-enc\n---\nExtract tables.\n"
    )
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 single")
    conn = _db.connect(cfg_a.db_path)
    doc_id = _db.add_document(
        conn, filename="doc.pdf", path=str(src), sha256=sha256_of(src),
        size_bytes=1, mtime=1, kind="pdf", now=time.time(), dir_path="collections/COLX",
        palaeographer="p1", editor=None,
    )
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="ONLY PAGE")
    _db.set_document_status(conn, doc_id, "done")
    conn.commit()
    write_document_pages(cfg_a, conn, doc_id)
    conn.close()

    bundle_dir = tmp_path / "bundle"
    res = export_bundle(cfg_a, ["collections/COLX/doc.pdf"], out=bundle_dir, verbose=False)
    assert res["documents"] == 1
    # selection file + encoder dir + def travelled with the single file
    assert (bundle_dir / "dropbox/collections/COLX/palaeographer").exists()
    assert (bundle_dir / "dropbox/collections/COLX/encoders/table.md").exists()
    assert (bundle_dir / "defs/palaeographers/p1.md").exists()

    cfg_b = _make_cfg(tmp_path, "projB")
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)
    res = import_bundle(cfg_b, bundle_dir, verbose=False)
    assert len(res["imported"]) == 1
    assert (cfg_b.palaeographers_dir / "p1.md").exists()
    conn = _db.connect(cfg_b.db_path)
    try:
        pages = _db.get_pages(conn, res["imported"][0]["id"])
        assert pages[0]["raw_text"] == "ONLY PAGE"
    finally:
        conn.close()


# ------------------------------------------------------- bibliographic sidecar

SIDECAR = """<?xml version="1.0"?>
<mods xmlns="http://www.loc.gov/mods/v3" version="3.8">
  <titleInfo><title>Documentos históricos do Padroado do Oriente</title></titleInfo>
  <recordInfo><recordOrigin>human-supplied</recordOrigin></recordInfo>
</mods>
"""


def test_bundle_carries_the_bibliographic_sidecar(tmp_path, monkeypatch):
    """The reference must travel with the document — a bundle that drops it is
    the failure mode that ruled out a central bibliography file."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)

    cfg_a = _make_cfg(tmp_path, "projA")
    seeded = _seed_archive_a(cfg_a, tmp_path)
    cfg_a = seeded["cfg"]
    sidecar = cfg_a.dropbox / "collections" / "COLX" / "doc.mods.xml"
    sidecar.write_text(SIDECAR, encoding="utf-8")

    bundle_dir = tmp_path / "bundle"
    export_bundle(cfg_a, ["COLX"], out=bundle_dir, verbose=False)
    assert (bundle_dir / "dropbox/collections/COLX/doc.mods.xml").exists()
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"][0]["bibliography"] == ["collections/COLX/doc.mods.xml"]

    cfg_b = _make_cfg(tmp_path, "projB")
    cfg_b.ensure_dirs()
    import_bundle(cfg_b, bundle_dir, verbose=False)
    assert (cfg_b.dropbox / "collections" / "COLX" / "doc.mods.xml").exists()


def test_bundle_without_a_sidecar_still_exports(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)
    cfg_a = _make_cfg(tmp_path, "projA")
    cfg_a = _seed_archive_a(cfg_a, tmp_path)["cfg"]
    bundle_dir = tmp_path / "bundle"
    export_bundle(cfg_a, ["COLX"], out=bundle_dir, verbose=False)
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"][0]["bibliography"] == []


def test_bundle_move_removes_the_bibliographic_sidecar(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)
    cfg_a = _make_cfg(tmp_path, "projA")
    cfg_a = _seed_archive_a(cfg_a, tmp_path)["cfg"]
    sidecar = cfg_a.dropbox / "collections" / "COLX" / "doc.mods.xml"
    sidecar.write_text(SIDECAR, encoding="utf-8")
    export_bundle(cfg_a, ["COLX"], out=tmp_path / "bundle", move=True, verbose=False)
    assert not sidecar.exists()


def test_unbundle_does_not_import_waiting_stubs_as_text(tmp_path, monkeypatch):
    """A `*waiting*` placeholder is the ABSENCE of a page, not its content.

    Regression (pha 0.28.0): the importer fed every library page body straight
    into `set_page_result`, so a partially-processed document arrived fully
    `done` with `*waiting*` as page text — and the receiving machine's
    `pha scan` then skipped it as unchanged, silently converting pending work
    into apparent content. Any handover of a partly-done document hit this.
    """
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr("personal_historical_archive.bundle.ModelClient", _NoEmbed)
    # bundle/import resolve relative dropbox paths against the cwd
    monkeypatch.chdir(tmp_path)

    cfg_a = _make_cfg(tmp_path, "projA")
    cfg_a.ensure_dirs()
    col = cfg_a.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 partially processed")

    sha = sha256_of(src)
    conn = _db.connect(cfg_a.db_path)
    doc_id = _db.add_document(
        conn, filename="doc.pdf", path=str(src), sha256=sha,
        size_bytes=src.stat().st_size, mtime=src.stat().st_mtime, kind="pdf",
        now=time.time(), dir_path="collections/COLX", palaeographer="p1",
    )
    _db.set_document_status(conn, doc_id, "processing")
    # page 1 extracted for real; page 2 never reached (so it gets a stub file)
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text="PAGE ONE real transcription")
    # a scan creates a row for EVERY rendered page up front, so an
    # un-transcribed page exists as a row (and exports as a stub file)
    _db.add_page(conn, doc_id, 2)
    _db.update_document(conn, doc_id, page_count=2)
    conn.commit()
    write_document_pages(cfg_a, conn, doc_id)   # writes page-002.md as a stub
    conn.close()

    stub_file = next((cfg_a.library).rglob("page-002.md"))
    assert "*waiting*" in stub_file.read_text(encoding="utf-8"), "fixture must write a stub"

    bundle_dir = tmp_path / "bundle"
    export_bundle(cfg_a, ["COLX"], out=bundle_dir, verbose=False)

    # --- import into a fresh archive B
    cfg_b = _make_cfg(tmp_path, "projB")
    cfg_b.ensure_dirs()
    import_bundle(cfg_b, bundle_dir, verbose=False)

    conn_b = _db.connect(cfg_b.db_path)
    doc = conn_b.execute("SELECT id, status, page_count FROM documents").fetchone()
    pages = {r["page_no"]: r for r in conn_b.execute(
        "SELECT page_no, raw_text, status FROM pages ORDER BY page_no")}
    conn_b.close()

    # the real page arrived
    assert pages[1]["raw_text"] == "PAGE ONE real transcription"
    assert pages[1]["status"] == "done"
    # the placeholder did NOT become content
    assert pages[2]["raw_text"] in (None, ""), f"stub imported as text: {pages[2]['raw_text']!r}"
    assert pages[2]["status"] != "done"
    # and the document is not claimed complete, so a scan resumes it
    assert doc["status"] != "done", "a partly-carried document must stay resumable"


def test_bundle_carries_pha_yaml_sidecars_and_resolved_models(tmp_path):
    """Regression: a `pha.yaml`-configured document must travel WITH its sidecar
    chain (including a per-document `editor: null`) and with the MODEL files its
    stages name — not just the content-only rules file.

    The export path was written against the legacy `<stem>.palaeographer`
    selection files, so it shipped neither the sidecars nor the models a
    sidecar pairs with its rules; a worker/resolver archive then fell back to
    its own defaults. Also covers the filter-directory copy (`_copytree` typo),
    which only ran when a sidecar named a filter.
    """
    cfg = _make_cfg(tmp_path, "projS")
    cfg.ensure_dirs()
    # content-only stage rules: the MODEL is named by the sidecar, so these
    # files carry no model_ref of their own
    (cfg.palaeographers_dir / "p1.md").write_text(
        "---\ndescription: ocr rules\n---\nTranscribe faithfully.\n"
    )
    (cfg.editors_dir / "e1.md").write_text(
        "---\ndescription: editor rules\n---\nModernize.\n"
    )
    (cfg.models_dir / "m1.md").write_text(
        "---\ndescription: test model\nbase_url: http://127.0.0.1:1234/v1\n"
        "model: fake-model\n---\n"
    )
    # a filter the sidecar names is archive-level and must travel too
    fdir = cfg.filters_dir / "hyphen"
    fdir.mkdir(parents=True)
    (fdir / "filter.py").write_text("def run(value, ctx):\n    return value\n")
    (fdir / "filter.md").write_text("---\nreturns: text\n---\njoin hyphens\n")
    cfg = Config.load(cfg.root)

    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    (col / "pha.yaml").write_text(
        "palaeographer:\n  rules: p1\n  model: m1\n  post:\n    - hyphen\n"
        "editor:\n  rules: e1\n  model: m1\n"
    )
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 sidecar-configured")
    # this volume is read WITHOUT the (paid) editor: the override must travel
    (col / "doc.pha.yaml").write_text("editor: null\n")

    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(
        conn, filename="doc.pdf", path=str(src), sha256=sha256_of(src),
        size_bytes=src.stat().st_size, mtime=src.stat().st_mtime, kind="pdf",
        now=time.time(), dir_path="collections/COLX", palaeographer="p1",
        editor=None,
    )
    _db.update_document(conn, doc_id, page_count=5)
    _db.set_document_status(conn, doc_id, "processing")
    conn.commit()
    conn.close()

    bundle_dir = tmp_path / "bundleS"
    export_bundle(cfg, ["COLX"], out=bundle_dir, verbose=False)

    # the configuration IS the sidecar: both the collection file and the
    # per-document override travel
    assert (bundle_dir / "dropbox/collections/COLX/pha.yaml").is_file()
    assert (bundle_dir / "dropbox/collections/COLX/doc.pha.yaml").is_file()
    # the model the sidecar pairs with the rules travels, despite the rules
    # file having an empty model_ref
    assert (bundle_dir / "defs/models/m1.md").is_file()
    # the filter named by the sidecar travels (regression: `_copytree` typo)
    assert (bundle_dir / "defs/filters/hyphen/filter.py").is_file()

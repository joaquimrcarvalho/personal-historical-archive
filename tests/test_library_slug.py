"""The library folder name is STORED, resolved, and its loss is loud.

Regression coverage for `enhancements/pha-library-slug-timezone-bug-report.md`:

- **F1** — `documents.library_slug` freezes the folder name at creation, so it no
  longer depends on the machine's timezone (the same document used to get two
  folders, and a reader recomputing the name looked in the wrong one);
- **F2** — a write lands in the folder the document already uses, and a read
  finds a variant that lives in a SIBLING folder (an artifact filter wrote 0
  files and called the pass a success before this);
- **F4** — `pha status` and `pha doctor` report a `done` document with no library
  folder at all.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from personal_historical_archive import cli, ingest
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import sha256_of


def _cfg(tmp_path) -> Config:
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


def _add_doc(cfg, *, name="d.pdf", library_slug=None, status="done", pages=1,
             palaeographer="default", editor="latin-to-english"):
    """One document in collections/COLX with `pages` transcribed pages."""
    src = cfg.dropbox / "collections" / "COLX" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF d")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(
        conn, filename=name, path=str(src), sha256=sha256_of(src),
        size_bytes=1, mtime=1, kind="pdf", now=time.time(),
        dir_path="collections/COLX", palaeographer=palaeographer, editor=editor,
        library_slug=library_slug,
    )
    for n in range(1, pages + 1):
        page_id = _db.add_page(conn, doc_id, n)
        _db.set_page_result(conn, page_id, raw_text=f"raw {n}")
    _db.set_document_status(conn, doc_id, status)
    conn.commit()
    conn.close()
    return doc_id


def _doc(cfg, doc_id) -> dict:
    conn = _db.connect(cfg.db_path)
    try:
        return dict(_db.get_document(conn, doc_id))
    finally:
        conn.close()


def _collection(cfg):
    return cfg.library / "collections" / "COLX"


# ------------------------------------------------------------------ F1: stored slug

def test_stored_slug_is_authoritative_over_the_computed_name():
    """A stored name wins even when the computed date would differ.

    This is the whole of F1: the folder name is a property of the ROW, not of
    the machine's clock."""
    doc = {"id": 1, "path": "/x/collections/COLX/d.pdf",
           "created_at": 1787360000.0, "library_slug": "d_2001-02-03"}
    assert ingest._doc_slug(doc) == "d_2001-02-03"


def test_a_row_without_a_stored_slug_still_computes_the_legacy_name():
    """Rows that predate the column keep working (no forced migration)."""
    doc = {"id": 1, "path": "/x/collections/COLX/d.pdf", "created_at": 1787360000.0}
    assert ingest._doc_slug(doc).startswith("d_20")


def test_migrate_adds_library_slug_to_an_existing_archive(tmp_path):
    """An archive written by an older pha gains the column on the next write."""
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    conn = _db.connect(cfg.db_path)
    conn.execute("ALTER TABLE documents DROP COLUMN library_slug")
    conn.commit()
    assert not _db.has_column(conn, "documents", "library_slug")
    conn.close()
    conn = _db.connect(cfg.db_path)  # connect() runs migrate()
    try:
        assert _db.has_column(conn, "documents", "library_slug")
    finally:
        conn.close()


def test_a_new_document_freezes_its_slug_at_creation(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id = _add_doc(cfg, library_slug="d_2001-02-03")
    assert _doc(cfg, doc_id)["library_slug"] == "d_2001-02-03"


# ------------------------------------------------------- F2: resolve before writing

def test_legacy_row_writes_into_the_folder_that_holds_its_pages(tmp_path):
    """A row with no stored slug JOINS the folder its transcription is in.

    The computed name holds only records (the 38/39/40 shape). Writing to the
    computed name would create a SECOND folder for one document — exactly the
    duplication this bug produced."""
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id = _add_doc(cfg, library_slug=None)
    doc = _doc(cfg, doc_id)
    legacy = _collection(cfg) / "d_1999-12-31"
    (legacy / "transcription-default@m").mkdir(parents=True)
    (legacy / "transcription-default@m" / "page-001.md").write_text("body")
    computed = _collection(cfg) / ingest._computed_doc_slug(doc)
    (computed / "segments").mkdir(parents=True)

    assert ingest._resolve_library_dir(cfg, doc) == legacy

    conn = _db.connect(cfg.db_path)
    try:
        ingest.write_document_pages(cfg, conn, doc_id)
        conn.commit()
        # the write joined the legacy folder and stamped it — no second folder
        assert (legacy / "transcription-default").is_dir()
        assert not (computed / "transcription-default").exists()
        assert _db.get_document(conn, doc_id)["library_slug"] == legacy.name
    finally:
        conn.close()
    # and the stamp makes the choice stable from now on
    assert ingest._resolve_library_dir(cfg, _doc(cfg, doc_id)) == legacy


def test_stamping_the_slug_does_not_move_updated_at(tmp_path):
    """The stamp must not hide a pending prompt change.

    `updated_at` is what `_prompt_newer_than` compares a prompt's mtime against,
    so moving it forward inside a pass would make an edit that happened BEFORE
    the pass look older than the pass and skip a needed re-extraction."""
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id = _add_doc(cfg, library_slug=None)
    before = _doc(cfg, doc_id)["updated_at"]
    legacy = _collection(cfg) / "d_1999-12-31"
    (legacy / "transcription-default@m").mkdir(parents=True)

    conn = _db.connect(cfg.db_path)
    try:
        ingest.write_document_pages(cfg, conn, doc_id)
        conn.commit()
        after = _db.get_document(conn, doc_id)
        assert after["library_slug"] == legacy.name  # stamped ...
        assert after["updated_at"] == before         # ... without touching this
    finally:
        conn.close()


def test_pages_dir_for_finds_a_variant_in_a_sibling_folder(tmp_path):
    """The exact §8 regression: transcription+edit in one folder, records in another.

    Before the fix the reader looked only in the computed folder and returned
    None, so an artifact filter wrote 0 files and reported success."""
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id = _add_doc(cfg, library_slug=None)
    doc = _doc(cfg, doc_id)
    legacy = _collection(cfg) / "d_1999-12-31"
    (legacy / "transcription-default@m").mkdir(parents=True)
    (legacy / "edited-latin-to-english@m").mkdir(parents=True)
    computed = _collection(cfg) / ingest._computed_doc_slug(doc)
    (computed / "segments").mkdir(parents=True)

    assert ingest._pages_dir_for(cfg, doc, "transcription-default") == \
        legacy / "transcription-default@m"
    assert ingest._pages_dir_for(cfg, doc, "edited-latin-to-english") == \
        legacy / "edited-latin-to-english@m"


def test_library_page_path_finds_a_page_in_a_sibling_folder(tmp_path):
    """`pha page` / `pha cite` reach the page written under another date."""
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id = _add_doc(cfg, library_slug=None)
    doc = _doc(cfg, doc_id)
    legacy = _collection(cfg) / "d_1999-12-31"
    variant = legacy / "transcription-default@m"
    variant.mkdir(parents=True)
    (variant / "page-001.md").write_text("the page")

    via_disk = ingest.library_page_path(cfg, doc, 1, "raw")
    # library_page_path accepts a sqlite3.Row in production; a dict is the same
    assert via_disk == variant / "page-001.md"


# ------------------------------------------------------------------- F4: loud loss

def test_library_missing_reports_only_a_truly_absent_folder(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    absent = _add_doc(cfg, name="absent.pdf")
    elsewhere = _add_doc(cfg, name="elsewhere.pdf")
    pending = _add_doc(cfg, name="pending.pdf", status="pending")
    # a library under a date this machine no longer computes is NOT missing
    (_collection(cfg) / "elsewhere_1999-12-31").mkdir(parents=True)

    conn = _db.connect(cfg.db_path)
    try:
        ids = {m["id"] for m in ingest.library_missing_documents(cfg, conn)}
    finally:
        conn.close()
    assert absent in ids
    assert elsewhere not in ids
    assert pending not in ids  # only a `done` document is expected to have one


def test_status_flags_a_done_document_with_no_library_folder(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id = _add_doc(cfg, name="absent.pdf")
    cli.cmd_status(cfg, SimpleNamespace(json=False))
    out = capsys.readouterr().out
    assert "library folder missing" in out
    assert f"pha export --doc {doc_id}" in out


def test_status_json_carries_library_missing(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    doc_id = _add_doc(cfg, name="absent.pdf")
    cli.cmd_status(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert [m["doc_id"] for m in data["library_missing"]] == [doc_id]


def test_doctor_reports_a_missing_library():
    from personal_historical_archive import doctor

    report = doctor.diagnose(library_missing=[
        {"doc_id": 7, "filename": "d.pdf", "dir_path": "collections/COLX"}])
    out = doctor.render(report)
    assert "NO library folder" in out
    assert "pha export --doc 7" in out
    # a data condition to fix, not a broken install: `ok` still gates engines
    assert report["ok"] is True
    assert report["library_missing"][0]["doc_id"] == 7


def test_doctor_render_omits_the_section_when_nothing_is_missing():
    from personal_historical_archive import doctor

    out = doctor.render(doctor.diagnose())
    assert "NO library folder" not in out

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import discover, sha256_of


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


def test_status_shows_unscanned_dropbox_files(tmp_path, capsys):
    """pha status reports files that are in the dropbox but not yet in the
    archive (never scanned), before `pha scan` runs — shown as 'new' leaves
    under their collection in the status tree."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()

    # one already-scanned document (has a DB record)
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    scanned = col / "scanned.pdf"
    scanned.write_bytes(b"%PDF scanned")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="scanned.pdf", path=str(scanned),
                     sha256=sha256_of(scanned), size_bytes=1, mtime=1, kind="pdf",
                     now=time.time(), dir_path="collections/COLX")
    _db.set_document_status(conn, _db.get_document_by_path(conn, str(scanned))["id"], "done")
    conn.commit()
    conn.close()

    # two files dropped in but never scanned (one in the collection, one at root)
    fresh1 = col / "fresh.pdf"
    fresh1.write_bytes(b"%PDF fresh")
    fresh2 = cfg.dropbox / "documents" / "rootdoc.pdf"
    fresh2.parent.mkdir(parents=True, exist_ok=True)
    fresh2.write_bytes(b"%PDF root")

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "scanned.pdf" in out                 # the archived doc is listed
    assert "  COLX" in out                      # collection node (prefix stripped)
    assert "1 document (done)" in out
    assert "~ 1 new  (fresh.pdf)" in out        # the unscanned collection file
    assert "~ 1 new  (rootdoc.pdf)" in out      # the unscanned root file
    assert "2 file(s) not yet scanned" in out   # overview tally


def test_status_shows_archived_status_and_new_count(tmp_path, capsys):
    """A collection with both an archived doc and unscanned files shows the
    doc's status and the new count together, so it is not ambiguous."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "CAT"
    col.mkdir(parents=True)
    a = col / "a.pdf"
    a.write_bytes(b"%PDF a")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="a.pdf", path=str(a), sha256=sha256_of(a),
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                     dir_path="collections/CAT")
    _db.set_document_status(conn, _db.get_document_by_path(conn, str(a))["id"], "processing")
    conn.commit()
    conn.close()
    (col / "b.pdf").write_bytes(b"%PDF b")

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "  CAT" in out
    assert "1 document (processing)" in out
    assert "#  1  processing  a.pdf" in out
    assert "~ 1 new  (b.pdf)" in out


def test_status_no_new_section_when_everything_scanned(tmp_path, capsys):
    """No 'new in dropbox' section when every dropbox file has a record."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "doc.pdf"
    src.write_bytes(b"%PDF doc")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="doc.pdf", path=str(src), sha256=sha256_of(src),
                     size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                     dir_path="collections/COLX")
    conn.commit()
    conn.close()

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "new in dropbox" not in out
    assert "doc.pdf" in out


def test_status_shows_on_hold_inbox(tmp_path, capsys):
    """pha status reports documents parked in the inbox as 'on hold' — they are
    shown under an 'on hold (inbox)' section and a tally, not as scanned or new."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    held = cfg.inbox / "collections" / "CAT"
    held.mkdir(parents=True)
    (held / "h.pdf").write_bytes(b"%PDF h")
    (cfg.inbox / "loose.pdf").write_bytes(b"%PDF loose")

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "on hold (inbox)" in out
    assert "2 file(s) on hold" in out
    assert "  CAT" in out
    assert "h.pdf" in out
    assert "loose.pdf" in out


def test_status_no_on_hold_when_inbox_empty_or_absent(tmp_path, capsys):
    """No 'on hold' section when the inbox is empty or does not exist."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "on hold (inbox)" not in out


def test_inbox_dry_run_then_move(tmp_path, capsys):
    """`pha inbox --dry-run` shows the plan without moving; `pha inbox --move`
    relocates held documents (and siblings) into the dropbox, preserving the
    relative layout, and empties the inbox."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    held = cfg.inbox / "collections" / "CAT"
    held.mkdir(parents=True)
    (held / "h.pdf").write_bytes(b"%PDF h")
    (held / "prompt.md").write_text("sidecar")
    (cfg.inbox / "loose.pdf").write_bytes(b"%PDF loose")

    cli.cmd_inbox(cfg, SimpleNamespace(move=False, dry_run=True))
    out = capsys.readouterr().out
    assert "would move 2 file(s)" in out
    assert (cfg.inbox / "loose.pdf").exists()
    assert not (cfg.dropbox / "loose.pdf").exists()

    cli.cmd_inbox(cfg, SimpleNamespace(move=True, dry_run=False))
    out = capsys.readouterr().out
    assert "moved 2 file(s) from the inbox into the dropbox" in out
    assert (cfg.dropbox / "collections" / "CAT" / "h.pdf").exists()
    assert (cfg.dropbox / "collections" / "CAT" / "prompt.md").exists()  # sidecar travels
    assert (cfg.dropbox / "loose.pdf").exists()
    assert not (cfg.inbox / "loose.pdf").exists()


def test_discover_excludes_path(tmp_path):
    """discover(..., exclude=[...]) skips units at/under the excluded path, so
    a nested inbox is never picked up by a scan."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    nested = cfg.dropbox / "inbox"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "held.pdf").write_bytes(b"%PDF held")
    (cfg.dropbox / "documents" / "a.pdf").parent.mkdir(parents=True, exist_ok=True)
    (cfg.dropbox / "documents" / "a.pdf").write_bytes(b"%PDF a")

    units = discover(cfg.dropbox, True, exclude=[cfg.dropbox / "inbox"])
    rels = {str(u.relative_to(cfg.dropbox)) for u in units}
    assert "documents/a.pdf" in rels
    assert not any(r.startswith("inbox") for r in rels)


def test_status_surfaces_done_document_with_no_index(tmp_path, capsys):
    """A `done` document with zero chunks must be visible: this is the state
    doc 57 (documenta-indica) sat in for hours while a status-only check
    reported the collection complete. `done` is now written after indexing, so
    this only fires for rows that predate the fix — which is exactly when it
    matters."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "unindexed.pdf"
    src.write_bytes(b"%PDF unindexed")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="unindexed.pdf", path=str(src),
                              sha256=sha256_of(src), size_bytes=1, mtime=1, kind="pdf",
                              now=time.time(), dir_path="collections/COLX")
    _db.set_document_status(conn, doc_id, "done")
    _db.update_document(conn, doc_id, page_count=961)
    conn.commit()
    conn.close()

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "1 document(s) marked done with no index" in out
    assert "unindexed.pdf" in out
    assert "961 page(s)" in out
    assert "pha reindex" in out



def test_status_json_carries_the_same_numbers_as_the_report(tmp_path, capsys):
    """`pha status --json` is the view's source of truth: the same computation as the
    text report, structured — so a caller never re-derives what is unscanned (document
    units, image-directories and the inbox exclusion are the CLI's rules) nor has to
    walk the inbox itself."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()

    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    scanned = col / "scanned.pdf"
    scanned.write_bytes(b"%PDF scanned")
    conn = _db.connect(cfg.db_path)
    _db.add_document(conn, filename="scanned.pdf", path=str(scanned),
                     sha256=sha256_of(scanned), size_bytes=1, mtime=1, kind="pdf",
                     now=time.time(), dir_path="collections/COLX")
    conn.commit()
    conn.close()
    (col / "new.pdf").write_bytes(b"%PDF new")           # in the dropbox, never scanned
    held = cfg.inbox / "collections" / "COLX"
    held.mkdir(parents=True)
    (held / "held.pdf").write_bytes(b"%PDF held")        # parked in the inbox

    cli.cmd_status(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)

    assert data["ok"] is True
    assert data["documents"] == 1
    assert data["new"] == 1 and data["on_hold"] == 1
    assert data["unscanned"] == [
        {"dir_path": "collections/COLX", "count": 1, "documents": ["new.pdf"]}]
    assert data["in_inbox"] == [
        {"dir_path": "collections/COLX", "count": 1, "documents": ["held.pdf"]}]
    assert data["collections"] == [
        {"dir_path": "collections/COLX", "documents": 1, "new": 1}]
    assert data["archive"].endswith("archive.db")


def test_status_json_is_quiet_about_an_empty_archive(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    cli.cmd_status(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["documents"] == 0 and data["new"] == 0 and data["on_hold"] == 0
    assert data["unscanned"] == [] and data["in_inbox"] == []
    assert data["out_on_handover"] == []


def test_status_json_marks_a_document_out_on_handover(tmp_path, capsys):
    """A leased document is visible to the view: `pha status --json` carries the
    hand-off, its worker and the document ids, so a page-less `processing` row
    reads as 'out on hand-over' instead of looking stuck."""
    from personal_historical_archive import handoff

    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True)
    src = col / "vol.pdf"
    src.write_bytes(b"%PDF vol")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="vol.pdf", path=str(src),
                              sha256=sha256_of(src), size_bytes=1, mtime=1, kind="pdf",
                              now=time.time(), dir_path="collections/COLX")
    _db.set_document_status(conn, doc_id, "processing")
    conn.commit()
    conn.close()

    handoff.export_handoff(cfg, ["collections/COLX"], tmp_path / "ho",
                           worker="studio", verbose=False)

    cli.cmd_status(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)
    groups = data["out_on_handover"]
    assert len(groups) == 1
    assert groups[0]["worker"] == "studio"
    assert groups[0]["state"] == "out"
    assert [d["doc_id"] for d in groups[0]["documents"]] == [doc_id]


# --------------------------------------------------------------------------- stall visibility (F3)

def test_status_flags_a_stalled_processing_document(tmp_path, capsys):
    """A 'processing' document whose row has not moved is named with its age, so
    a stalled model request is not indistinguishable from healthy progress
    (enhancements/pha-request-stall-timeout-bug-report.md, F3)."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "CAT"
    col.mkdir(parents=True)
    a = col / "a.pdf"
    a.write_bytes(b"%PDF a")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="a.pdf", path=str(a), sha256=sha256_of(a),
                              size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                              dir_path="collections/CAT")
    _db.set_document_status(conn, doc_id, "processing")
    # last page written two hours ago
    conn.execute("UPDATE documents SET updated_at=? WHERE id=?",
                 (time.time() - 7200, doc_id))
    conn.commit()
    conn.close()

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "no progress for 2h00m" in out
    assert "stalled?" in out


def test_status_does_not_flag_a_fresh_processing_document(tmp_path, capsys):
    """A document that just started processing is not called stalled."""
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    col = cfg.dropbox / "collections" / "CAT"
    col.mkdir(parents=True)
    a = col / "a.pdf"
    a.write_bytes(b"%PDF a")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="a.pdf", path=str(a), sha256=sha256_of(a),
                              size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                              dir_path="collections/CAT")
    _db.set_document_status(conn, doc_id, "processing")
    conn.commit()
    conn.close()

    cli.cmd_status(cfg, SimpleNamespace())
    assert "stalled?" not in capsys.readouterr().out


def test_age_str_formats():
    assert cli._age_str(5) == "5s"
    assert cli._age_str(12 * 60) == "12m"
    assert cli._age_str(4 * 3600 + 17 * 60) == "4h17m"
    assert cli._age_str(3 * 86400) == "3d"

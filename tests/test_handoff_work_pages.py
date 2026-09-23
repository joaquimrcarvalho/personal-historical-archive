"""G5 — `pha handoff work --pages N[,M] --resume` (stateless resume).

Correcting a page list on the worker meant the owner orchestrating repeated
`pha edit --path … --page N` over ssh from a local script. Two things were
missing: a way to name the pages, and a way to continue an interrupted list.

`--resume` deliberately has NO progress file: the database already knows which
pages are done, so `plan_work` reads it. A page needs the transcription pass when
it has no text (and is not human-reviewed) and the editor pass when the reading
the archive serves for it is missing, not `done`, or was produced from different
raw text. A named page WITHOUT `--resume` is a deliberate re-do.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli, db as _db, handoff
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import _raw_sha, sha256_of


def _cfg(tmp_path: Path, name: str) -> Config:
    root = tmp_path / name
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'arc'}\n"
        "  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _hand_out(tmp_path: Path, *, pages: int = 3, done: int = 1, editor: str | None = "e1"):
    """A's document (partly scanned), handed to B."""
    a = _cfg(tmp_path, "owner")
    col = a.dropbox / "collections" / "DI"
    col.mkdir(parents=True, exist_ok=True)
    src = col / "vol04.pdf"
    src.write_bytes(b"%PDF-1.4 vol04")
    conn = _db.connect(a.db_path)
    doc_id = _db.add_document(
        conn, filename="vol04.pdf", path=str(src), sha256=sha256_of(src),
        size_bytes=src.stat().st_size, mtime=src.stat().st_mtime, kind="pdf",
        now=time.time(), dir_path="collections/DI", palaeographer="default", editor=editor)
    for pno in range(1, pages + 1):
        pid = _db.add_page(conn, doc_id, pno)
        if pno <= done:
            _db.set_page_result(conn, pid, raw_text=f"PAGE {pno} text")
    _db.update_document(conn, doc_id, page_count=pages)
    _db.set_document_status(conn, doc_id, "done" if done >= pages else "processing")
    conn.commit()
    conn.close()

    out = tmp_path / "ho"
    handoff.export_handoff(a, ["collections/DI"], out, verbose=False)
    b = _cfg(tmp_path, "worker")
    handoff.import_handoff(b, out, verbose=False)
    return a, b, out


def _worker_doc(b: Config):
    conn = _db.connect(b.db_path)
    try:
        return conn.execute("SELECT * FROM documents").fetchone()
    finally:
        conn.close()


def _finish(b: Config, page_no: int, *, edit: bool = False, reviewed: bool = False,
            text: str | None = None):
    """The worker finishes a page (as a real scan/edit pass would)."""
    conn = _db.connect(b.db_path)
    try:
        doc = conn.execute("SELECT * FROM documents").fetchone()
        pid = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=?",
                           (doc["id"], page_no)).fetchone()["id"]
        text = text or f"WORKER PAGE {page_no}"
        _db.set_page_result(conn, pid, raw_text=text)
        if edit and doc["editor"]:
            _db.set_page_edit(conn, pid, doc["editor"], text=text.upper(),
                              raw_sha=_raw_sha(text))
            if reviewed:
                _db.mark_edit_reviewed(conn, pid, doc["editor"], text.upper())
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- planner

def test_plan_without_resume_is_a_deliberate_redo(tmp_path):
    _a, b, out = _hand_out(tmp_path, pages=3, done=1)

    plan = handoff.plan_work(b, out, pages={1, 2})
    entry = plan[0]
    assert entry["scan_pages"] == [1, 2]        # named -> both passes re-do them
    assert entry["edit_pages"] == [1, 2]
    assert entry["doc_id"]


def test_plan_resume_names_only_what_is_missing(tmp_path):
    _a, b, out = _hand_out(tmp_path, pages=3, done=1)
    _finish(b, 2, edit=True)                    # page 2 fully done
    _finish(b, 1, edit=True)                    # page 1 was already there

    plan = handoff.plan_work(b, out, pages={1, 2, 3}, resume=True)
    entry = plan[0]
    # page 1 (text + edit) is complete; page 2 (text + edit) is complete;
    # page 3 has no text, so it needs BOTH passes.
    assert entry["scan_pages"] == [3]
    assert entry["edit_pages"] == [3]
    assert entry["doc_id"]


def test_plan_resume_skips_a_finished_document_entirely(tmp_path):
    _a, b, out = _hand_out(tmp_path, pages=2, done=2)
    _finish(b, 1, edit=True)
    _finish(b, 2, edit=True)

    entry = handoff.plan_work(b, out, resume=True)[0]
    assert entry["scan_pages"] == [] and entry["edit_pages"] == []


def test_plan_resume_replans_an_edit_made_from_older_raw_text(tmp_path):
    """A re-read page invalidates its edit: `raw_sha` says so, statelessly."""
    _a, b, out = _hand_out(tmp_path, pages=1, done=1)
    _finish(b, 1, edit=True)
    assert handoff.plan_work(b, out, resume=True)[0]["edit_pages"] == []

    _finish(b, 1, text="A NEW READING")         # re-read: new text, stale edit
    assert handoff.plan_work(b, out, resume=True)[0]["edit_pages"] == [1]


def test_plan_keeps_human_work_out_of_both_lists(tmp_path):
    _a, b, out = _hand_out(tmp_path, pages=2, done=2)
    _finish(b, 1, edit=True, reviewed=True)     # a human corrected the edit here
    conn = _db.connect(b.db_path)
    try:
        doc = conn.execute("SELECT * FROM documents").fetchone()
        pid = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=2",
                           (doc["id"],)).fetchone()["id"]
        _db.mark_page_reviewed(conn, pid, "A HUMAN TRANSCRIPTION")
        conn.commit()
    finally:
        conn.close()

    entry = handoff.plan_work(b, out, resume=True)[0]
    assert 1 not in entry["edit_pages"]         # corrected edit: never re-edited
    assert 2 not in entry["scan_pages"]         # corrected transcription: never re-read
    assert entry["kept_edits"] == [1]
    assert entry["kept_reviewed"] == [2]


def test_plan_reports_a_document_that_was_never_imported(tmp_path):
    _a, b, out = _hand_out(tmp_path, pages=1, done=1)
    manifest = json.loads((out / handoff.MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["documents"][0]["sha256"] = "0" * 64      # not in this archive
    (out / handoff.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    entry = handoff.plan_work(b, out, resume=True)[0]
    assert "handoff in" in entry["reason"]


# --------------------------------------------------------------------------- CLI

def _work_args(out: Path, **kw):
    base = dict(handoff_cmd="work", directory=str(out), pages=None, resume=False,
                dry_run=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_work_dry_run_prints_the_planned_commands(tmp_path, capsys):
    _a, b, out = _hand_out(tmp_path, pages=3, done=1)

    cli.cmd_handoff(b, _work_args(out, pages=["2,3"], resume=True, dry_run=True))
    printed = capsys.readouterr().out

    assert "pha scan --path collections/DI/vol04.pdf --page 2 --page 3" in printed
    assert "pha edit --path collections/DI/vol04.pdf --page 2 --page 3" in printed
    assert "pha encode --path collections/DI/vol04.pdf" in printed


def test_work_runs_pages_and_reports_a_finished_document(tmp_path, monkeypatch, capsys):
    _a, b, out = _hand_out(tmp_path, pages=3, done=3)
    _finish(b, 1, edit=True)
    _finish(b, 2, edit=True)
    _finish(b, 3, edit=True)

    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: calls.append(argv) or 0)

    cli.cmd_handoff(b, _work_args(out, pages=["1,2,3"], resume=True))
    printed = capsys.readouterr().out

    assert "nothing to do" in printed
    assert calls == [], "a complete document must not be touched on resume"


def test_work_resume_runs_only_the_missing_pages(tmp_path, monkeypatch, capsys):
    _a, b, out = _hand_out(tmp_path, pages=3, done=1)
    _finish(b, 1, edit=True)
    _finish(b, 2, edit=True)                     # 1 and 2 done; 3 has no text

    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: calls.append(argv) or 0)

    cli.cmd_handoff(b, _work_args(out, pages=["1,2,3"], resume=True))
    printed = capsys.readouterr().out

    assert "resuming 1 of 3 named page(s)" in printed
    scan = [c for c in calls if c[3] == "scan"]
    assert len(scan) == 1 and scan[0][-1] == "3"      # only page 3
    assert all("encode" in c for c in calls if c[3] == "encode")

"""The Harness plugin reads the archive with an embedded SQL program.

That program is the view's data layer, and it has to work against an archive whose
DB has not been migrated yet — the plugin never migrates anything. The real archive
that prompted this file had no `document_bibliography` table at all, and a plain
LEFT JOIN on it emptied the whole document list ("no such table"). These tests
therefore run the plugin's ACTUAL embedded program, extracted from lib/index.js,
against both the current schema and that legacy one.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from personal_historical_archive import db
from personal_historical_archive.ingest import sha256_of

PLUGIN = Path(__file__).resolve().parents[1] / "dsh-pha" / "lib" / "index.js"


def _unescape(lit: str) -> str:
    """Decode a JS/JSON string literal body (\\n is a real newline, \\' a quote)."""
    escapes = {"n": "\n", "t": "\t", "r": "\r", "\'": "'", '\"': '"', "\\": "\\"}
    out: list[str] = []
    i = 0
    while i < len(lit):
        ch = lit[i]
        if ch == "\\" and i + 1 < len(lit):
            out.append(escapes.get(lit[i + 1], lit[i + 1]))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _db_script() -> str:
    """The plugin's embedded Python program, unescaped from the JS string array."""
    text = PLUGIN.read_text(encoding="utf-8")
    block = text[text.index("const DB_SCRIPT = ["):]
    block = block[: block.index("].join")]
    lines: list[str] = []
    pattern = r"""^\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"),?\s*$"""
    for m in re.finditer(pattern, block, re.M):
        lit = m.group(1) if m.group(1) is not None else m.group(2)
        lines.append(_unescape(lit))
    assert lines, "could not extract DB_SCRIPT from the plugin"
    return "\n".join(lines)


def _run(db_path: Path, op: str, *extra) -> object:
    proc = subprocess.run(
        [sys.executable, "-c", _db_script(), str(db_path), op, *[str(x) for x in extra]],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _seed(tmp_path) -> tuple[Path, int]:
    """One document in collections/COLX, with a stored reference snapshot."""
    src = tmp_path / "dropbox" / "collections" / "COLX" / "d.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF d")
    db_path = tmp_path / "archive.db"
    conn = db.connect(db_path)
    db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                    size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                    dir_path="collections/COLX")
    doc_id = db.get_document_by_path(conn, str(src))["id"]
    db.set_page_result(conn, db.add_page(conn, doc_id, 1), raw_text="body")
    db.set_bibliography(conn, doc_id, sidecar_path=str(src.with_suffix(".dc.json")),
                        sidecar_sha="abc", source_format="dc.json",
                        citation="Someone, *A Title* (1675)", parsed_json="{}",
                        record_origin="human-supplied")
    conn.commit()
    conn.close()
    return db_path, doc_id


def test_plugin_reads_return_the_reference_columns(tmp_path):
    db_path, doc_id = _seed(tmp_path)
    rows = _run(db_path, "documents")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == doc_id
    assert row["reference"] == "Someone, *A Title* (1675)"
    assert row["bib_format"] == "dc.json" and row["bib_origin"] == "human-supplied"
    one = _run(db_path, "document", doc_id)
    assert one["doc"]["reference"] == "Someone, *A Title* (1675)"


def test_plugin_survives_an_archive_without_the_snapshot_table(tmp_path):
    """An unmigrated archive must still list its documents — with no references.

    This is the regression guard: joining `document_bibliography` unconditionally
    made every document disappear from the view on an archive that had not been
    migrated yet.
    """
    db_path, doc_id = _seed(tmp_path)
    conn = db.connect(db_path)
    conn.execute("DROP TABLE document_bibliography")
    conn.commit()
    conn.close()

    rows = _run(db_path, "documents")
    assert len(rows) == 1, "the document list must survive a missing snapshot table"
    assert rows[0]["id"] == doc_id
    assert rows[0]["reference"] is None and rows[0]["bib_sidecar"] is None
    one = _run(db_path, "document", doc_id)
    assert one["doc"]["reference"] is None
    assert len(one["pages"]) == 1


def test_plugin_pagemeta_and_page_reads_still_run(tmp_path):
    db_path, doc_id = _seed(tmp_path)
    meta = _run(db_path, "pagemeta", doc_id, 1)
    assert meta is not None and "sha256" in meta
    one = _run(db_path, "document", doc_id)
    assert one["pages"][0]["page_no"] == 1

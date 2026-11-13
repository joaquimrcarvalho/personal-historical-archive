from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.config import Config


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
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _args(**kw) -> SimpleNamespace:
    """The namespace `pha records` reads, with the parser defaults filled in."""
    base = dict(doc=None, encoder=None, record_kind=None, collection=None,
                field_filters=None, ignore_case=False, limit=20, offset=0,
                json=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _seed(cfg: Config):
    """Two documents, three records: two letters (one letter kind each) and a person."""
    conn = _db.connect(cfg.db_path)
    now = time.time()
    d1 = _db.add_document(
        conn, filename="letters.pdf",
        path=str(cfg.dropbox / "collections/COLX/letters.pdf"),
        sha256="a", size_bytes=1, mtime=now, kind="pdf", now=now,
        dir_path="collections/COLX",
    )
    d2 = _db.add_document(
        conn, filename="people.pdf",
        path=str(cfg.dropbox / "collections/COLX/people.pdf"),
        sha256="b", size_bytes=1, mtime=now, kind="pdf", now=now,
        dir_path="collections/COLX",
    )
    _db.add_record(conn, d1, "letters", "letter", json.dumps({
        "kind": "letter", "text": "Carta de Malaca", "from": "Xavier",
        "place": "Malaca", "page_start": 69,
    }))
    _db.add_record(conn, d1, "letters", "letter", json.dumps({
        "kind": "letter", "text": "Carta de Goa", "from": "Xavier",
        "place": "Goa", "page_start": 20,
    }))
    _db.add_record(conn, d2, "person", "person", json.dumps({
        "kind": "person", "text": "Francisco Xavier", "name": "Francisco Xavier",
        "page_start": 3,
    }))
    conn.commit()
    conn.close()
    return d1, d2


# ------------------------------------------------------------------ listing


def test_records_lists_all_json_with_total_and_stable_order(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    d1, _d2 = _seed(cfg)

    cli.cmd_records(cfg, _args(json=True))
    out = json.loads(capsys.readouterr().out)

    assert out["total"] == 3
    assert out["limit"] == 20 and out["offset"] == 0
    assert len(out["records"]) == 3
    # order is document, start page, record id — deterministic pagination
    assert [r["id"] for r in out["records"]] == [2, 1, 3]
    assert [r["page_no"] for r in out["records"]] == [20, 69, 3]
    first = out["records"][0]
    assert first["document_id"] == d1
    assert first["filename"] == "letters.pdf"
    assert first["collection"] == "collections/COLX"
    assert first["encoder"] == "letters"
    assert first["kind"] == "letter"
    assert first["data"]["place"] == "Goa"
    assert first["path"].endswith("letters.pdf")


def test_records_filters_combine(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    d1, _d2 = _seed(cfg)

    cases = [
        (_args(doc=d1, json=True), 2),
        (_args(encoder="person", json=True), 1),
        (_args(record_kind="letter", json=True), 2),
        (_args(collection="COLX", json=True), 3),
        (_args(field_filters=["from=Xavier"], json=True), 2),
        (_args(doc=d1, encoder="letters", record_kind="letter",
               field_filters=["place=Goa"], json=True), 1),
    ]
    for args, expected in cases:
        cli.cmd_records(cfg, args)
        out = json.loads(capsys.readouterr().out)
        assert out["total"] == expected, args


def test_records_ignore_case_only_when_asked(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _seed(cfg)

    cli.cmd_records(cfg, _args(field_filters=["place=malaca"], json=True))
    assert json.loads(capsys.readouterr().out)["total"] == 0

    cli.cmd_records(cfg, _args(field_filters=["place=malaca"], ignore_case=True,
                               json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1
    assert out["records"][0]["data"]["place"] == "Malaca"


def test_records_limit_and_offset_page_through_the_total(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _seed(cfg)

    cli.cmd_records(cfg, _args(limit=1, offset=2, json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 3
    assert [r["id"] for r in out["records"]] == [3]

    cli.cmd_records(cfg, _args(limit=1, offset=99, json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 3 and out["records"] == []

    cli.cmd_records(cfg, _args(limit=1, offset=99))
    assert "no records at offset 99 (total 3)" in capsys.readouterr().out


def test_records_text_output_names_the_records(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _seed(cfg)

    cli.cmd_records(cfg, _args())
    out = capsys.readouterr().out
    assert "records: 3   (showing 1-3)" in out
    assert "letters.pdf" in out
    assert "p.20" in out and "p.69" in out
    assert "Carta de Goa" in out
    assert "[letters/letter]" in out and "[person/person]" in out


def test_records_no_matches_vs_empty_archive(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)

    cli.cmd_records(cfg, _args())
    assert "no encoder records yet" in capsys.readouterr().out

    _seed(cfg)
    cli.cmd_records(cfg, _args(encoder="missing-encoder"))
    assert "no records match the filters" in capsys.readouterr().out


def test_records_rejects_unknown_document(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_records(cfg, _args(doc=999))
    assert exc.value.code == 2
    assert "no document #999" in capsys.readouterr().err


def test_records_rejects_bad_field_specs(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _seed(cfg)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_records(cfg, _args(field_filters=["place"]))
    assert exc.value.code == 2
    assert "expects NAME=VALUE" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        cli.cmd_records(cfg, _args(field_filters=["place; DROP TABLE records=Malaca"]))
    assert exc.value.code == 2
    assert "invalid record field name" in capsys.readouterr().err


def test_records_degrades_on_db_without_records_table(tmp_path, capsys):
    """An archive whose DB predates encoder records (no `records` table) must
    list zero records, not crash: the read-only connection never runs the DDL
    that would add the table."""
    cfg = _make_cfg(tmp_path)
    conn = _db.connect(cfg.db_path)
    conn.execute("DROP TABLE record_embeddings")
    conn.execute("DROP TABLE records_fts")
    conn.execute("DROP TABLE records")
    conn.commit()
    conn.close()

    cli.cmd_records(cfg, _args(json=True))
    out = json.loads(capsys.readouterr().out)
    assert out == {"total": 0, "limit": 20, "offset": 0, "records": []}

    cli.cmd_records(cfg, _args())
    assert "no encoder records yet" in capsys.readouterr().out

    # A bad field name is still a usage error on an old DB: validation runs
    # before the table guard.
    with pytest.raises(SystemExit) as exc:
        cli.cmd_records(cfg, _args(field_filters=["bad name=x"]))
    assert exc.value.code == 2
    assert "invalid record field name" in capsys.readouterr().err

    # The same guard holds for the DB helper used by the command.
    ro = _db.connect(cfg.db_path, readonly=True)
    try:
        assert _db.record_listing(ro)["total"] == 0
        assert _db.count_records(ro) == 0
        assert _db.page_records(ro, 1, 1) == []
        assert _db.record_encoders_for_document(ro, 1) == []
    finally:
        ro.close()


def test_record_listing_page_filter_and_public_projection(tmp_path):
    """The page filter selects records that START there, and `record_public`
    is the single projection shared by CLI/serve/view."""
    cfg = _make_cfg(tmp_path)
    d1, d2 = _seed(cfg)
    conn = _db.connect(cfg.db_path, readonly=True)
    try:
        listing = _db.record_listing(conn, page=69)
        assert listing["total"] == 1
        public = _db.record_public(listing["records"][0])
        assert public["page_no"] == 69
        assert public["encoder"] == "letters"
        assert public["data"]["place"] == "Malaca"
        assert _db.record_listing(conn, page=999)["total"] == 0
        assert _db.page_records(conn, d1, 20)[0]["data"]["place"] == "Goa"
        assert _db.page_records(conn, d1, 999) == []
        assert _db.record_encoders_for_document(conn, d1) == ["letters"]
        assert _db.record_encoders_for_document(conn, d2) == ["person"]
    finally:
        conn.close()


# ------------------------------------------------------------------ status


def test_status_reports_record_totals_and_per_document_counts(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _seed(cfg)

    cli.cmd_status(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["records"]["total"] == 3
    assert data["records"]["documents"] == 2
    assert data["records"]["embedded"] == 0
    assert data["records"]["by_encoder"] == {"letters": 2, "person": 1}

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "records:   3 in 2/2 document(s)" in out
    assert "2 records" in out     # letters.pdf
    assert "1 record" in out      # people.pdf


def test_summary_exposes_records_for_mcp(tmp_path):
    """`pha_extraction_status` returns db.summary() verbatim, so the totals
    must be there once encoder output exists."""
    cfg = _make_cfg(tmp_path)
    _seed(cfg)
    conn = _db.connect(cfg.db_path, readonly=True)
    try:
        summary = _db.summary(conn)
    finally:
        conn.close()
    assert summary["records"]["total"] == 3
    assert summary["records"]["by_encoder"] == {"letters": 2, "person": 1}

"""The notes index: the archive's memory, searchable beside its sources.

Notes are not documents. These tests pin the two properties that make the
feature safe as well as useful: a note is found by a query (keyword, semantic
and hybrid), and a note hit is *marked* as a conclusion, never fused into a
page or record — no document id, no page number, its own `kind`.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from personal_historical_archive import cli
from personal_historical_archive import db
from personal_historical_archive import notes as notes_memory
from personal_historical_archive.config import Config
from personal_historical_archive.search import search


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


def _write_note(cfg, name: str, body: str) -> None:
    (cfg.notes / f"{name}.md").write_text(body, encoding="utf-8")


def _seed_page(cfg, text="Zumbae in Missione insulae Sumba"):
    """One document + one page chunk, so pages and notes can be told apart."""
    conn = db.connect(cfg.db_path)
    doc = db.add_document(
        conn, filename="schutte.pdf", path=str(cfg.dropbox / "schutte.pdf"),
        sha256="a", size_bytes=1, mtime=1, kind="pdf",
        dir_path="collections/SCHUTTE", now=time.time(),
    )
    page_id = db.add_page(conn, doc, 1089)
    db.set_page_result(conn, page_id, raw_text=text)
    db.add_chunk(conn, doc, page_id, 0, text, None, "raw")
    conn.commit()
    conn.close()


def _index(cfg, embed_client=None, force=False):
    conn = db.connect(cfg.db_path)
    try:
        return notes_memory.index_notes(cfg, conn, embed_client=embed_client, force=force)
    finally:
        conn.close()


class _FakeEmbed:
    """Stands in for the embed server: a deterministic vector per text."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def embed(self, model, texts, batch_size=None):
        from personal_historical_archive.model_client import ModelError

        self.calls.append(list(texts))
        if self.fail:
            raise ModelError("model not loaded")
        return [[float((hash(t) % 97) + 1), 1.0, 0.5] for t in texts]


def _cfg_ns(cfg):
    return SimpleNamespace(
        default_mode="keyword", top_k=10, embed_model=cfg.embed_model,
        embed_base_url=cfg.embed_base_url, embed_timeout_s=1,
    )


# --------------------------------------------------------------- indexing

def test_index_creates_rows_and_excludes_the_readme(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "zumbae", "---\ntitle: \"Zumbae = Sumba\"\n---\n\n# Heading\n\nBody text.\n")
    _write_note(cfg, "plain", "# A plain heading\n\nAnother body.\n")
    _write_note(cfg, "_draft", "template, not a note\n")
    assert (cfg.notes / "README.md").exists(), "ensure_dirs seeds the notes README"

    res = _index(cfg)
    assert res["changed"] == 2 and res["removed"] == 0

    conn = db.connect(cfg.db_path)
    try:
        rows = {r["name"]: r for r in db.all_notes(conn)}
        assert set(rows) == {"zumbae", "plain"}
        assert rows["zumbae"]["title"] == "Zumbae = Sumba"
        assert rows["plain"]["title"] == "A plain heading"
        assert rows["zumbae"]["sha256"]
        fts = conn.execute("SELECT COUNT(*) n FROM notes_fts").fetchone()["n"]
        assert fts == 2
    finally:
        conn.close()


def test_indexing_is_incremental_and_tracks_edits_and_deletions(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "one", "# One\n\nfirst\n")
    _write_note(cfg, "two", "# Two\n\nsecond\n")
    assert _index(cfg)["changed"] == 2

    # Nothing changed: a second pass writes nothing at all.
    again = _index(cfg)
    assert again["changed"] == 0 and again["unchanged"] == 2

    # A touched mtime with identical content is not a change (backups, syncs).
    (cfg.notes / "one.md").touch()
    assert _index(cfg)["changed"] == 0

    # Edited content is re-indexed, and the OLD wording stops matching.
    _write_note(cfg, "one", "# One\n\nrewritten about Timor\n")
    assert _index(cfg)["changed"] == 1
    conn = db.connect(cfg.db_path)
    try:
        assert db.notes_keyword_search(conn, "first") == []
        assert len(db.notes_keyword_search(conn, "Timor")) == 1
    finally:
        conn.close()

    # A deleted note disappears from the index.
    (cfg.notes / "two.md").unlink()
    assert _index(cfg)["removed"] == 1
    conn = db.connect(cfg.db_path)
    try:
        assert [r["name"] for r in db.all_notes(conn)] == ["one"]
    finally:
        conn.close()


def test_unreadable_note_is_skipped_not_fatal(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "good", "# Good\n\nbody\n")
    bad = cfg.notes / "bad.md"
    bad.write_text("# Bad\n\nbody\n")
    bad.chmod(0o000)
    try:
        res = _index(cfg)
    finally:
        bad.chmod(0o644)
    assert res["changed"] == 1, "one unreadable file must not fail the pass"


def test_embed_failure_indexes_text_only_then_fills_vectors(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "one", "# One\n\nabout Timor\n")
    failing = _FakeEmbed(fail=True)
    res = _index(cfg, embed_client=failing)
    assert res["changed"] == 1 and res["embedded"] == 0
    assert "model not loaded" in (res["error"] or "")
    conn = db.connect(cfg.db_path)
    try:
        row = db.all_notes(conn)[0]
        assert row["text"] and row["embedding"] is None
    finally:
        conn.close()

    # A later pass with a working embedder fills the missing vector in place.
    good = _FakeEmbed()
    res = _index(cfg, embed_client=good)
    assert res["changed"] == 0 and res["embedded"] == 1
    conn = db.connect(cfg.db_path)
    try:
        assert db.all_notes(conn)[0]["embedding"] is not None
    finally:
        conn.close()


def test_ensure_indexed_probes_read_only_then_indexes(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert notes_memory.ensure_indexed(cfg) is None, "nothing to do on an empty notes/"

    _write_note(cfg, "new", "# New\n\nsomething\n")
    res = notes_memory.ensure_indexed(cfg)
    assert res and res["changed"] == 1
    assert notes_memory.ensure_indexed(cfg) is None, "a current index writes nothing"


# --------------------------------------------------------------- search

def test_source_scoping_keeps_pages_and_notes_apart(tmp_path):
    cfg = _make_cfg(tmp_path)
    _seed_page(cfg)
    _write_note(cfg, "zumbae", "# Zumbae\n\nZumbae is Sumba, per the catalogue.\n")
    _index(cfg)

    conn = db.connect(cfg.db_path)
    try:
        notes_only = search(conn, None, _cfg_ns(cfg), "Zumbae", mode="keyword",
                            source="notes")
        assert [r["kind"] for r in notes_only["results"]] == ["note"]

        pages_only = search(conn, None, _cfg_ns(cfg), "Zumbae", mode="keyword",
                            source="pages")
        assert pages_only["results"], "the page matches too"
        assert all(r["kind"] == "page" for r in pages_only["results"])

        both = search(conn, None, _cfg_ns(cfg), "Zumbae", mode="keyword", source="all")
        kinds = [r["kind"] for r in both["results"]]
        assert "note" in kinds and "page" in kinds
    finally:
        conn.close()


def test_note_hit_shape_is_marked_as_a_conclusion(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "zumbae", "---\ntitle: Zumbae = Sumba\n---\n\nBody about Zumbae.\n")
    _index(cfg)
    conn = db.connect(cfg.db_path)
    try:
        hit = search(conn, None, _cfg_ns(cfg), "Zumbae", mode="keyword",
                     source="notes")["results"][0]
        assert hit["kind"] == "note"
        assert hit["note"] == "zumbae"
        assert hit["title"] == "Zumbae = Sumba"
        assert hit["path"].endswith("zumbae.md")
        assert "Zumbae" in hit["text"]
        assert hit["text_truncated"] is False
        assert hit["snippet"]
        # It is not a page and must not pretend to be one.
        assert "document_id" not in hit and "page_no" not in hit
    finally:
        conn.close()


def test_fusion_keeps_a_note_and_a_chunk_with_the_same_number(tmp_path):
    """A note id of 1 must not swallow chunk 1: fusion keys on (kind, id)."""
    cfg = _make_cfg(tmp_path)
    _seed_page(cfg)
    _write_note(cfg, "zumbae", "# Zumbae\n\nZumbae is Sumba.\n")
    _index(cfg)

    conn = db.connect(cfg.db_path)
    try:
        assert conn.execute("SELECT MIN(id) FROM notes").fetchone()[0] == 1
        assert conn.execute("SELECT MIN(id) FROM chunks").fetchone()[0] == 1
        hits = search(conn, None, _cfg_ns(cfg), "Zumbae", mode="keyword",
                      source="all")["results"]
        keys = {(h["kind"], h.get("note_id") or h.get("chunk_id")) for h in hits}
        assert ("note", 1) in keys and ("page", 1) in keys
        assert len(hits) == 2
    finally:
        conn.close()


def test_semantic_note_search_uses_the_shared_embedding_model(tmp_path):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "zumbae", "# Zumbae\n\nZumbae is Sumba.\n")
    client = _FakeEmbed()
    _index(cfg, embed_client=client)
    conn = db.connect(cfg.db_path)
    try:
        res = search(conn, client, _cfg_ns(cfg), "Sumba", mode="semantic",
                     source="notes")
        assert [r["kind"] for r in res["results"]] == ["note"]
        assert res["results"][0]["source"] == "semantic"
    finally:
        conn.close()


def test_notes_source_rejects_record_field_filters(tmp_path):
    cfg = _make_cfg(tmp_path)
    conn = db.connect(cfg.db_path)
    try:
        try:
            search(conn, None, _cfg_ns(cfg), "x", source="notes",
                   fields={"from": "Xavier"})
        except ValueError as e:
            assert "--field" in str(e)
        else:
            raise AssertionError("--field with --source notes must be refused")
    finally:
        conn.close()


# --------------------------------------------------------------- surfaces

def _search_args(**kw):
    base = dict(query="Zumbae", mode="keyword", limit=10, collection=None,
                source="notes", encoder=None, record_kind=None,
                field_filters=None, ignore_case=False, force=False,
                verbose=False, json=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cli_search_prints_and_json_encodes_note_hits(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "zumbae", "---\ntitle: Zumbae = Sumba\n---\n\nBody.\n")

    cli.cmd_search(cfg, _search_args())
    data = json.loads(capsys.readouterr().out)
    assert data["results"][0]["kind"] == "note"
    assert data["results"][0]["note_file"].endswith("zumbae.md")
    assert data["results"][0]["page_file"] is None

    cli.cmd_search(cfg, _search_args(json=False))
    out = capsys.readouterr().out
    assert "[note] zumbae" in out
    assert "Zumbae = Sumba" in out
    assert "full note:" in out


def test_cli_notes_list_index_and_show(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "zumbae", "---\ntitle: Zumbae = Sumba\n---\n\nBody text.\n")

    cli.cmd_notes(cfg, SimpleNamespace(notes_cmd="index", force=False, json=False))
    assert "1 indexed" in capsys.readouterr().out

    cli.cmd_notes(cfg, SimpleNamespace(notes_cmd=None, json=False))
    out = capsys.readouterr().out
    assert "zumbae" in out and "Zumbae = Sumba" in out

    cli.cmd_notes(cfg, SimpleNamespace(notes_cmd="show", name="zumbae"))
    assert "Body text." in capsys.readouterr().out


def test_status_reports_the_notes_index(tmp_path, capsys):
    cfg = _make_cfg(tmp_path)
    _write_note(cfg, "zumbae", "# Zumbae\n\nbody\n")
    _index(cfg)
    cli.cmd_status(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["notes"]["files"] == 1
    assert data["notes"]["indexed"] == 1
    assert data["notes"]["stale"] == 0

from __future__ import annotations

"""The per-page EDIT override: `pha edit --path <doc> --page N --editor X --model Y`.

The edit-stage twin of `pha scan --page N --palaeographer X --model Y`: the
override is authoritative for that page, is recorded per page
(`page_edits.editor_model` + `pinned_at`), does NOT reconfigure the document,
keeps the page inside the document's existing variant folder (with its own pair
in the front matter) and is pinned so a later bulk `pha edit` keeps it.
"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli, db as _db, ingest
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import sha256_of

PAGES = 3


# --------------------------------------------------------------------------- fixtures

def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _write_rules(cfg: Config, ed_id: str, body: str = "modernise") -> None:
    cfg.editors_dir.mkdir(parents=True, exist_ok=True)
    (cfg.editors_dir / f"{ed_id}.md").write_text(
        f"---\ntemperature: 0.0\n---\n{body}\n")


def _write_model(cfg: Config, m_id: str, *, server: str = "srv-a",
                 model: str | None = None) -> None:
    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    (cfg.models_dir / f"{m_id}.md").write_text(
        f"---\ndescription: {m_id}\nserver: {server}\n"
        f"base_url: http://127.0.0.1:9/v1\nmodel: {model or m_id}\n---\n")


def _seed(cfg: Config, *, pages: int = PAGES, doc_editor: str = "A",
          doc_model: str = "mA"):
    """An ingested document whose pages are already edited by `doc_editor`."""
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    src = col / "d.pdf"
    src.write_bytes(b"%PDF-1.4 d")
    conn = _db.connect(cfg.db_path)
    doc = _db.add_document(conn, filename="d.pdf", path=str(src), sha256=sha256_of(src),
                           size_bytes=src.stat().st_size, mtime=src.stat().st_mtime,
                           kind="pdf", dir_path="collections/COLX", now=time.time(),
                           palaeographer="default", palaeographer_model="default",
                           editor=doc_editor, editor_model=doc_model)
    for n in range(1, pages + 1):
        pid = _db.add_page(conn, doc, n)
        _db.set_page_result(conn, pid, raw_text=f"machine reading {n}")
        _db.set_page_edit(conn, pid, doc_editor, text=f"{doc_editor} text {n}",
                          raw_sha="x", editor_model=doc_model)
    _db.set_document_status(conn, doc, "done")
    conn.commit()
    row = _db.get_document(conn, doc)
    conn.close()
    return src, row


class FakeClient:
    """Offline stand-in for ModelClient: the text names the SERVER MODEL used."""

    seen: list = []

    def __init__(self, *a, **kw):
        pass

    def chat_text(self, model, prompt, temperature=0.1, max_tokens=4096, thinking=True):
        FakeClient.seen.append(model)
        return f"[{model}] edited"

    def chat_vision(self, *a, **kw):  # pragma: no cover - not used here
        return "x"

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network: stub the model client and the embedder."""
    FakeClient.seen = []
    monkeypatch.setattr(ingest, "ModelClient", FakeClient)
    monkeypatch.setattr(ingest, "index_document",
                        lambda cfg, conn, doc_id, verbose=True, pages=None: 1)


def _edit(cfg: Config, path: Path, **kw) -> dict:
    """`pha edit --path <doc>` through the real entry point."""
    return ingest.edit_documents_under(cfg, str(path), verbose=False, **kw)


def _row(cfg: Config, doc_id: int, page_no: int, editor: str):
    conn = _db.connect(cfg.db_path)
    try:
        row = conn.execute(
            "SELECT pe.* FROM page_edits pe JOIN pages p ON p.id = pe.page_id "
            "WHERE p.document_id = ? AND p.page_no = ? AND pe.editor = ?",
            (doc_id, page_no, editor)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _served(cfg: Config, doc: dict, page_no: int) -> str:
    """The edited text the archive serves for a page (the `pha page` rule)."""
    conn = _db.connect(cfg.db_path)
    try:
        pid = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=?",
                           (doc["id"], page_no)).fetchone()["id"]
        row = _db.effective_edit_for_page(conn, pid, doc["editor"])
        return (row["text"] if row is not None else None) or ""
    finally:
        conn.close()


def _library_page(cfg: Config, doc: dict, page_no: int, editor: str, model: str) -> Path:
    """The file the archive actually wrote for one page."""
    conn = _db.connect(cfg.db_path)
    try:
        slug = ingest._doc_slug(doc)
    finally:
        conn.close()
    rel = Path(doc["dir_path"] or "")
    folder = cfg.library / rel / slug / f"edited-{editor}@{model}"
    return folder / f"page-{page_no:03d}.md"


# --------------------------------------------------------------------------- the override

def test_override_re_edits_only_the_named_page(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    (cfg.dropbox / "collections" / "COLX").mkdir(parents=True, exist_ok=True)
    (cfg.dropbox / "collections" / "COLX" / "pha.yaml").write_text(
        "editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    res = _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")
    assert res["results"][0]["action"] == "edited"
    assert FakeClient.seen == ["model-x"]        # only page 2 talked to the model

    # pages 1 and 3 keep A's text; page 2 is X's
    assert _row(cfg, doc["id"], 1, "A")["text"] == "A text 1"
    assert _row(cfg, doc["id"], 3, "A")["text"] == "A text 3"
    x = _row(cfg, doc["id"], 2, "X")
    assert x["text"] == "[model-x] edited"
    assert x["editor_model"] == "mX"
    assert x["pinned_at"]                        # protected by default

    # the DOCUMENT is not reconfigured by a per-page override
    conn = _db.connect(cfg.db_path)
    try:
        d = _db.get_document(conn, doc["id"])
        assert (d["editor"], d["editor_model"]) == ("A", "mA")
    finally:
        conn.close()


def test_several_pages_can_be_overridden_in_one_run(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    res = _edit(cfg, src, pages={1, 3}, editor_override="X", model_override="mX")
    assert res["results"][0]["pages"] == 2
    assert FakeClient.seen == ["model-x", "model-x"]
    assert _row(cfg, doc["id"], 1, "X")["text"] == "[model-x] edited"
    assert _row(cfg, doc["id"], 3, "X")["text"] == "[model-x] edited"
    assert _row(cfg, doc["id"], 2, "X") is None          # page 2 untouched
    assert _row(cfg, doc["id"], 2, "A")["text"] == "A text 2"

    # and a bulk pass keeps both
    res = _edit(cfg, src, reprocess=True)
    assert res["results"][0]["kept_pinned"] == [1, 3]


def test_the_override_keeps_the_documents_folder_with_page_front_matter(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")

    # the variant folder is still the document's edit, NOT edited-X@mX
    f = _library_page(cfg, doc, 2, "A", "mA")
    assert f.exists(), f
    text = f.read_text(encoding="utf-8")
    assert "[model-x] edited" in text
    assert "editor: X" in text and "model: mX" in text and "pinned: true" in text
    # an untouched page keeps the plain front matter
    other = _library_page(cfg, doc, 1, "A", "mA").read_text(encoding="utf-8")
    assert "A text 1" in other and "editor: A" in other


def test_a_bulk_pass_keeps_the_pinned_override(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")
    res = _edit(cfg, src, reprocess=True)          # a whole-document re-edit

    assert res["results"][0]["kept_pinned"] == [2]
    assert _row(cfg, doc["id"], 2, "X")["text"] == "[model-x] edited"   # kept
    assert _row(cfg, doc["id"], 1, "A")["text"] == "[model-a] edited"   # re-edited


def test_unpin_releases_the_override_and_the_configured_editor_wins(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")
    res = _edit(cfg, src, unpin=True)
    assert res["results"][0]["unpinned"] == 1
    assert _row(cfg, doc["id"], 2, "X")["text"] == "[model-x] edited"   # text kept
    assert _row(cfg, doc["id"], 2, "X")["pinned_at"] is None

    # with the pin gone, a bulk pass re-edits the page with the document's editor
    res = _edit(cfg, src, reprocess=True)
    assert res["results"][0]["kept_pinned"] == []
    assert _row(cfg, doc["id"], 2, "A")["text"] == "[model-a] edited"


def test_no_pin_records_provenance_but_a_bulk_pass_re_edits(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX", pin=False)
    x = _row(cfg, doc["id"], 2, "X")
    assert x["editor_model"] == "mX" and x["pinned_at"] is None

    res = _edit(cfg, src, reprocess=True)
    assert res["results"][0]["kept_pinned"] == []


# --------------------------------------------------------------------------- halves

def test_editor_alone_keeps_the_documents_model(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    _edit(cfg, src, page_no=2, editor_override="X")
    assert FakeClient.seen == ["model-a"]        # the document's model
    x = _row(cfg, doc["id"], 2, "X")
    assert x["editor_model"] == "mA"


def test_model_alone_keeps_the_documents_editor(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mZ", server="srv-b", model="model-z")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    _edit(cfg, src, page_no=2, model_override="mZ")
    assert FakeClient.seen == ["model-z"]
    a = _row(cfg, doc["id"], 2, "A")
    assert a["editor_model"] == "mZ" and a["pinned_at"]


# --------------------------------------------------------------------------- CLI

def _cli_args(src, **kw):
    base = dict(path=str(src), page=2, editor="X", model="mX", dry_run=False,
                unpin=False, no_pin=False, reprocess=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_edit_applies_the_override_and_reports_the_pair(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    cli.cmd_edit(cfg, _cli_args(src))
    out = capsys.readouterr().out
    assert "↻ page 2: A@mA → X@mX" in out
    assert "pinned" in out
    assert _row(cfg, doc["id"], 2, "X")["text"] == "[model-x] edited"


def test_cmd_edit_dry_run_plans_without_writing(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    cli.cmd_edit(cfg, _cli_args(src, dry_run=True))
    out = capsys.readouterr().out
    assert "plan for d.pdf" in out
    assert "re-edit (was A@mA" in out and "→ X@mX" in out
    assert FakeClient.seen == []
    assert _row(cfg, doc["id"], 2, "X") is None


def test_cmd_edit_unpin_reports(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, _doc = _seed(cfg)
    cli.cmd_edit(cfg, _cli_args(src))
    capsys.readouterr()

    cli.cmd_edit(cfg, _cli_args(src, editor=None, model=None, page=None, unpin=True))
    out = capsys.readouterr().out
    assert "unpinned 1 page edit(s)" in out


def test_the_cli_offers_the_page_override_flags(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["edit", "--help"])
    assert e.value.code == 0
    help_text = capsys.readouterr().out
    for flag in ("--editor", "--model", "--dry-run", "--no-pin", "--unpin"):
        assert flag in help_text


# --------------------------------------------------------------------------- locks

def test_the_override_model_server_is_the_one_locked(tmp_path):
    """A per-page override talks to a server the document's config does not name,
    so the job must take ITS lock — otherwise two models could load at once."""
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", server="srv-a", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, _doc = _seed(cfg)

    from personal_historical_archive import locks

    own = locks.stage_key(cfg.resolve_model(cfg.get_editor("A"), "mA"))
    other = locks.stage_key(cfg.resolve_model(cfg.get_editor("X"), "mX"))
    assert own != other

    keys = ingest._job_keys(cfg, [src], include_pal=False,
                            ed_override="X", ed_model_override="mX")
    assert other in keys                  # the override's server is locked
    assert own not in keys                # the run never talks to the document's one


# --------------------------------------------------------------------------- guards

def test_the_override_needs_one_page_and_one_document(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_model(cfg, "mA", model="model-a")
    src, _doc = _seed(cfg)

    res = _edit(cfg, src, editor_override="A")           # no --page
    assert res["results"][0]["action"] == "error"
    assert "--page" in res["results"][0]["reason"]
    capsys.readouterr()


def test_the_override_refuses_two_documents(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_model(cfg, "mA", model="model-a")
    src, _doc = _seed(cfg)
    # a second document in the same collection
    other = src.parent / "e.pdf"
    other.write_bytes(b"%PDF-1.4 e")
    conn = _db.connect(cfg.db_path)
    d2 = _db.add_document(conn, filename="e.pdf", path=str(other), sha256=sha256_of(other),
                          size_bytes=1, mtime=1, kind="pdf", dir_path="collections/COLX",
                          now=time.time(), palaeographer="default",
                          palaeographer_model="default", editor="A", editor_model="mA")
    _db.set_page_result(conn, _db.add_page(conn, d2, 1), raw_text="e1")
    _db.set_document_status(conn, d2, "done")
    conn.commit()
    conn.close()

    res = _edit(cfg, src.parent, page_no=1, editor_override="A", model_override="mA")
    assert res["results"][0]["action"] == "error"
    assert "exactly one document" in res["results"][0]["reason"]


def test_a_human_reviewed_page_is_refused_and_kept(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)
    conn = _db.connect(cfg.db_path)
    try:
        pid = conn.execute("SELECT id FROM pages WHERE document_id=? AND page_no=2",
                           (doc["id"],)).fetchone()["id"]
        _db.mark_edit_reviewed(conn, pid, "A", "HUMAN TEXT")
        conn.commit()
    finally:
        conn.close()

    res = _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")
    assert res["results"][0]["refused_reviewed"] == [2]
    assert FakeClient.seen == []                        # no model call
    assert _row(cfg, doc["id"], 2, "A")["text"] == "HUMAN TEXT"


def test_dry_run_writes_nothing(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)

    res = _edit(cfg, src, page_no=2, editor_override="X", model_override="mX",
                dry_run=True)
    assert res["results"][0]["action"] == "planned"
    plan = res["results"][0]["plan"][0]
    assert plan["page"] == 2 and plan["action"] == "re-edit"
    assert plan["from"] == {"editor": "A", "model": "mA"}
    assert plan["to"] == {"editor": "X", "model": "mX"}
    # nothing was written and no model was called
    assert FakeClient.seen == []
    assert _row(cfg, doc["id"], 2, "X") is None
    assert _row(cfg, doc["id"], 2, "A")["text"] == "A text 2"
    capsys.readouterr()


# --------------------------------------------------------------------------- serving

def test_page_and_cite_serve_the_page_scoped_pair(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)
    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")
    capsys.readouterr()

    cli.cmd_page(cfg, SimpleNamespace(doc="d.pdf", page=2, edited=True, editor=None,
                                      json=True))
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "[model-x] edited"
    assert payload["page_editor"] == "X"
    assert payload["page_editor_model"] == "mX"
    assert payload["edit_pinned"] is True

    cli.cmd_cite(cfg, SimpleNamespace(doc="d.pdf", page=2, edited=True, editor=None,
                                      palaeographer=None, json=True))
    cite = json.loads(capsys.readouterr().out)
    assert cite["label"] == "edited: X@mX"           # not the folder's A@mA
    assert cite["page_editor"] == "X"

    # an untouched page still reports the document's pair
    cli.cmd_page(cfg, SimpleNamespace(doc="d.pdf", page=1, edited=True, editor=None,
                                      json=True))
    plain = json.loads(capsys.readouterr().out)
    assert plain["text"] == "A text 1"
    assert plain["page_editor"] == "A" and plain["edit_pinned"] is False


def test_mcp_reports_the_served_edit_and_its_provenance(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)
    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")

    import asyncio

    from personal_historical_archive import mcp_server

    fns = {t.name: t.fn for t in asyncio.run(mcp_server.make_server(cfg).list_tools())}
    page = fns["pha_get_page"](doc["id"], 2)
    # every editor's text is listed, and the served one is named explicitly
    assert page["edited"]["X"] == "[model-x] edited"
    assert page["edited_served"]["editor"] == "X"
    assert page["edited_served"]["model"] == "mX"
    assert page["edited_served"]["pinned"] is True
    assert page["edited_served"]["text"] == "[model-x] edited"
    # an untouched page serves the document's editor
    plain = fns["pha_get_page"](doc["id"], 1)
    assert plain["edited_served"]["editor"] == "A"
    assert plain["edited_served"]["pinned"] is False


def test_status_reports_the_pinned_edits(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)
    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")

    cli.cmd_status(cfg, SimpleNamespace())
    out = capsys.readouterr().out
    assert "pinned page edit(s)" in out
    assert f"#{doc['id']} (1)" in out


# --------------------------------------------------------------------------- review round-trip

def test_a_human_correction_of_a_pinned_page_is_imported_and_served(tmp_path):
    cfg = _cfg(tmp_path)
    _write_rules(cfg, "A")
    _write_rules(cfg, "X", "translate")
    _write_model(cfg, "mA", model="model-a")
    _write_model(cfg, "mX", server="srv-b", model="model-x")
    col = cfg.dropbox / "collections" / "COLX"
    col.mkdir(parents=True, exist_ok=True)
    (col / "pha.yaml").write_text("editor: {rules: A, model: mA}\n")
    cfg = Config.load(cfg.root)
    src, doc = _seed(cfg)
    _edit(cfg, src, page_no=2, editor_override="X", model_override="mX")

    # the historian corrects the SERVED file (inside the document's folder)
    f = _library_page(cfg, doc, 2, "A", "mA")
    text = f.read_text(encoding="utf-8")
    f.write_text(text.replace("[model-x] edited", "HUMAN CORRECTION"), encoding="utf-8")
    time.sleep(0.01)

    conn = _db.connect(cfg.db_path)
    try:
        assert [r["page_no"] for r in ingest.pending_review_files(cfg, conn)] == [2]
        res = ingest.review_import(cfg, conn, verbose=False)
        assert res["edits"] == 1
        x = _db.get_page_edit(conn, conn.execute(
            "SELECT id FROM pages WHERE document_id=? AND page_no=2",
            (doc["id"],)).fetchone()["id"], "X")
        assert x["text"] == "HUMAN CORRECTION" and x["reviewed_at"]      # on X's row
        # the human's reading is what is served (reviewed outranks the pin)
        assert _served(cfg, _db.get_document(conn, doc["id"]), 2) == "HUMAN CORRECTION"
    finally:
        conn.close()

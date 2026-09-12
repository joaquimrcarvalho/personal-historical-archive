"""Stage integration: the filter hooks actually run inside scan/edit/encode.

These drive the real pipeline functions with the model call stubbed, so they
cover the wiring (sidecar -> hook -> stored text/records) rather than the
filter framework itself (see test_filters.py).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import db as _db
from personal_historical_archive.config import Config
from personal_historical_archive.ingest import edit_document, encode_document


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def _add_filter(cfg: Config, name: str, code: str, manifest: str = "") -> None:
    """Write a filter into the archive's filters/ directory."""
    d = cfg.filters_dir / name
    d.mkdir(parents=True, exist_ok=True)
    if manifest:
        (d / "filter.md").write_text(manifest, encoding="utf-8")
    (d / "filter.py").write_text(code, encoding="utf-8")


def _write_sidecar(cfg: Config, body: str) -> None:
    (cfg.dropbox / "collections" / "COLX").mkdir(parents=True, exist_ok=True)
    (cfg.dropbox / "collections" / "COLX" / "pha.yaml").write_text(body, encoding="utf-8")


def _doc_with_page(cfg: Config, raw: str = "RAW   TEXT"):
    src = cfg.dropbox / "collections" / "COLX" / "d.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF fake")
    conn = _db.connect(cfg.db_path)
    doc_id = _db.add_document(conn, filename="d.pdf", path=str(src), sha256="a",
                              size_bytes=1, mtime=1, kind="pdf", now=time.time(),
                              dir_path="collections/COLX")
    pid = _db.add_page(conn, doc_id, 1)
    _db.set_page_result(conn, pid, raw_text=raw)
    _db.update_document(conn, doc_id, page_count=1)
    conn.commit()
    return conn, doc_id, pid


class _FakeTextClient:
    """Stands in for the editor/encoder model; records the prompts it saw."""

    def __init__(self, *a, **k):
        self.prompts: list[str] = []
        self.reply = "MODEL OUTPUT"

    def close(self):
        pass

    def chat_text(self, model, prompt, temperature, max_tokens, thinking):
        self.prompts.append(prompt)
        return self.reply


# --------------------------------------------------------------------- editor hooks

def test_editor_pre_and_post_filters_run(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    _add_filter(cfg, "tag-pre", "def run(value, ctx):\n    return value + ' [PRE]'\n")
    _add_filter(cfg, "tag-post", "def run(value, ctx):\n    return value + ' [POST]'\n")
    _write_sidecar(cfg, """
editor:
  rules: default
  model: default
  pre: [tag-pre]
  post: [tag-post]
""")
    conn, doc_id, pid = _doc_with_page(cfg)
    fake = _FakeTextClient()
    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", lambda *a, **k: fake)

    res = edit_document(cfg, conn, doc_id, verbose=False)
    assert res["pages"] == 1
    # the model saw the PRE-filtered transcription ...
    assert "[PRE]" in fake.prompts[0]
    # ... and the stored edit carries the POST filter
    row = conn.execute("SELECT text FROM page_edits WHERE page_id=? AND editor='default'",
                       (pid,)).fetchone()
    assert row["text"] == "MODEL OUTPUT [POST]"
    conn.close()


def test_editor_prompt_changed_by_pre_filter(monkeypatch, tmp_path):
    """The pre filter's output is what the model is asked to edit (not the raw)."""
    cfg = _cfg(tmp_path)
    _add_filter(cfg, "uppercase", "def run(value, ctx):\n    return value.upper()\n")
    _write_sidecar(cfg, """
editor:
  rules: default
  model: default
  pre: [uppercase]
""")
    conn, doc_id, pid = _doc_with_page(cfg, raw="lowercase raw")
    fake = _FakeTextClient()
    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", lambda *a, **k: fake)
    edit_document(cfg, conn, doc_id, verbose=False)
    assert "LOWERCASE RAW" in fake.prompts[0]
    conn.close()


def test_editor_filter_failure_stores_no_edit(monkeypatch, tmp_path):
    """A failing filter must not store a partial edit — the page records an error."""
    cfg = _cfg(tmp_path)
    _add_filter(cfg, "boom", "def run(value, ctx):\n    raise RuntimeError('filter exploded')\n")
    _write_sidecar(cfg, """
editor:
  rules: default
  model: default
  pre: [boom]
""")
    conn, doc_id, pid = _doc_with_page(cfg)
    fake = _FakeTextClient()
    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", lambda *a, **k: fake)
    edit_document(cfg, conn, doc_id, verbose=False)
    row = conn.execute("SELECT status, text, error FROM page_edits WHERE page_id=? AND editor='default'",
                       (pid,)).fetchone()
    assert row["text"] is None
    assert "filter exploded" in (row["error"] or "")
    assert fake.prompts == []          # the model was never called
    conn.close()


# -------------------------------------------------------------------- encoder hooks

def _encoder_files(cfg: Config):
    """A collection-local encoder definition for the fixture document."""
    d = cfg.dropbox / "collections" / "COLX" / "encoders"
    d.mkdir(parents=True, exist_ok=True)
    (d / "documents.md").write_text(
        "---\nname: documents\ntemperature: 0.0\nmax_tokens: 1024\n---\nExtract entries.\n",
        encoding="utf-8",
    )
    return d / "documents.md"


def test_encoder_pre_filter_shapes_the_model_input(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    enc_file = _encoder_files(cfg)
    _add_filter(cfg, "mark-pages", "def run(value, ctx):\n    return value.replace('RAW', 'MARKED')\n")
    _write_sidecar(cfg, """
encoders:
  - rules: documents
    model: default
    pre: [mark-pages]
""")
    conn, doc_id, pid = _doc_with_page(cfg, raw="RAW PAGE TEXT")
    fake = _FakeTextClient()
    fake.reply = json.dumps([{"entry": "something", "entry_attributes": {"page": 1}}])
    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", lambda *a, **k: fake)
    monkeypatch.setattr("personal_historical_archive.ingest.make_encoder_client",
                        lambda *a, **k: (fake, "default"))

    res = encode_document(cfg, conn, doc_id, enc_file=enc_file, verbose=False)
    assert res.get("action") == "encoded", res
    assert fake.prompts, "the encoder must have called the model"
    assert "MARKED" in fake.prompts[0], "the encoder.pre filter must shape what the model reads"
    assert "RAW" not in fake.prompts[0]
    conn.close()


def test_encoder_post_artifact_filter_writes_files_and_stamps(monkeypatch, tmp_path):
    """An artifact filter (returns: none) runs after the model and is stamped."""
    cfg = _cfg(tmp_path)
    enc_file = _encoder_files(cfg)
    _add_filter(
        cfg, "art",
        "import json, pathlib\n"
        "def run(value, ctx):\n"
        "    d = pathlib.Path(ctx['library_dir']) / 'segments'\n"
        "    d.mkdir(parents=True, exist_ok=True)\n"
        "    (d / 'one.md').write_text(str(len(value)))\n"
        "    return None\n",
        manifest="---\naccepts: records\nreturns: none\n---\n",
    )
    _write_sidecar(cfg, """
encoders:
  - rules: documents
    model: default
    post: [art]
""")
    conn, doc_id, pid = _doc_with_page(cfg, raw="RAW PAGE TEXT")
    fake = _FakeTextClient()
    fake.reply = json.dumps([{"entry": "something", "entry_attributes": {"page": 1}}])
    monkeypatch.setattr("personal_historical_archive.ingest.ModelClient", lambda *a, **k: fake)
    monkeypatch.setattr("personal_historical_archive.ingest.make_encoder_client",
                        lambda *a, **k: (fake, "default"))

    encode_document(cfg, conn, doc_id, enc_file=enc_file, verbose=False)
    lib = cfg.library / "collections" / "COLX"
    seg = list(lib.glob("*/segments/one.md"))
    assert seg, f"artifact not written under {lib}"
    stamps = list(lib.glob("*/.filter-stamps/documents.art.stamp"))
    assert stamps, "a successful artifact run must leave a stamp"
    conn.close()

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli


def _args(**kw):
    base = dict(doc=1, page=1, edited=False, editor=None, palaeographer=None, json=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cite_prefers_the_filled_variant(cfg, add_document, write_variant, capsys):
    """The motivating case: an empty `edited-french-ocr` stub sits beside a
    filled `edited-french-ocr@deepseek-v4-flash`. The citation must name the
    filled one — the empty stub cites nothing."""
    doc_id = add_document()
    write_variant(doc_id, "edited-french-ocr", 1, None)
    write_variant(doc_id, "edited-french-ocr@deepseek-v4-flash", 1, "real text")

    cli.cmd_cite(cfg, _args(doc=doc_id, edited=True))
    data = json.loads(capsys.readouterr().out)
    assert data["variant"] == "edited-french-ocr@deepseek-v4-flash"
    assert data["filled"] is True
    assert data["stage"] == "edited"
    assert data["slug"] == "colx-d"
    assert data["rel_path"] == "collections/COLX/d.pdf"
    assert data["file"].endswith("edited-french-ocr@deepseek-v4-flash/page-001.md")
    assert "edited: french-ocr@deepseek-v4-flash" in data["citation"]


def test_cite_exits_when_the_only_variant_is_empty(cfg, add_document, write_variant, capsys):
    doc_id = add_document()
    write_variant(doc_id, "edited-french-ocr", 1, None)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_cite(cfg, _args(doc=doc_id, edited=True))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "empty (waiting)" in err
    assert "edited-french-ocr" in err


def test_cite_requires_a_choice_when_several_filled(cfg, add_document, write_variant, capsys):
    doc_id = add_document(editor=None)
    write_variant(doc_id, "edited-a@m1", 1, "reading A")
    write_variant(doc_id, "edited-b@m2", 1, "reading B")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_cite(cfg, _args(doc=doc_id, edited=True))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "edited-a@m1" in err and "edited-b@m2" in err
    assert "--editor" in err


def test_cite_disambiguates_by_editor_flag(cfg, add_document, write_variant, capsys):
    doc_id = add_document(editor=None)
    write_variant(doc_id, "edited-a@m1", 1, "reading A")
    write_variant(doc_id, "edited-b@m2", 1, "reading B")
    cli.cmd_cite(cfg, _args(doc=doc_id, edited=True, editor="b"))
    data = json.loads(capsys.readouterr().out)
    assert data["variant"] == "edited-b@m2"


def test_cite_defaults_to_the_raw_transcription(cfg, add_document, write_variant, capsys):
    doc_id = add_document()
    write_variant(doc_id, "transcription-ocr@liteparse-fra", 1, "raw text")
    cli.cmd_cite(cfg, _args(doc=doc_id))
    data = json.loads(capsys.readouterr().out)
    assert data["stage"] == "transcription"
    assert data["variant"] == "transcription-ocr@liteparse-fra"


def test_cite_without_any_variant_exits(cfg, add_document, capsys):
    doc_id = add_document()
    with pytest.raises(SystemExit) as exc:
        cli.cmd_cite(cfg, _args(doc=doc_id))
    assert exc.value.code == 1
    assert "no transcription variant" in capsys.readouterr().err


def test_cite_text_output_is_pasteable(cfg, add_document, write_variant, capsys):
    doc_id = add_document()
    write_variant(doc_id, "edited-french-ocr@deepseek-v4-flash", 1, "real text")
    cli.cmd_cite(cfg, _args(doc=doc_id, edited=True, json=False))
    out = capsys.readouterr().out
    assert "doc 1, p. 1" in out
    assert "slug:  colx-d" in out
    assert "page:  1" in out
    assert "edited-french-ocr@deepseek-v4-flash/page-001.md" in out


def test_cite_rejects_two_disambiguators(cfg, add_document, write_variant, capsys):
    doc_id = add_document()
    write_variant(doc_id, "edited-french-ocr@deepseek-v4-flash", 1, "real text")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_cite(cfg, _args(doc=doc_id, edited=True, editor="french-ocr",
                                palaeographer="ocr"))
    assert exc.value.code == 2
    assert "only one of" in capsys.readouterr().err


def test_cite_prints_the_viewer_url(cfg, add_document, write_variant, capsys):
    """The citation links to the served viewer (prev/next), not the bare jpg."""
    doc_id = add_document()
    write_variant(doc_id, "edited-french-ocr@deepseek-v4-flash", 1, "real text")
    cli.cmd_cite(cfg, _args(doc=doc_id, edited=True))
    data = json.loads(capsys.readouterr().out)
    assert data["url"].endswith("/doc/colx-d/p001")
    assert data["overview_url"].endswith("/doc/colx-d/")
    cli.cmd_cite(cfg, _args(doc=doc_id, edited=True, json=False))
    out = capsys.readouterr().out
    assert "url:" in out and "/doc/colx-d/p001" in out

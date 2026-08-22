from __future__ import annotations

from pathlib import Path

from personal_historical_archive.extract import (
    _split_entities,
    compose_prompts,
    format_notes,
    render_document,
    resolve_editor_id,
    resolve_encoder_id,
    resolve_palaeographer_id,
    resolve_prompt,
)


def test_render_document_prefix_distinct_files(tmp_path):
    """A single image rendered with a prefix does NOT overwrite p001.jpg —
    regression for directory-of-images documents where every image was
    previously rendered to the same p001.jpg (all pages = the last image)."""
    import fitz
    out = tmp_path / "renders"
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    for path, color in ((a, (1, 0, 0)), (b, (0, 0, 1))):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
        pix.set_rect(fitz.IRect(0, 0, 10, 10), color)
        pix.save(str(path))
    ra = render_document(a, out, 72, 1800, 88, prefix="a")
    rb = render_document(b, out, 72, 1800, 88, prefix="b")
    names = sorted(p.name for p in out.iterdir())
    assert names == ["a.jpg", "b.jpg"], names
    assert ra != rb


def test_split_entities_keeps_parens():
    items = _split_entities("Geylolo (likely Gading, a ruler); João, escrivão")
    assert items == ["Geylolo (likely Gading, a ruler)", "João", "escrivão"]


def test_split_entities_dash_separated():
    items = _split_entities("Afonso - Frei João - Pero Vaz")
    assert items == ["Afonso", "Frei João", "Pero Vaz"]


def test_format_notes_inline():
    text = (
        "- Named entities: Frei João de São Bento; Pero Vaz Caminha (escrivão)\n"
        "- Content summary: Charter of donation."
    )
    out = format_notes(text)
    assert "### Named entities" in out
    assert "### Content summary" in out
    assert "- Frei João de São Bento" in out
    assert "- Pero Vaz Caminha (escrivão)" in out
    assert "Charter of donation." in out


def test_format_notes_leaves_other_structure_alone():
    text = "This is a JSON prompt output: {\"a\": 1}"
    assert format_notes(text) == text


def test_compose_prompts_base_first():
    out = compose_prompts("PAL PROMPT", "DOC PROMPT")
    assert out == "PAL PROMPT\n\n---\n\nDOC PROMPT"


def test_compose_prompts_empty_base():
    assert compose_prompts("", "DOC PROMPT") == "DOC PROMPT"


def test_resolve_prompt_sidecar_wins(tmp_path):
    dropbox, prompts = tmp_path / "dropbox", tmp_path / "prompts"
    (dropbox / "docs").mkdir(parents=True)
    prompts.mkdir()
    side = dropbox / "docs" / "charter.prompt.md"
    side.write_text("SIDECAR")
    (prompts / "default_prompt.md").write_text("DEFAULT")
    text, src = resolve_prompt("charter", dropbox / "docs", dropbox, prompts)
    assert text == "SIDECAR"
    assert src == str(side)


def test_resolve_prompt_collection_chain(tmp_path):
    dropbox, prompts = tmp_path / "dropbox", tmp_path / "prompts"
    coll = dropbox / "collections" / "COLX" / "sub"
    coll.mkdir(parents=True)
    prompts.mkdir()
    (dropbox / "collections" / "COLX" / "prompt.md").write_text("COLL")
    (prompts / "default_prompt.md").write_text("DEFAULT")
    text, src = resolve_prompt("file1", coll, dropbox, prompts)
    assert text == "COLL"
    assert src == str(dropbox / "collections" / "COLX" / "prompt.md")


def test_resolve_prompt_default_fallback(tmp_path):
    dropbox, prompts = tmp_path / "dropbox", tmp_path / "prompts"
    dropbox.mkdir()
    prompts.mkdir()
    (prompts / "default_prompt.md").write_text("DEFAULT")
    text, src = resolve_prompt("orphan", dropbox, dropbox, prompts)
    assert text == "DEFAULT"
    assert src == str(prompts / "default_prompt.md")


def test_resolve_prompt_builtin_fallback(tmp_path):
    dropbox, prompts = tmp_path / "dropbox", tmp_path / "prompts"
    dropbox.mkdir()
    prompts.mkdir()
    text, src = resolve_prompt("orphan", dropbox, dropbox, prompts)
    assert src == "builtin"
    assert "scholarly transcriber" in text


def test_resolve_palaeographer_nearest_wins(tmp_path):
    dropbox = tmp_path / "dropbox"
    coll = dropbox / "collections" / "COLX"
    sub = coll / "sub"
    sub.mkdir(parents=True)
    (coll / "palaeographer").write_text("secretary")
    pid, src = resolve_palaeographer_id("doc1", sub, dropbox)
    assert pid == "secretary"
    assert src == str(coll / "palaeographer")
    (sub / "doc1.palaeographer.md").write_text("printed")
    pid, src = resolve_palaeographer_id("doc1", sub, dropbox)
    assert pid == "printed"


def test_resolve_editor_and_encoder(tmp_path):
    dropbox = tmp_path / "dropbox"
    coll = dropbox / "collections" / "COLX"
    coll.mkdir(parents=True)
    (coll / "editor").write_text("modern-portuguese")
    (coll / "encoder").write_text("letters")
    ed, _ = resolve_editor_id("doc", coll, dropbox)
    assert ed == "modern-portuguese"
    enc, _ = resolve_encoder_id("doc", coll, dropbox)
    assert enc == "letters"

from __future__ import annotations

from personal_historical_archive import addresses, db as _db


def test_slug_rule_is_mechanical():
    assert addresses.doc_slug("collections/COLX/d.pdf") == "colx-d"
    assert (addresses.doc_slug("collections/DocHistMissPadPortOriente/vol04_1548-1550.pdf")
            == "dochistmisspadportoriente-vol04-1548-1550")
    assert addresses.doc_slug("documents/foo bar.pdf") == "documents-foo-bar"


def test_slug_folds_accents_and_collapses_separators():
    assert addresses.doc_slug("collections/x/Coïmbre Évora.pdf") == "x-coimbre-evora"
    assert addresses.doc_slug("collections/x/__weird--name__.jpg") == "x-weird-name"
    assert addresses.doc_slug("collections/x/A.B.C.pdf") == "x-a-b-c"


def test_slug_is_stable_across_reprocessing_but_changes_on_rename():
    """The contract: the slug derives from the PATH, so a re-scan never changes
    it and a rename always does (documented as intended, not a regression)."""
    original = "collections/COLX/d.pdf"
    assert addresses.doc_slug(original) == addresses.doc_slug(original)
    assert addresses.doc_slug(original) != addresses.doc_slug("collections/COLX/renamed.pdf")


def test_document_rel_path_and_slug(cfg, add_document):
    doc_id = add_document(filename="d.pdf", collection="collections/COLX")
    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()
    assert addresses.document_rel_path(cfg, doc) == "collections/COLX/d.pdf"
    assert addresses.document_slug(cfg, doc) == "colx-d"


def test_variant_files_never_lists_a_bare_alias_beside_its_model(cfg, add_document, write_variant):
    """`edited-french-ocr` and `edited-french-ocr@deepseek-v4-flash` are ONE
    variant: the bare name only means the model was unknown when it was written.
    Listing both made `pha cite --edited` refuse to choose between a reading and
    itself (the duplicate-variant bug)."""
    doc_id = add_document()
    write_variant(doc_id, "edited-french-ocr", 1, None)  # empty -> *waiting*
    write_variant(doc_id, "edited-french-ocr@deepseek-v4-flash", 1, "real text")
    write_variant(doc_id, "transcription-ocr@liteparse-fra", 1, "raw text")

    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()

    variants = addresses.variant_files(cfg, doc, 1)
    assert set(variants) == {
        "edited-french-ocr@deepseek-v4-flash",
        "transcription-ocr@liteparse-fra",
    }
    assert variants["edited-french-ocr@deepseek-v4-flash"]["filled"] is True
    assert variants["edited-french-ocr@deepseek-v4-flash"]["stage"] == "edited"
    assert variants["edited-french-ocr@deepseek-v4-flash"]["id"] == "french-ocr"
    assert variants["edited-french-ocr@deepseek-v4-flash"]["model"] == "deepseek-v4-flash"
    assert variants["transcription-ocr@liteparse-fra"]["stage"] == "transcription"


def test_variant_files_keeps_a_bare_variant_with_no_qualified_sibling(cfg, add_document, write_variant):
    doc_id = add_document(editor="null", editor_model=None)
    write_variant(doc_id, "edited-null", 1, "verbatim")
    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()
    assert set(addresses.variant_files(cfg, doc, 1)) == {"edited-null"}


def test_collapse_variant_aliases():
    """Only the bare-vs-qualified pair is an alias; two real models both stay."""
    assert addresses.collapse_variant_aliases(
        ["edited-x", "edited-x@m1", "transcription-y"]) == \
        ["edited-x@m1", "transcription-y"]
    # the current, model-less variant keeps its bare directory
    assert addresses.collapse_variant_aliases(
        ["edited-x", "edited-x@m1"], current={"edited": ("x", None)}) == \
        ["edited-x", "edited-x@m1"]
    # two models of one id are two readings, not an alias
    assert addresses.collapse_variant_aliases(
        ["edited-x@m1", "edited-x@m2"]) == ["edited-x@m1", "edited-x@m2"]
    # non-variant names pass through
    assert addresses.collapse_variant_aliases([".filter-stamps"]) == [".filter-stamps"]


def test_pick_variant_prefers_the_recorded_model():
    names = ["edited-x", "edited-x@m1", "edited-x@m2"]
    assert addresses.pick_variant(names, want_model="m2", known=True) == "edited-x@m2"
    assert addresses.pick_variant(names, want_model=None, known=True) == "edited-x"
    # unknown state: the model-qualified name wins over the bare alias
    assert addresses.pick_variant(names) == "edited-x@m1"


def test_variant_files_empty_without_a_library_dir(cfg, add_document):
    doc_id = add_document()
    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()
    assert addresses.variant_files(cfg, doc, 1) == {}


def test_render_path_uses_current_sha(cfg, add_document, write_render):
    doc_id = add_document()
    written = write_render(doc_id, 1)
    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()
    assert addresses.render_path(cfg, doc, 1) == written


def test_render_path_falls_back_to_named_source_image(cfg, add_document, write_render):
    doc_id = add_document(source_names=["502V"])
    write_render(doc_id, 1, source_name="502V")
    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()
    assert addresses.render_path(cfg, doc, 1, "502V") is not None
    assert addresses.render_path(cfg, doc, 1, None) is None


def test_render_path_missing_is_none(cfg, add_document):
    doc_id = add_document()
    conn = _db.connect(cfg.db_path)
    try:
        doc = _db.get_document(conn, doc_id)
    finally:
        conn.close()
    assert addresses.render_path(cfg, doc, 1) is None


def test_render_url_and_variant_label():
    assert (addresses.render_url("http://127.0.0.1:8765/", "colx-d", 7)
            == "http://127.0.0.1:8765/doc/colx-d/p007.jpg")
    assert (addresses.variant_label("edited-french-ocr@deepseek-v4-flash")
            == "edited: french-ocr@deepseek-v4-flash")
    assert addresses.variant_label("transcription-ocr") == "transcription: ocr"

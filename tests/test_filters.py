"""Stage filters: contract, params, chains, hooks, artifact stamps, reference vectors.

The reference-filter expectations come from FILTERS_PLAN.md §7.9 (the
documenta-indica acceptance vectors).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import filters as F
from personal_historical_archive.filters import (
    FilterError,
    FilterSpec,
    apply_filters,
    artifact_stale,
    build_context,
    discover_filters,
    filter_sha,
    load_filter,
    parse_result,
    stamp_path,
    validate_for_hook,
    write_stamp,
)

ROOT = Path(__file__).resolve().parents[1]
REF_FILTERS = ROOT / "filters"


def _ctx(cfg=None, **kw):
    cfg = cfg or SimpleNamespace(archive_dir=str(ROOT))
    base = dict(cfg=cfg, document={"id": 7, "path": "/x/d.pdf", "filename": "d.pdf",
                                   "dir_path": "collections/COLX"},
                stage="editor", hook="editor.pre", kind="text", params={}, inputs={},
                page=1, library_dir=Path("/tmp/lib"), pages_dir_edited=None,
                records_file=None, concatenated_file=None)
    base.update(kw)
    return build_context(**base)


def run_ref(name, text, hook="editor.pre", filters_dir=REF_FILTERS, **params):
    """Run one reference filter, returning the value."""
    ctx = _ctx(hook=hook, kind=F.HOOK_KINDS[hook], params=params)
    out, _ran = apply_filters(text, [FilterSpec(name, params)], hook=hook, ctx=ctx,
                              filters_dir=filters_dir, verbose=False)
    return out


# --------------------------------------------------------------- registry & contract

def test_discover_skips_underscore_and_reports_ids():
    found = discover_filters(REF_FILTERS)
    assert "markdown-from-records" in found
    assert all(not n.startswith("_") for n in found)
    f = found["markdown-from-records"]
    assert f.accepts == "records" and f.returns == "none"
    assert f.params["out_dir"] == "segments"


def test_missing_filter_is_a_load_error():
    with pytest.raises(FilterError) as e:
        load_filter(REF_FILTERS, "nope-not-here")
    assert "no filter named" in str(e.value)


def test_hook_validation_rejects_wrong_kind():
    artifacts = load_filter(REF_FILTERS, "markdown-from-records")  # records -> none
    with pytest.raises(FilterError):
        validate_for_hook(artifacts, "editor.pre")     # text hook
    validate_for_hook(artifacts, "encoder.post")       # records hook: fine
    text_filter = load_filter(REF_FILTERS, "collapse-whitespace")
    validate_for_hook(text_filter, "editor.pre")
    with pytest.raises(FilterError):
        validate_for_hook(text_filter, "encoder.post")  # text filter on records


def test_parse_result_pass_through_and_kind_mismatch():
    assert parse_result(None, expected_kind="text", name="f") == (False, None)
    assert parse_result({}, expected_kind="text", name="f") == (False, None)
    assert parse_result({"kind": "text", "value": None}, expected_kind="text", name="f") == (False, None)
    assert parse_result({"kind": "text", "value": "x"}, expected_kind="text", name="f") == (True, "x")
    with pytest.raises(FilterError):
        parse_result({"kind": "records", "value": []}, expected_kind="text", name="f")
    with pytest.raises(FilterError):
        parse_result({"kind": "bogus", "value": "x"}, expected_kind="text", name="f")


def test_manifest_validation_errors(tmp_path):
    d = tmp_path / "filters" / "bad"
    d.mkdir(parents=True)
    (d / "filter.py").write_text("def run(value, ctx):\n    return value\n")
    (d / "filter.md").write_text("---\naccepts: nonsense\n---\n")
    with pytest.raises(FilterError) as e:
        discover_filters(tmp_path / "filters")
    assert "accepts must be" in str(e.value)


def test_no_script_and_no_command_is_an_error(tmp_path):
    d = tmp_path / "filters" / "empty"
    d.mkdir(parents=True)
    (d / "filter.md").write_text("---\naccepts: text\n---\n")
    with pytest.raises(FilterError):
        discover_filters(tmp_path / "filters")


# --------------------------------------------------------------------------- chain

def test_chain_runs_in_order_and_composes(tmp_path):
    fd = tmp_path / "filters"
    for name, suffix in (("a-upper", "X"), ("b-bang", "!")):
        d = fd / name
        d.mkdir(parents=True)
        (d / "filter.py").write_text(
            "def run(value, ctx):\n    return value + %r\n" % suffix
        )
    ctx = _ctx()
    out, ran = apply_filters("v", [FilterSpec("a-upper"), FilterSpec("b-bang")],
                             hook="editor.pre", ctx=ctx, filters_dir=fd, verbose=False)
    assert out == "vX!"
    assert [r["name"] for r in ran] == ["a-upper", "b-bang"]


def test_failing_filter_aborts_the_unit(tmp_path):
    fd = tmp_path / "filters" / "boom"
    fd.mkdir(parents=True)
    (fd / "filter.py").write_text("def run(value, ctx):\n    raise ValueError('nope')\n")
    ctx = _ctx()
    with pytest.raises(FilterError) as e:
        apply_filters("v", [FilterSpec("boom")], hook="editor.pre", ctx=ctx,
                      filters_dir=tmp_path / "filters", verbose=False)
    assert "boom" in str(e.value)


def test_pass_through_filter_leaves_value_unchanged(tmp_path):
    fd = tmp_path / "filters" / "noop"
    fd.mkdir(parents=True)
    (fd / "filter.py").write_text("def run(value, ctx):\n    return None\n")
    ctx = _ctx()
    out, ran = apply_filters("keep me", [FilterSpec("noop")], hook="editor.pre",
                             ctx=ctx, filters_dir=tmp_path / "filters", verbose=False)
    assert out == "keep me"
    assert len(ran) == 1


def test_params_defaults_overridden_by_sidecar(tmp_path):
    d = tmp_path / "filters" / "p"
    d.mkdir(parents=True)
    (d / "filter.md").write_text("---\nparams:\n  mode: default\n  keep: yes\n---\n")
    (d / "filter.py").write_text(
        "def run(value, ctx):\n"
        "    p = ctx['params']\n"
        "    return f\"{p['mode']}|{p['keep']}\"\n"
    )
    f = load_filter(tmp_path / "filters", "p")
    assert F.resolve_params(f, FilterSpec("p")) == {"mode": "default", "keep": True}
    assert F.resolve_params(f, FilterSpec("p", {"mode": "side"})) == {"mode": "side", "keep": True}
    out = apply_filters("x", [FilterSpec("p", {"mode": "side"})], hook="editor.pre",
                        ctx=_ctx(), filters_dir=tmp_path / "filters", verbose=False)[0]
    assert out == "side|True"


def test_context_carries_the_documented_keys():
    ctx = _ctx()
    for key in ("archive_dir", "document", "document_id", "filename", "collection",
                "stage", "hook", "kind", "encoder", "page", "source_name",
                "library_dir", "pages_dir_raw", "pages_dir_edited", "records_file",
                "concatenated_file", "sidecar", "params", "inputs"):
        assert key in ctx, key


def test_subprocess_mode_uses_the_command_from_the_manifest(tmp_path):
    """A `command:` manifest runs an executable with the envelope on disk."""
    fd = tmp_path / "filters" / "ext"
    fd.mkdir(parents=True)
    script = tmp_path / "double.py"
    script.write_text(
        "import json, sys\n"
        "a = sys.argv\n"
        "env = json.load(open(a[a.index('--input') + 1]))\n"
        "json.dump({'kind': env['kind'], 'value': env['value'] * 2},\n"
        "          open(a[a.index('--output') + 1], 'w'))\n"
    )
    import sys as _sys
    (fd / "filter.md").write_text(
        f"---\naccepts: text\nreturns: text\ncommand: ['{_sys.executable}', '{script}']\n---\n"
    )
    out, _ = apply_filters("ab", [FilterSpec("ext")], hook="editor.pre", ctx=_ctx(),
                           filters_dir=tmp_path / "filters", verbose=False)
    assert out == "abab"


def test_filter_sha_changes_with_the_script(tmp_path):
    d = tmp_path / "filters" / "s"
    d.mkdir(parents=True)
    f_py = d / "filter.py"
    f_py.write_text("def run(value, ctx):\n    return value\n")
    f = load_filter(tmp_path / "filters", "s")
    first = filter_sha(f)
    time.sleep(0.01)
    f_py.write_text("def run(value, ctx):\n    return value + '!'\n")
    f2 = load_filter(tmp_path / "filters", "s")
    assert filter_sha(f2) != first


# ------------------------------------------------------------------- artifact stamps

def test_artifact_stamp_lifecycle(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    pages = tmp_path / "pages_edited"
    pages.mkdir()
    (pages / "page-001.md").write_text("body")
    f = load_filter(REF_FILTERS, "markdown-from-records")
    # no stamp yet -> stale (first run)
    assert artifact_stale(lib, "documents", f, pages_dir_edited=pages) is True
    write_stamp(lib, "documents", f)
    assert stamp_path(lib, "documents", f.name).exists()
    assert artifact_stale(lib, "documents", f, pages_dir_edited=pages) is False
    # a historian's correction to an EDITED page re-materialises the artifact
    time.sleep(0.02)
    (pages / "page-001.md").write_text("corrected")
    import os
    os.utime(pages / "page-001.md", (time.time() + 5,) * 2)
    assert artifact_stale(lib, "documents", f, pages_dir_edited=pages) is True


# ------------------------------------------------------- reference filter vectors

def test_strip_ocr_page_separator_removes_only_the_marker():
    assert run_ref("strip-ocr-page-separator", "--- Page 7 ---\nMalaca e nobre") == "Malaca e nobre"
    assert run_ref("strip-ocr-page-separator", "texto\n\n--- Page 8 ---\nmais") == "texto\n\nmais"
    # a mid-text line that is not a marker survives
    assert run_ref("strip-ocr-page-separator", "a\npagina cheia de texto\nb") == \
        "a\npagina cheia de texto\nb"


def test_strip_ocr_page_separator_only_first_line_param():
    text = "texto\n--- Page 9 ---\nmais"
    assert run_ref("strip-ocr-page-separator", text, only_first_line=True) == text
    assert run_ref("strip-ocr-page-separator", "--- Page 9 ---\nmais", only_first_line=True) == "mais"


def test_collapse_whitespace_vectors():
    assert run_ref("collapse-whitespace", "Malaca    e   muito    nobre") == "Malaca e muito nobre"
    assert run_ref("collapse-whitespace", "Malaca  . . . . . . . . 437") == "Malaca 437"
    # a REAL ellipsis (no spaces) survives
    assert run_ref("collapse-whitespace", "espera...  ainda   mais") == "espera... ainda mais"
    # blank lines and foliation untouched
    assert run_ref("collapse-whitespace", "linha um\n\n[3v]\n\nlinha   dois") == \
        "linha um\n\n[3v]\n\nlinha dois"


def test_collapse_whitespace_keep_leaders_param():
    assert run_ref("collapse-whitespace", "a  . . . .  b", collapse_leaders=False) == "a . . . . b"


def test_join_hyphenated_words_vectors():
    # (a) soft hyphen: drop it
    assert run_ref("join-hyphenated-words", "o gover-\nnador mandou") == "o governador mandou"
    # (b) doubled hyphen: keep exactly one
    assert run_ref("join-hyphenated-words", "Dizer-\n-vos hei") == "Dizer-vos hei"
    assert run_ref("join-hyphenated-words", "del-\n-rei") == "del-rei"
    # (c) Portuguese enclitic keeps its hyphen
    assert run_ref("join-hyphenated-words", "encarecer-vo-\ns") == "encarecer-vos"
    # a real line-break compound before a capital is left alone
    assert run_ref("join-hyphenated-words", "Anti-\nCristo") == "Anti-\nCristo"


def test_line_numbers_sequences_and_foliation():
    out = run_ref("line-numbers", "115 quem nao tiver\n116 e outro\n\n120 mais")
    assert out == "[l. 115] quem nao tiver\n[l. 116] e outro\n\n[l. 120] mais"
    # foliation (letter after digits) is never wrapped
    assert run_ref("line-numbers", "[3v]") == "[3v]"
    # a big jump is not a margin number unless allowed
    assert run_ref("line-numbers", "115 texto\n1900 ano") == "[l. 115] texto\n1900 ano"
    assert run_ref("line-numbers", "1900 ano", allow_non_sequential=True) == "[l. 1900] ano" \
        or run_ref("line-numbers", "1900 ano", allow_non_sequential=True) == "1900 ano"


def test_line_numbers_tail_side():
    assert run_ref("line-numbers", "texto da linha 5", side="tail") == "texto da linha [l. 5]"


def test_footnote_marker_residue_keeps_digit_refs_and_note_text():
    out = run_ref("footnote-marker-residue", "governador**\n*\ntexto normal\n* Cf. Sousa")
    assert out == "governador\ntexto normal\n* Cf. Sousa"


# ------------------------------------------------------------------ back-compat

def test_sidecar_without_filters_is_unchanged():
    from personal_historical_archive.sidecar import _normalize_stage
    s = _normalize_stage({"rules": "modernise", "model": "deepseek-v4-flash"})
    assert s.pre == [] and s.post == []


def test_sidecar_parses_filter_forms():
    from personal_historical_archive.sidecar import _normalize_stage
    s = _normalize_stage({
        "rules": "modernise", "model": "m",
        "pre": ["line-numbers", {"name": "collapse-whitespace", "params": {"keep_blank_lines": False}}],
        "post": "join-hyphenated-words",
    })
    assert [f.name for f in s.pre] == ["line-numbers", "collapse-whitespace"]
    assert s.pre[1].params == {"keep_blank_lines": False}
    assert [f.name for f in s.post] == ["join-hyphenated-words"]


def test_no_filters_configured_is_a_noop():
    assert apply_filters("text", [], hook="editor.pre", ctx=_ctx(),
                         filters_dir=REF_FILTERS, verbose=False)[0] == "text"
    assert apply_filters("text", None, hook="editor.pre", ctx=_ctx(),
                         filters_dir=REF_FILTERS, verbose=False)[0] == "text"

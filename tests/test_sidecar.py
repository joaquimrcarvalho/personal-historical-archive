from pathlib import Path

import pytest

from personal_historical_archive.sidecar import (
    Sidecar,
    effective_render,
    load_sidecar,
    resolve_sidecar,
    sidecar_from_dict,
)


class _Cfg:
    render_dpi = 200
    max_image_px = 3000
    jpeg_quality = 88


def test_load_sidecar_valid(tmp_path):
    p = tmp_path / "pha.yaml"
    p.write_text("palaeographer: jesuit-cat1\neditor: null\nencoders: [table]\n")
    data = load_sidecar(p)
    assert data["palaeographer"] == "jesuit-cat1"
    assert data["editor"] is None
    assert data["encoders"] == ["table"]


def test_load_sidecar_unknown_key_rejected(tmp_path):
    p = tmp_path / "pha.yaml"
    p.write_text("palaeogarpher: x\n")
    with pytest.raises(ValueError, match="Additional properties are not allowed"):
        load_sidecar(p)


def test_merge_nearest_wins_per_key(tmp_path):
    drop = tmp_path / "dropbox"
    coll = drop / "collections" / "COLX"
    doc = coll / "docs"
    doc.mkdir(parents=True)
    (coll / "pha.yaml").write_text(
        "palaeographer:\n  rules: jesuit-cat1\n  model: minimax-m3\n"
        "editor: null\n"
        "encoders: [table, biographies]\n"
        "render:\n  max_image_px: 2000\n"
    )
    (doc / "pha.yaml").write_text(
        "palaeographer:\n  rules: jesuit-cat1\n  model: deepseek-v4\n"
    )
    sc = resolve_sidecar(drop, doc)
    assert sc.palaeographer.rules == "jesuit-cat1"
    assert sc.palaeographer.model == "deepseek-v4"  # nearest override
    assert sc.editor_set is True
    assert sc.editor is None
    assert [e.rules for e in sc.encoders] == ["table", "biographies"]
    assert sc.render == {"max_image_px": 2000}


def test_merge_inherits_unset_keys(tmp_path):
    drop = tmp_path / "dropbox"
    coll = drop / "collections" / "COLX"
    doc = coll / "docs"
    doc.mkdir(parents=True)
    (coll / "pha.yaml").write_text("palaeographer: jesuit-cat1\nrender:\n  jpeg_quality: 55\n")
    (doc / "pha.yaml").write_text("editor:\n  rules: modernise\n  model: qwen-text\n")
    sc = resolve_sidecar(drop, doc)
    assert sc.palaeographer.rules == "jesuit-cat1"  # inherited
    assert sc.editor.rules == "modernise"
    assert sc.editor.model == "qwen-text"
    assert sc.render == {"jpeg_quality": 55}


def test_effective_render_overrides_globals():
    sc = sidecar_from_dict({"render": {"max_image_px": 1200, "jpeg_quality": 60}})
    dpi, max_px, q = effective_render(_Cfg(), sc)
    assert (dpi, max_px, q) == (200, 1200, 60)


def test_effective_render_falls_back_to_globals():
    dpi, max_px, q = effective_render(_Cfg(), Sidecar())
    assert (dpi, max_px, q) == (200, 3000, 88)

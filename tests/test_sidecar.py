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
    p.write_text(
        "palaeographer:\n  rules: jesuit-cat1\n  model: minimax-m3\n"
        "editor: null\n"
        "encoders:\n  - rules: table\n    model: minimax-m2-5\n"
    )
    data = load_sidecar(p)
    assert data["palaeographer"]["rules"] == "jesuit-cat1"
    assert data["palaeographer"]["model"] == "minimax-m3"
    assert data["editor"] is None
    assert data["encoders"][0]["rules"] == "table"
    assert data["encoders"][0]["model"] == "minimax-m2-5"


def test_load_sidecar_missing_model_rejected(tmp_path):
    p = tmp_path / "pha.yaml"
    p.write_text("palaeographer:\n  rules: jesuit-cat1\n")
    with pytest.raises(ValueError, match="'model' is a required property"):
        load_sidecar(p)


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
        "encoders:\n  - rules: table\n    model: m1\n  - rules: biographies\n    model: m2\n"
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
    (coll / "pha.yaml").write_text(
        "palaeographer:\n  rules: jesuit-cat1\n  model: minimax-m3\nrender:\n  jpeg_quality: 55\n"
    )
    (doc / "pha.yaml").write_text("editor:\n  rules: modernise\n  model: qwen-text\n")
    sc = resolve_sidecar(drop, doc)
    assert sc.palaeographer.rules == "jesuit-cat1"  # inherited
    assert sc.palaeographer.model == "minimax-m3"
    assert sc.editor.rules == "modernise"
    assert sc.editor.model == "qwen-text"
    assert sc.render == {"jpeg_quality": 55}


def test_document_specific_sidecar_wins(tmp_path):
    drop = tmp_path / "dropbox"
    docs = drop / "documents"
    docs.mkdir(parents=True)
    (docs / "pha.yaml").write_text(
        "palaeographer:\n  rules: generic-pal\n  model: m1\n"
    )
    (docs / "one.pha.yaml").write_text(
        "palaeographer:\n  rules: special-pal\n  model: m2\n"
    )
    sc = resolve_sidecar(drop, docs, stem="one")
    assert sc.palaeographer.rules == "special-pal"  # doc-specific wins
    assert sc.palaeographer.model == "m2"
    # a different document falls back to the directory-level sidecar
    sc2 = resolve_sidecar(drop, docs, stem="two")
    assert sc2.palaeographer.rules == "generic-pal"


def test_effective_render_overrides_globals():
    sc = sidecar_from_dict({"render": {"max_image_px": 1200, "jpeg_quality": 60}})
    dpi, max_px, q = effective_render(_Cfg(), sc)
    assert (dpi, max_px, q) == (200, 1200, 60)


def test_effective_render_falls_back_to_globals():
    dpi, max_px, q = effective_render(_Cfg(), Sidecar())
    assert (dpi, max_px, q) == (200, 3000, 88)


# --------------------------------------------------------------------------- schema resource


def test_schema_loads_wherever_pha_is_imported():
    """`_schema()` must resolve for source, editable and packaged installs."""
    from personal_historical_archive.sidecar import _schema

    schema = _schema()
    assert schema["title"].startswith("pha.yaml")
    assert "palaeographer" in schema["properties"]


def test_schema_env_override(tmp_path, monkeypatch):
    """PHA_SCHEMA wins, so a broken install can be pointed at a known schema."""
    import personal_historical_archive.sidecar as sidecar

    override = tmp_path / "schema.json"
    override.write_text('{"title": "override"}', encoding="utf-8")
    monkeypatch.setenv("PHA_SCHEMA", str(override))
    monkeypatch.setattr(sidecar, "_SCHEMA", None)
    assert sidecar._schema() == {"title": "override"}


def test_schema_missing_names_where_it_looked(tmp_path, monkeypatch):
    """A missing schema is a clear error, never a bogus parents[N] path."""
    import personal_historical_archive.sidecar as sidecar

    monkeypatch.setenv("PHA_SCHEMA", str(tmp_path / "absent.json"))
    monkeypatch.setattr(sidecar, "_SCHEMA", None)
    with pytest.raises(FileNotFoundError, match="absent.json"):
        sidecar._schema()


def test_wheel_force_includes_the_schema():
    """Regression gate for the wheel that shipped without its schema.

    The canonical file stays at the repo root (its published `$id`, and what
    existing pha.yaml files point their editors at), so the wheel build MUST
    force-include it under the package or an installed pha cannot validate any
    sidecar at all. See
    enhancements/pha-installed-wheel-missing-schema-bug-report.md.
    """
    import tomllib

    root = Path(__file__).resolve().parents[1]
    canonical = root / "schema" / "pha-sidecar.schema.json"
    assert canonical.is_file()
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
    force = wheel.get("force-include", {})
    assert force.get("schema/pha-sidecar.schema.json") == (
        "personal_historical_archive/schema/pha-sidecar.schema.json"
    )

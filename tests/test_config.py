from __future__ import annotations

import os
import sys

from pathlib import Path

import personal_historical_archive.config as c
from personal_historical_archive.config import Config


def _to_abs(path: str) -> Path:
    if os.name == "nt":
        # drive-ROOTED absolute path (C:\abs\path); Path("C:")/... would be
        # drive-relative (C:abs\path) and NOT absolute on Windows.
        return Path("C:/") / path
    return Path("/" + path.lstrip("/"))


def test_p_expands_tilde():
    out = c._p(Path("/proj"), "~/pha-test-data/dropbox")
    assert out == Path.home() / "pha-test-data" / "dropbox"


def test_p_relative_to_root():
    root = Path("/proj")
    # relative input resolves under root; the exact shape differs by OS
    if os.name == "nt":
        assert c._p(root, "data") == (root / "data").resolve()
    else:
        assert c._p(root, "data") == Path("/proj/data").resolve()


def test_p_absolute_path():
    # a path that is absolute on the current platform passes through unchanged
    abs_path = _to_abs("abs/path")
    assert c._p(Path("/proj"), str(abs_path)) == abs_path


def test_expand_env(monkeypatch):
    monkeypatch.setenv("PHA_TEST_KEY", "secret")
    monkeypatch.setattr(c, "_dotenv", lambda: {})
    monkeypatch.setattr(c, "_secret_get", lambda name: "")
    assert c._expand("${PHA_TEST_KEY}") == "secret"
    assert c._expand("${PHA_TEST_UNSET:-fallback}") == "fallback"
    monkeypatch.delenv("PHA_TEST_KEY")
    assert c._expand("${PHA_TEST_UNSET}") == ""


def test_config_load_tilde_dropbox(monkeypatch, tmp_path):
    monkeypatch.delenv("PHA_HOME", raising=False)
    monkeypatch.delenv("PHA_DROPBOX", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: ~/pha-test-dropbox\n  palaeographers: palaeographers\n"
        "  editors: editors\n  encoders: encoders\n  prompts: prompts\n"
    )
    cfg = Config.load(root)
    assert cfg.dropbox == Path.home() / "pha-test-dropbox"


def test_config_load_env_dropbox_wins(monkeypatch, tmp_path):
    monkeypatch.delenv("PHA_HOME", raising=False)
    abs_env = _to_abs("from/env")  # a genuinely absolute path on this platform
    monkeypatch.setenv("PHA_DROPBOX", str(abs_env))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  dropbox: dropbox\n")
    cfg = Config.load(root)
    assert cfg.dropbox == abs_env


def test_config_load_dotenv_dropbox(monkeypatch, tmp_path):
    """PHA_DROPBOX stored in the root .env (via `pha set dropbox`) is honoured."""
    monkeypatch.delenv("PHA_HOME", raising=False)
    monkeypatch.delenv("PHA_DROPBOX", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  dropbox: dropbox\n")
    (root / ".env").write_text(f"PHA_DROPBOX=~/external-docs\n")
    cfg = Config.load(root)
    assert cfg.dropbox == Path.home() / "external-docs"


def test_config_archive_dir_default(tmp_path):
    """archive_dir defaults to the project root; data paths derive from it."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    cfg = Config.load(root)
    assert cfg.archive_dir == root.resolve()
    assert cfg.dropbox == (root / "dropbox").resolve()
    assert cfg.library == (root / "library").resolve()
    assert cfg.renders == (root / "renders").resolve()
    assert cfg.db_path == (root / "archive.db").resolve()
    assert cfg.palaeographers_dir == (root / "palaeographers").resolve()


def test_config_archive_dir_env_wins(monkeypatch, tmp_path):
    """PHA_ARCHIVE_DIR env var overrides everything."""
    monkeypatch.delenv("PHA_HOME", raising=False)
    monkeypatch.setenv("PHA_ARCHIVE_DIR", str(tmp_path / "arc"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    cfg = Config.load(root)
    arc = (tmp_path / "arc").resolve()
    assert cfg.archive_dir == arc
    assert cfg.dropbox == (arc / "dropbox").resolve()
    assert cfg.db_path == (arc / "archive.db").resolve()


def test_config_archive_dir_dotenv(monkeypatch, tmp_path):
    """PHA_ARCHIVE_DIR in the root .env (via pha set archive-dir) is honoured."""
    monkeypatch.delenv("PHA_HOME", raising=False)
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    (root / ".env").write_text(f"PHA_ARCHIVE_DIR={tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    arc = (tmp_path / "arc").resolve()
    assert cfg.archive_dir == arc
    assert cfg.dropbox == (arc / "dropbox").resolve()


def test_config_archive_dir_explicit_in_yaml(tmp_path):
    """paths.archive_dir in config.yaml is used when env/.env are absent."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    assert cfg.archive_dir == (tmp_path / "arc").resolve()


def test_config_seeds_zero_config_defaults(tmp_path):
    """A fresh archive seeds default.md palaeographer/editor/encoder (qwen)."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    # seeded on first load
    assert list(cfg.palaeographers) == ["default"]
    assert list(cfg.editors) == ["default"]
    assert list(cfg.encoders) == ["default"]
    # rules-only stage files (no model at load time); the model is in models/
    assert cfg.default_model == "default"
    assert cfg.models["default"].model == "qwen/qwen3-vl-8b"
    assert cfg.palaeographers["default"].model == ""
    assert cfg.editors["default"].model == ""
    assert cfg.encoders["default"].model == ""
    # resolving binds the default model
    pal = cfg.resolve_model(cfg.palaeographers["default"])
    assert pal.model == "qwen/qwen3-vl-8b"
    assert pal.model_ref == "default"
    # the files exist in the archive
    arc = (tmp_path / "arc").resolve()
    assert (arc / "palaeographers" / "default.md").exists()
    assert (arc / "editors" / "default.md").exists()
    assert (arc / "encoders" / "default.md").exists()


def test_config_migrates_legacy_defs(tmp_path):
    """Legacy project definitions migrate into a relocated archive (id-preserving)."""
    root = tmp_path / "proj"
    root.mkdir()
    # a legacy palaeographer + editor in the PROJECT dir
    (root / "palaeographers").mkdir(parents=True)
    (root / "palaeographers" / "my-hand.md").write_text(
        "---\ndescription: x\nbase_url: http://x/v1\nmodel: m\n---\nbody\n"
    )
    (root / "editors").mkdir(parents=True)
    (root / "editors" / "modern-pt.md").write_text(
        "---\ndescription: y\nbase_url: http://x/v1\nmodel: m\n---\nbody\n"
    )
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    cfg = Config.load(root)
    arc = (tmp_path / "arc").resolve()
    # migrated + default seeded
    assert "my-hand" in cfg.palaeographers
    assert "modern-pt" in cfg.editors
    assert "default" in cfg.palaeographers
    assert (arc / "palaeographers" / "my-hand.md").exists()
    assert (arc / "editors" / "modern-pt.md").exists()


def test_load_palaeographers(tmp_path):
    d = tmp_path / "palaeographers"
    d.mkdir()
    (d / "qwen-local.md").write_text(
        "---\ndescription: test\nbase_url: http://x/v1\nmodel: m\n---\nbody prompt\n"
    )
    (d / "_sample.md").write_text("ignored")
    (d / "broken.md").write_text("---\ntemperature: not-a-number\n---\n")
    pals = c._load_model_dir(d, "palaeographer", c._palaeographer_from_frontmatter)
    assert list(pals) == ["qwen-local"]
    assert pals["qwen-local"].model == "m"
    assert pals["qwen-local"].prompt_text == "body prompt"


# --------------------------------------------------------------------------- tesseract engine

def test_tesseract_model_interface_parsed():
    """A models/<id>.md with `engine: tesseract` carries the OCR settings."""
    m = c._model_from_frontmatter(
        "tesseract",
        "---\ndescription: ocr\nengine: tesseract\n"
        "tesseract_lang: por+lat\ntesseract_psm: 6\n---\n",
        Path("/tmp/tesseract.md"),
    )
    assert m.engine == "tesseract"
    assert m.tesseract_lang == "por+lat"
    assert m.tesseract_psm == 6


def test_tesseract_palaeographer_inline_engine():
    """A palaeographer file that inlines `engine: tesseract` (no base_url)
    is treated as a legacy inline interface: the engine survives and base_url
    stays the default, so resolve_model() does not rebind a different model."""
    pal = c._palaeographer_from_frontmatter(
        "ocr",
        "---\ndescription: tesseract\nengine: tesseract\ntesseract_lang: por\n---\nbody\n",
        Path("/tmp/ocr.md"),
    )
    assert pal.engine == "tesseract"
    assert pal.tesseract_lang == "por"
    assert pal.base_url == "http://127.0.0.1:1234/v1"
    assert pal.model == ""


def test_tesseract_palaeographer_bound_via_model_id(tmp_path):
    """rules-only palaeographer + pha.yaml `model: tesseract` binds the engine."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    arc = tmp_path / "arc"
    (arc / "models").mkdir(parents=True)
    (arc / "models" / "tesseract.md").write_text(
        "---\nengine: tesseract\ntesseract_lang: lat\ntesseract_psm: 6\n---\n"
    )
    (arc / "palaeographers").mkdir(parents=True)
    (arc / "palaeographers" / "ocr-rules.md").write_text(
        "---\ndescription: rules only\n---\nbody\n"
    )
    cfg = Config.load(root)
    pal = cfg.palaeographers["ocr-rules"]
    bound = cfg.resolve_model(pal, "tesseract")
    assert bound.engine == "tesseract"
    assert bound.tesseract_lang == "lat"
    assert bound.tesseract_psm == 6
    assert bound.model_ref == "tesseract"


# --------------------------------------------------------------------------- liteparse engine

def test_liteparse_model_interface_parsed():
    """A models/<id>.md with `engine: liteparse` carries its OCR settings."""
    m = c._model_from_frontmatter(
        "liteparse",
        "---\ndescription: ocr\nengine: liteparse\n"
        "liteparse_lang: fra\nliteparse_dpi: 300\n---\n",
        Path("/tmp/liteparse.md"),
    )
    assert m.engine == "liteparse"
    assert m.liteparse_lang == "fra"
    assert m.liteparse_dpi == 300


def test_liteparse_palaeographer_inline_engine():
    """A palaeographer that inlines `engine: liteparse` (no base_url) keeps the
    engine and the default base_url, so resolve_model() leaves it unbound."""
    pal = c._palaeographer_from_frontmatter(
        "lparse",
        "---\ndescription: liteparse\nengine: liteparse\n"
        "liteparse_lang: por\nliteparse_dpi: 300\n---\nbody\n",
        Path("/tmp/lparse.md"),
    )
    assert pal.engine == "liteparse"
    assert pal.liteparse_lang == "por"
    assert pal.liteparse_dpi == 300
    assert pal.base_url == "http://127.0.0.1:1234/v1"


def test_liteparse_palaeographer_bound_via_model_id(tmp_path):
    """rules-only palaeographer + pha.yaml `model: liteparse` binds the engine."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {tmp_path / 'arc'}\n")
    arc = tmp_path / "arc"
    (arc / "models").mkdir(parents=True)
    (arc / "models" / "liteparse.md").write_text(
        "---\nengine: liteparse\nliteparse_lang: por\nliteparse_dpi: 300\n---\n"
    )
    (arc / "palaeographers").mkdir(parents=True)
    (arc / "palaeographers" / "ocr-rules.md").write_text(
        "---\ndescription: rules only\n---\nbody\n"
    )
    cfg = Config.load(root)
    pal = cfg.palaeographers["ocr-rules"]
    bound = cfg.resolve_model(pal, "liteparse")
    assert bound.engine == "liteparse"
    assert bound.liteparse_lang == "por"
    assert bound.liteparse_dpi == 300
    assert bound.model_ref == "liteparse"

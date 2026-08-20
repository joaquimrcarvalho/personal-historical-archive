from __future__ import annotations

from pathlib import Path

import personal_historical_archive.config as c
from personal_historical_archive.config import Config


def test_p_expands_tilde():
    out = c._p(Path("/proj"), "~/pha-test-data/dropbox")
    assert out == Path.home() / "pha-test-data" / "dropbox"


def test_p_relative_to_root():
    assert c._p(Path("/proj"), "data") == Path("/proj/data").resolve()


def test_p_absolute_path():
    assert c._p(Path("/proj"), "/abs/path") == Path("/abs/path")


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
    monkeypatch.setenv("PHA_DROPBOX", "/from/env")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  dropbox: dropbox\n")
    cfg = Config.load(root)
    assert cfg.dropbox == Path("/from/env")


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

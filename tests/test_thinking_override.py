from __future__ import annotations

"""A rules file may set `thinking:` itself, and it beats the model sheet.

Motivating incident (2026-09-21): with `thinking: disabled` on the model sheet,
DeepSeek-V4.1-Flash stopped after the editor prompt's OCR-cleanup step on an
all-Latin page and never reached the translation step — ~1 300 pages stored
`done` while still Latin. `thinking` lived only on the model sheet, so a rules
file could not ask for it; and `pha editor` did not show the effective value, so
nothing made it visible. See
enhancements/pha-latin-not-translated-thinking-disabled-bug-report.md §4.2.
"""

from types import SimpleNamespace

import personal_historical_archive.config as c
from personal_historical_archive import cli
from personal_historical_archive.config import Config
from personal_historical_archive.model_client import ModelClient


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "paths:\n  dropbox: dropbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    return Config.load(root)


def _doc(cfg: Config, rel: str = "collections/COLX/d.pdf"):
    p = cfg.dropbox / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 d")
    return p


# --------------------------------------------------------------------------- parsing

def test_rules_file_thinking_is_optional_and_recorded():
    plain = c._editor_from_frontmatter("e1", "---\ntemperature: 0.1\n---\nbody",
                                       __import__("pathlib").Path("/tmp/e1.md"))
    assert plain.thinking_override is None      # inherit the model's value

    on = c._editor_from_frontmatter("e2", "---\nthinking: true\n---\nbody",
                                    __import__("pathlib").Path("/tmp/e2.md"))
    assert on.thinking_override is True
    # the accepted spellings all work
    for raw in ("false", "disabled", "off", "no", "0"):
        off = c._editor_from_frontmatter("e3", f"---\nthinking: {raw}\n---\nbody",
                                         __import__("pathlib").Path("/tmp/e3.md"))
        assert off.thinking_override is False, raw


def test_palaeographer_and_encoder_also_take_a_thinking_override():
    from pathlib import Path
    pal = c._palaeographer_from_frontmatter("p", "---\nthinking: false\n---\nbody", Path("/tmp/p.md"))
    enc = c._encoder_from_frontmatter("x", "---\nthinking: false\n---\nbody", Path("/tmp/x.md"))
    assert pal.thinking_override is False
    assert enc.thinking_override is False


# --------------------------------------------------------------------------- effective value

def test_rules_override_beats_the_model_sheet(tmp_path):
    cfg = _cfg(tmp_path)
    from pathlib import Path
    off_model = c._model_from_frontmatter(
        "off", "---\nmodel: m\nbase_url: http://x/v1\nthinking: disabled\n---\n", Path("/tmp/off.md"))
    on_model = c._model_from_frontmatter(
        "on", "---\nmodel: m\nbase_url: http://x/v1\nthinking: true\n---\n", Path("/tmp/on.md"))
    assert off_model.thinking is False and on_model.thinking is True
    cfg.models["off"], cfg.models["on"] = off_model, on_model

    plain = c._editor_from_frontmatter("plain", "---\n---\nbody", Path("/tmp/plain.md"))
    want_on = c._editor_from_frontmatter("on", "---\nthinking: true\n---\nbody", Path("/tmp/on2.md"))
    want_off = c._editor_from_frontmatter("off", "---\nthinking: false\n---\nbody", Path("/tmp/off2.md"))

    # no `thinking:` anywhere -> the model's value applies (regression)
    assert cfg.with_model(plain, "off").thinking is False
    assert cfg.with_model(plain, "on").thinking is True
    # the rules file wins, in both directions
    assert cfg.with_model(want_on, "off").thinking is True
    assert cfg.with_model(want_off, "on").thinking is False
    # and the un-paired path (rules-only stage) keeps the field
    assert want_off.thinking_override is False


def test_the_pipeline_sends_the_overridden_value(tmp_path):
    """The client call is what actually sends `thinking`, so assert on it."""
    from pathlib import Path
    cfg = _cfg(tmp_path)
    cfg.models["off"] = c._model_from_frontmatter(
        "off", "---\nmodel: m\nbase_url: http://x/v1\nthinking: disabled\n---\n", Path("/tmp/off.md"))
    pal = c._palaeographer_from_frontmatter(
        "want-on", "---\nthinking: true\n---\nread it", Path("/tmp/want-on.md"))
    resolved = cfg.with_model(pal, "off")
    assert resolved.thinking is True

    seen: dict = {}

    class FakeClient:
        def chat_vision(self, model, prompt, image_path, temperature=0.1,
                        max_tokens=4096, **kw):
            seen.update(kw)
            return "text"

    from personal_historical_archive.ingest import transcribe_page
    img = tmp_path / "p.jpg"
    img.write_bytes(b"jpeg")
    transcribe_page(FakeClient(), resolved, "prompt", img)

    assert seen["thinking"] is True        # the rules file won over the sheet


# --------------------------------------------------------------------------- visibility

def test_cli_editor_shows_the_effective_params(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _doc(cfg)
    (cfg.palaeographers_dir.mkdir(parents=True, exist_ok=True))
    (cfg.editors_dir / "want-on.md").write_text("---\nthinking: true\n---\nmodernise\n")
    (cfg.models_dir / "off-model.md").write_text(
        "---\ndescription: m\nbase_url: http://x/v1\nmodel: m\nthinking: disabled\n---\n")
    (cfg.dropbox / "collections" / "COLX" / "pha.yaml").write_text(
        "editor: {rules: want-on, model: off-model}\n")
    cfg = Config.load(cfg.root)

    cli.cmd_editor(cfg, SimpleNamespace(file="collections/COLX/d.pdf"))
    out = capsys.readouterr().out
    assert "params:" in out
    assert "thinking=on" in out          # the rules file wins over the sheet


def test_cli_editor_shows_thinking_off_when_inherited(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _doc(cfg)
    (cfg.editors_dir / "plain.md").write_text("---\n---\nmodernise\n")
    (cfg.models_dir / "off-model.md").write_text(
        "---\ndescription: m\nbase_url: http://x/v1\nmodel: m\nthinking: disabled\n---\n")
    (cfg.dropbox / "collections" / "COLX" / "pha.yaml").write_text(
        "editor: {rules: plain, model: off-model}\n")
    cfg = Config.load(cfg.root)

    cli.cmd_editor(cfg, SimpleNamespace(file="collections/COLX/d.pdf"))
    out = capsys.readouterr().out
    assert "thinking=off" in out         # visible, which is what was missing


def test_cli_palaeographer_shows_the_effective_params(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _doc(cfg)
    (cfg.palaeographers_dir / "want-on.md").write_text("---\nthinking: true\n---\nread it\n")
    (cfg.models_dir / "off-model.md").write_text(
        "---\ndescription: m\nbase_url: http://x/v1\nmodel: m\nthinking: disabled\n---\n")
    (cfg.dropbox / "collections" / "COLX" / "pha.yaml").write_text(
        "palaeographer: {rules: want-on, model: off-model}\n")
    cfg = Config.load(cfg.root)

    cli.cmd_palaeographer(cfg, SimpleNamespace(file="collections/COLX/d.pdf"))
    out = capsys.readouterr().out
    assert "params:" in out and "thinking=on" in out

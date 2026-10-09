from __future__ import annotations

import os
from pathlib import Path

from personal_historical_archive import cli
from personal_historical_archive.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_cfg(tmp_path, archive_dir="."):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {archive_dir}\n")
    return Config.load(root), root


def test_archive_explicitly_set_ignores_default_dot(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    assert cli._archive_explicitly_set(cfg) is False


def test_archive_explicitly_set_real_path(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    arc = tmp_path / "real-archive"
    arc.mkdir()
    cfg, _ = _make_cfg(tmp_path, str(arc))
    assert cli._archive_explicitly_set(cfg) is True


def test_archive_explicitly_set_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("PHA_ARCHIVE_DIR", str(tmp_path / "env-arc"))
    cfg, _ = _make_cfg(tmp_path, ".")
    assert cli._archive_explicitly_set(cfg) is True


def test_archive_unconfigured_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    assert cli._archive_unconfigured(cfg) is True


def test_archive_unconfigured_false_when_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHA_ARCHIVE_DIR", str(tmp_path / "env-arc"))
    cfg, _ = _make_cfg(tmp_path, ".")
    assert cli._archive_unconfigured(cfg) is False


def test_archive_unconfigured_false_when_db_has_documents(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, root = _make_cfg(tmp_path, ".")
    # seed one document so the default archive is not empty
    conn = __import__("personal_historical_archive.db", fromlist=["db"]).connect(
        cfg.db_path)
    now = __import__("time").time()
    conn.execute(
        "INSERT INTO documents (filename, path, sha256, status, created_at, updated_at) "
        "VALUES ('a.pdf', ?, 'x', 'done', ?, ?)",
        (str(root / "a.pdf"), now, now))
    conn.commit()
    conn.close()
    assert cli._archive_unconfigured(cfg) is False


def test_prompt_noninteractive_stops(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")

    class FakeStdin:
        def isatty(self):
            return False
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin())
    monkeypatch.setattr(cli.sys, "stdout", cli.sys.stdout)  # keep real stdout

    assert cli._prompt_archive_setup(cfg) is False
    err = capsys.readouterr().err
    assert "No pha archive is configured" in err
    assert "pha set archive-dir <path>" in err
    assert "pha init-archive" in err


def test_prompt_interactive_create_new(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    home = tmp_path / "fakehome"
    home.mkdir()

    class FakeStdin:
        def isatty(self):
            return True
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin())
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    monkeypatch.setattr(cli.os.path, "expanduser", lambda s: str(home))

    assert cli._prompt_archive_setup(cfg) is True
    # created ~/pha-home (fakehome/pha-home) and recorded it in config.yaml
    assert (home / "pha-home" / "archive.db").parent.is_dir()
    config = (cfg.root / "config.yaml").read_text(encoding="utf-8")
    assert "archive_dir:" in config and str(home) in config
    # and NOT in the gitignored .env (the pointer lives in config.yaml now)
    envp = cfg.root / ".env"
    assert not envp.exists() or "PHA_ARCHIVE_DIR" not in envp.read_text(encoding="utf-8")


def test_prompt_interactive_existing(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")

    class FakeStdin:
        def isatty(self):
            return True
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin())
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    set_calls = []
    monkeypatch.setattr(cli, "_set_archive_dir_in_config",
                        lambda *a, **k: set_calls.append((a, k)))

    assert cli._prompt_archive_setup(cfg) is True
    assert set_calls, "_set_archive_dir_in_config should have been called"


def test_help_overview_points_to_docs(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": None})())
    out = capsys.readouterr().out
    for f in ("README.md", "PIPELINE.md", "MCP_CLIENTS.md",
              "HISTORIANS_README.md", "AGENTS.md", "MULTI_COMPUTER.md",
              "HARNESS_INTRODUCTION.md", "DSH_PLUGIN.md"):
        assert f in out
    assert "pha status" in out
    assert "pha set archive-dir" in out
    assert "pha-home" in out


def test_help_overview_lists_the_machine_to_machine_commands(tmp_path, capsys,
                                                             monkeypatch):
    """`pha help` is the first thing an operator or agent runs, and its COMMON
    COMMANDS list is curated (not every command belongs there), so a new
    headline command has to be added by hand — and `pha handoff` was not."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": None})())
    out = capsys.readouterr().out
    for cmd in ("pha bundle", "pha unbundle", "pha handoff out|in|back|fetch"):
        assert cmd in out, f"`pha help` does not mention {cmd!r}"


def test_help_topic_prints_path(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "mcp"})())
    out = capsys.readouterr().out
    assert "MCP_CLIENTS.md" in out
    assert str(cfg.root / "MCP_CLIENTS.md") in out


def test_help_pipeline_topic_prints_path(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "pipeline"})())
    out = capsys.readouterr().out
    assert "PIPELINE.md" in out
    assert str(cfg.root / "PIPELINE.md") in out


def test_help_multi_computer_topic_prints_path(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "multi-computer"})())
    out = capsys.readouterr().out
    assert "MULTI_COMPUTER.md" in out
    assert str(cfg.root / "MULTI_COMPUTER.md") in out


def test_help_unknown_topic_stderr(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "bogus"})())
    err = capsys.readouterr().err
    assert "unknown help topic: bogus" in err
    assert "known topics" in err


def _fake_doc(d_id):
    return {"filename": f"doc{d_id}.pdf", "dir_path": f"collections/cat"}


def test_pending_summary_empty():
    assert cli._pending_summary_lines([], _fake_doc) == []


def test_pending_summary_groups_by_document():
    pending = [
        {"document_id": 4, "page_no": 1, "variant": "transcription-"},
        {"document_id": 4, "page_no": 2, "variant": "edited-x"},
        {"document_id": 7, "page_no": 5, "variant": "transcription-"},
    ]
    lines = cli._pending_summary_lines(pending, _fake_doc)
    joined = "\n".join(lines)
    # total + doc count (3 unique page/doc pairs across 2 documents)
    assert "3 page(s)" in lines[0]
    assert "2 document(s)" in lines[1]
    # which documents and pages
    assert "#4   [collections/cat] doc4.pdf  — pages 1, 2" in joined
    assert "#7   [collections/cat] doc7.pdf  — pages 5" in joined
    # instruction: a transcription correction is pending -> pha edit is advised
    assert "pha review" in joined
    assert "pha edit" in joined
    assert joined.rstrip().endswith("pha reindex")


def test_pending_summary_missing_doc():
    pending = [{"document_id": 9, "page_no": 3, "variant": "transcription-"}]
    lines = cli._pending_summary_lines(pending, lambda d: None)
    assert "doc#9" in lines[2]
    assert "(root)" in lines[2]


# --- the archive location now lives in config.yaml (not the gitignored .env) ---

def _run_set_archive_dir(cfg, path):
    """Drive `pha set archive-dir PATH` non-interactively."""
    from types import SimpleNamespace
    cli.cmd_set_archive_dir(cfg, SimpleNamespace(path=str(path)))


def test_set_archive_dir_writes_paths_archive_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, root = _make_cfg(tmp_path, ".")
    arc = tmp_path / "the-archive"
    arc.mkdir()
    _run_set_archive_dir(cfg, arc)
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert f"archive_dir: {arc}" in text
    assert not (root / ".env").exists() or "PHA_ARCHIVE_DIR" not in (
        root / ".env").read_text(encoding="utf-8")
    # and it resolves on the next load
    assert Config.load(root).archive_dir == arc.resolve()


def test_set_archive_dir_preserves_config_comments_and_other_keys(tmp_path, monkeypatch):
    """The edit must be a targeted line change, not a YAML round-trip."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        "# a comment that must survive\n"
        "paths:\n"
        "  archive_dir: .   # inline note\n"
        "  dropbox: dropbox\n"
        "\n"
        "update:\n  check: true\n"
    )
    cfg = Config.load(root)
    arc = tmp_path / "arc2"
    arc.mkdir()
    _run_set_archive_dir(cfg, arc)
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "# a comment that must survive" in text
    assert "dropbox: dropbox" in text
    assert "check: true" in text
    assert f"archive_dir: {arc}" in text
    assert text.count("archive_dir:") == 1          # replaced, not appended
    assert "  # inline note" not in text            # the stale inline comment goes


def test_set_archive_dir_creates_a_paths_block_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("update:\n  check: true\n")
    cfg = Config.load(root)
    arc = tmp_path / "arc3"
    arc.mkdir()
    _run_set_archive_dir(cfg, arc)
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "paths:" in text and f"archive_dir: {arc}" in text
    assert Config.load(root).archive_dir == arc.resolve()


def test_set_archive_dir_migrates_a_legacy_dotenv_line(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, root = _make_cfg(tmp_path, ".")
    (root / ".env").write_text("PHA_ARCHIVE_DIR=/somewhere/old\n")
    arc = tmp_path / "arc4"
    arc.mkdir()
    _run_set_archive_dir(cfg, arc)
    env_text = (root / ".env").read_text(encoding="utf-8")
    assert "PHA_ARCHIVE_DIR" not in env_text
    assert "legacy" in capsys.readouterr().out


def test_env_still_overrides_config_yaml(tmp_path, monkeypatch):
    """An explicit PHA_ARCHIVE_DIR is a per-run override; config.yaml is the default."""
    from_cfg = tmp_path / "from-config"
    from_cfg.mkdir()
    from_env = tmp_path / "from-env"
    from_env.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {from_cfg}\n")
    assert Config.load(root).archive_dir == from_cfg.resolve()
    monkeypatch.setenv("PHA_ARCHIVE_DIR", str(from_env))
    assert Config.load(root).archive_dir == from_env.resolve()


# --- resolution is now loud when it matters (option 1) -----------------------

def test_notice_when_no_archive_is_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli._warn_resolved_archive(cfg)
    err = capsys.readouterr().err
    assert "archive:" in err and "default" in err
    assert "pha set archive-dir" in err


def test_no_notice_when_an_archive_is_configured_and_populated(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    arc = tmp_path / "real"
    arc.mkdir()
    cfg, root = _make_cfg(tmp_path, str(arc))
    conn = __import__("personal_historical_archive.db", fromlist=["db"]).connect(cfg.db_path)
    now = __import__("time").time()
    conn.execute(
        "INSERT INTO documents (filename, path, sha256, status, created_at, updated_at) "
        "VALUES ('a.pdf', ?, 'x', 'done', ?, ?)", (str(root / "a.pdf"), now, now))
    conn.commit()
    conn.close()
    cli._warn_resolved_archive(cfg)
    assert capsys.readouterr().err == ""


def test_warns_when_the_configured_archive_is_empty_but_another_exists(tmp_path, monkeypatch, capsys):
    """The stale-.env shape: configured archive empty, another archive.db nearby."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    empty = tmp_path / "wrong"
    empty.mkdir()
    cfg, root = _make_cfg(tmp_path, str(empty))
    # an archive database in the CWD's parent (where the user probably meant)
    (tmp_path / "archive.db").write_bytes(b"")
    monkeypatch.chdir(root)
    cli._warn_resolved_archive(cfg)
    err = capsys.readouterr().err
    assert "has no documents" in err and "pha info" in err


def test_dotenv_archive_dir_reports_the_legacy_source(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, root = _make_cfg(tmp_path, ".")
    (root / ".env").write_text("PHA_ARCHIVE_DIR=/legacy/place\n")
    assert cli._dotenv_archive_dir(cfg) == "/legacy/place"


def test_info_reports_where_the_archive_came_from(tmp_path, monkeypatch, capsys):
    import json as _json
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    arc = tmp_path / "cfg-arc"
    arc.mkdir()
    cfg, _ = _make_cfg(tmp_path, str(arc))
    cli.cmd_info(cfg, type("A", (), {"json": True})())
    data = _json.loads(capsys.readouterr().out)
    assert data["archive_source"] == "paths.archive_dir in config.yaml"
    assert data["filters"] == str(cfg.filters_dir)


def test_info_reports_the_env_source(tmp_path, monkeypatch, capsys):
    import json as _json
    arc = tmp_path / "cfg-arc"
    arc.mkdir()
    monkeypatch.setenv("PHA_ARCHIVE_DIR", str(arc))
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_info(cfg, type("A", (), {"json": True})())
    assert _json.loads(capsys.readouterr().out)["archive_source"].startswith("PHA_ARCHIVE_DIR env")


# ------------------------------------------- `pha help` finds the file it names
#
# A help topic prints a path and says "open this file". In a wheel install there
# is no checkout, so the path used to be a plausible lie (`<cwd>/MCP_CLIENTS.md`)
# — and HARNESS_INTRODUCTION.md was not in the distribution at all. The files are
# force-included under the package and resolved from wherever they exist.


def test_every_help_topic_has_a_real_file_in_the_checkout():
    """Each advertised topic must have something to open — a topic that points
    at nothing is worse than no topic."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(root=REPO_ROOT, archive_dir=REPO_ROOT / "no-archive-here")
    missing = []
    for key in cli._HELP_TOPICS:
        name = cli._HELP_DOCS[key][0]
        path, source = cli._help_doc_path(name, cfg)
        if not path.is_file():
            missing.append((key, name))
    assert not missing, f"help topics point at files that do not exist: {missing}"


def test_every_help_doc_is_shipped_in_the_wheel():
    """A wheel has no repo root, so `pha help <topic>` in a `uv tool install`
    only works if the build carries each file under the package."""
    import tomllib

    force = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    for key in cli._HELP_TOPICS:
        name = cli._HELP_DOCS[key][0]
        assert force.get(name) == f"personal_historical_archive/{name}", (
            f"{name} is a `pha help` topic but the wheel does not ship it")


def test_help_doc_prefers_the_archive_copy(tmp_path):
    """An archive's own README/PIPELINE is the copy matching the pha that made
    it (and the owner's if edited), so it wins over the checkout."""
    from types import SimpleNamespace

    arc = tmp_path / "archive"
    arc.mkdir()
    (arc / "PIPELINE.md").write_text("# the archive's own copy", encoding="utf-8")
    cfg = SimpleNamespace(root=REPO_ROOT, archive_dir=arc)

    path, source = cli._help_doc_path("PIPELINE.md", cfg)
    assert path == arc / "PIPELINE.md"
    assert source == "your archive"


def test_help_doc_falls_back_to_the_installed_package(tmp_path, monkeypatch):
    """No archive copy and no checkout (`uv tool install`): the packaged copy is
    what makes `pha help harness` work there."""
    import importlib.resources as res
    from types import SimpleNamespace

    packaged = tmp_path / "site-packages" / "personal_historical_archive"
    packaged.mkdir(parents=True)
    target = packaged / "HARNESS_INTRODUCTION.md"
    target.write_text("# how to get the harness", encoding="utf-8")

    class _Traversable:
        def __init__(self, base: Path):
            self.base = base

        def joinpath(self, *parts):
            return _Traversable(self.base.joinpath(*parts))

        def is_file(self):
            return self.base.is_file()

        def __str__(self):
            return str(self.base)

    monkeypatch.setattr(res, "files", lambda pkg=None: _Traversable(packaged))
    cfg = SimpleNamespace(root=tmp_path / "no-checkout", archive_dir=tmp_path / "no-archive")

    path, source = cli._help_doc_path("HARNESS_INTRODUCTION.md", cfg)
    assert path == target
    assert source == "the installed pha package"


def test_help_harness_topic_prints_a_path(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "harness"})())
    out = capsys.readouterr().out
    assert "HARNESS_INTRODUCTION.md" in out
    assert "DeepSeek Harness" in out


def test_help_view_topic_points_at_the_plugin_doc(tmp_path, capsys, monkeypatch):
    """The PHA view has its own topic — the GUI is not something a reader of the
    other topics would discover."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "view"})())
    out = capsys.readouterr().out
    assert "DSH_PLUGIN.md" in out
    assert "PHA view" in out


def test_help_says_when_a_doc_is_missing(tmp_path, capsys, monkeypatch):
    """When the file is in none of the three places, say so — do not print a
    path as if it were there."""
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "mcp"})())
    captured = capsys.readouterr()
    assert "MCP_CLIENTS.md" in captured.out
    assert "not present in this installation" in captured.err

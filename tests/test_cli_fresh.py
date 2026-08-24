from __future__ import annotations

import os

from personal_historical_archive import cli
from personal_historical_archive.config import Config


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
    # created ~/pha-home (fakehome/pha-home) and pointed pha at it in the root .env
    assert (home / "pha-home" / "archive.db").parent.is_dir()
    envp = cfg.root / ".env"
    assert envp.exists()
    assert "PHA_ARCHIVE_DIR=" in envp.read_text(encoding="utf-8")


def test_prompt_interactive_existing(tmp_path, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")

    class FakeStdin:
        def isatty(self):
            return True
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin())
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    set_calls = []
    monkeypatch.setattr(cli, "_set_env_in_dotenv",
                        lambda *a, **k: set_calls.append((a, k)))

    assert cli._prompt_archive_setup(cfg) is True
    assert set_calls, "_set_env_in_dotenv should have been called to point at an existing archive"


def test_help_overview_points_to_docs(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": None})())
    out = capsys.readouterr().out
    for f in ("README.md", "MCP_CLIENTS.md", "HISTORIANS_README.md", "AGENTS.md"):
        assert f in out
    assert "pha status" in out
    assert "pha set archive-dir" in out
    assert "pha-home" in out


def test_help_topic_prints_path(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "mcp"})())
    out = capsys.readouterr().out
    assert "MCP_CLIENTS.md" in out
    assert str(cfg.root / "MCP_CLIENTS.md") in out


def test_help_unknown_topic_stderr(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cfg, _ = _make_cfg(tmp_path, ".")
    cli.cmd_help(cfg, type("A", (), {"topic": "bogus"})())
    err = capsys.readouterr().err
    assert "unknown help topic: bogus" in err
    assert "known topics" in err

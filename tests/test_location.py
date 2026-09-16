"""Tests for the machine-local trace of where `pha` lives (location.py).

An archive is discovered by the TOOL (its config.yaml / .env), but an agent
dropped into the archive directory has no way back to the tool — `pha` may not
be on its PATH at all. These tests pin the reverse trace: `.pha/location.json`
+ `pha-location.md`, refreshed on every run, gitignored, and pointing at the
agent docs that make it discoverable.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from personal_historical_archive import cli, location
from personal_historical_archive.archive_init import (
    ARCHIVE_AGENTS_MD,
    ARCHIVE_GITIGNORE,
    ARCHIVE_README_MD,
)
from personal_historical_archive.config import Config
from personal_historical_archive.location import (
    LOCATION_JSON,
    LOCATION_MD,
    MANAGED_IGNORE_MARKER,
    PHA_DIRNAME,
    build_location,
    ensure_managed_ignores,
    render_markdown,
    write_location,
)


def _cfg(tmp_path, archive: str = "") -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {archive or (root / 'archive')}\n"
        "  dropbox: dropbox\n  inbox: inbox\n  library: library\n  renders: renders\n"
        "  notes: notes\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    return Config.load(root)


# --- the record -------------------------------------------------------------

def test_location_records_a_path_proof_fallback(tmp_path):
    """`sys.executable -m personal_historical_archive` is the command that keeps
    working when PATH is minimal, the shim is gone or the venv is not active."""
    cfg = _cfg(tmp_path)
    rec = build_location(cfg)
    pha = rec["pha"]

    assert pha["module"] == [pha["python"], "-m", "personal_historical_archive"]
    assert pha["python"].startswith("/")  # absolute: no PATH lookup needed
    assert pha["version"]
    # both env vars: the data root AND the project whose config.yaml is used,
    # so a recorded command does not depend on cwd or a machine-wide config
    assert pha["env"]["PHA_ARCHIVE_DIR"] == str(cfg.archive_dir)
    assert pha["env"]["PHA_HOME"] == str(cfg.root)
    assert rec["archive_dir"] == str(cfg.archive_dir)
    assert rec["hostname"]


def test_mcp_commands_are_client_shaped(tmp_path):
    """An MCP client config needs program + args + env, not a shell line."""
    cfg = _cfg(tmp_path)
    rec = build_location(cfg)

    for kind in ("stdio", "sse"):
        entry = rec["mcp"][kind]
        assert entry["command"].startswith("/")
        assert entry["env"]["PHA_ARCHIVE_DIR"] == str(cfg.archive_dir)
        assert entry["env"]["PHA_HOME"] == str(cfg.root)
    assert rec["mcp"]["stdio"]["args"][-1] == "mcp"
    assert rec["mcp"]["sse"]["args"][-7:] == [
        "mcp", "--transport", "sse", "--host", "127.0.0.1", "--port", "8000",
    ]
    assert rec["serve"]["args"][-1] == "serve"
    assert rec["serve"]["url"].startswith("http://")


def test_missing_console_script_falls_back_to_the_interpreter_form(tmp_path, monkeypatch):
    """No `pha` script on this machine is not a dead end — the module form is
    recorded in its place, so every command in the trace still works."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(location, "_pha_command", lambda: None)
    rec = build_location(cfg)

    assert rec["pha"]["command"] is None
    assert rec["mcp"]["stdio"]["command"] == rec["pha"]["python"]
    assert rec["mcp"]["stdio"]["args"][:2] == ["-m", "personal_historical_archive"]

    md = render_markdown(rec)
    assert f'{rec["pha"]["python"]} -m personal_historical_archive status' in md
    # the whole command must not be swallowed into one quoted word
    assert f'"{rec["pha"]["python"]} -m personal_historical_archive" status' not in md


# --- writing it into the archive -------------------------------------------

def test_write_location_creates_json_and_markdown(tmp_path):
    cfg = _cfg(tmp_path)
    rec = write_location(cfg)

    j = cfg.archive_dir / PHA_DIRNAME / LOCATION_JSON
    m = cfg.archive_dir / LOCATION_MD
    assert j.is_file() and m.is_file()
    assert json.loads(j.read_text(encoding="utf-8"))["archive_dir"] == str(cfg.archive_dir)

    text = m.read_text(encoding="utf-8")
    assert str(cfg.archive_dir) in text
    assert "personal_historical_archive" in text  # the interpreter fallback
    assert rec["pha"]["version"] in text
    assert "mcp" in text  # the MCP server is part of the trace


def test_ensure_dirs_leaves_the_trace_on_every_run(tmp_path):
    """The hook is ensure_dirs, so any pha command refreshes the trace."""
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    assert (cfg.archive_dir / LOCATION_MD).is_file()
    assert (cfg.archive_dir / PHA_DIRNAME / LOCATION_JSON).is_file()


def test_the_recorded_command_runs_in_a_bare_shell(tmp_path):
    """The point of the whole trace: an agent with NO PATH and the WRONG cwd can
    still run the archive. Execute what the record says, from a minimal env."""
    cfg = _cfg(tmp_path)
    rec = write_location(cfg)

    proc = subprocess.run(
        [*rec["pha"]["module"], "info", "--json"],
        capture_output=True, text=True, cwd="/",
        env={**rec["pha"]["env"], "PHA_NO_UPDATE_CHECK": "1"},  # note: no PATH
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["archive_dir"] == str(cfg.archive_dir)


def test_write_location_refreshes_instead_of_duplicating(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    (cfg.archive_dir / ".gitignore").write_text("# my own ignores\n*.secret\n", encoding="utf-8")

    write_location(cfg)
    write_location(cfg)

    gi = (cfg.archive_dir / ".gitignore").read_text(encoding="utf-8")
    assert "# my own ignores" in gi and "*.secret" in gi
    assert gi.count(".pha/") == 1
    assert gi.count(LOCATION_MD) == 1


def test_legacy_layout_writes_no_trace(tmp_path):
    """archive_dir == the project dir is the legacy single-dropbox layout: the
    repo's own README/AGENTS are the docs there, and no machine trace is added."""
    cfg = _cfg(tmp_path, archive=".")
    cfg.ensure_dirs()
    assert not (cfg.archive_dir / LOCATION_MD).exists()
    assert not (cfg.archive_dir / PHA_DIRNAME).exists()


# --- keeping the trace out of git ------------------------------------------

def test_gitignore_template_uses_the_managed_block():
    """A fresh archive must already carry the exact block ensure_managed_ignores
    looks for, or every run would append a duplicate."""
    assert f"{MANAGED_IGNORE_MARKER}\n{PHA_DIRNAME}/\n{LOCATION_MD}" in ARCHIVE_GITIGNORE


def test_gitignore_block_is_appended_and_customised_file_is_kept(tmp_path):
    arch = tmp_path / "arc"
    arch.mkdir()
    gi = arch / ".gitignore"
    gi.write_text("# custom rules\n*.secret", encoding="utf-8")  # no trailing newline

    assert ensure_managed_ignores(arch) is True
    text = gi.read_text(encoding="utf-8")
    assert "*.secret" in text and ".pha/" in text and LOCATION_MD in text
    assert ensure_managed_ignores(arch) is False  # idempotent
    assert gi.read_text(encoding="utf-8").count(".pha/") == 1


def test_gitignore_created_only_inside_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ensure_managed_ignores(plain) is False
    assert not (plain / ".gitignore").exists()

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert ensure_managed_ignores(repo) is True
    assert ".pha/" in (repo / ".gitignore").read_text(encoding="utf-8")


def test_archive_docs_point_agents_at_the_location_file():
    """The trace is only useful if the FIRST files an agent reads name it."""
    for text in (ARCHIVE_README_MD, ARCHIVE_AGENTS_MD):
        assert LOCATION_MD in text
        assert "personal_historical_archive" in text  # the PATH-proof fallback
    assert ".pha/" in ARCHIVE_GITIGNORE
    assert LOCATION_MD in ARCHIVE_GITIGNORE


# --- and inside pha itself --------------------------------------------------

def test_info_reports_where_pha_is(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    cli.cmd_info(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)

    assert data["pha_version"]
    assert "personal_historical_archive" in data["pha_module_command"]
    assert data["location_file"].endswith(LOCATION_MD)
    # discovery stays read-only: it must not create the archive's database
    assert not cfg.db_path.exists()


def test_info_human_output_shows_the_tool_location(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    cli.cmd_info(cfg, SimpleNamespace(json=False))
    out = capsys.readouterr().out
    assert "pha_command: " in out
    assert "location_file: " in out

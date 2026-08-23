"""Tests for pha init-archive (creating a new self-contained archive)."""

from __future__ import annotations

import pytest

from personal_historical_archive.archive_init import init_archive


def test_init_archive_creates_structure(tmp_path):
    p = tmp_path / "newarc"
    out = init_archive(p)
    assert out == p.resolve()
    for sub in ("dropbox/documents", "dropbox/collections", "library",
                "renders", "palaeographers", "editors", "encoders"):
        assert (p / sub).is_dir(), f"missing {sub}"
    # zero-config defaults seeded
    assert (p / "palaeographers" / "default.md").exists()
    assert (p / "editors" / "default.md").exists()
    assert (p / "encoders" / "default.md").exists()
    # agent guidance + gitignore
    agents = (p / "AGENTS.md").read_text(encoding="utf-8")
    assert "pha" in agents and "github.com" in agents
    gitignore = (p / ".gitignore").read_text(encoding="utf-8")
    assert "archive.db" in gitignore
    assert "renders/" in gitignore
    # user-facing data (dropbox/, library/, palaeographers/...) is KEPT: there
    # must be no ignore rule for them (only comments may mention them)
    rules = [l for l in gitignore.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    for kept in ("dropbox", "library", "palaeographers", "editors", "encoders"):
        assert not any(r.startswith(kept) for r in rules), f"ignored {kept}"


def test_init_archive_existing_empty_ok(tmp_path):
    p = tmp_path / "empty"
    p.mkdir()
    assert init_archive(p) == p.resolve()


def test_init_archive_nonempty_fails(tmp_path):
    p = tmp_path / "occupied"
    p.mkdir()
    (p / "file.txt").write_text("x")
    with pytest.raises(FileExistsError):
        init_archive(p)


def test_init_archive_existing_file_fails(tmp_path):
    p = tmp_path / "afile"
    p.write_text("x")
    with pytest.raises(NotADirectoryError):
        init_archive(p)

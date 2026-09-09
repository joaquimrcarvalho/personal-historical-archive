"""The archive's agent docs (README.md, AGENTS.md) must stay current with the
installed pha version: seeded at init-archive time, then refreshed into an
existing dedicated archive on every run — without clobbering a user's edits,
and without touching the project checkout's own docs.
"""

from __future__ import annotations

from personal_historical_archive import archive_init as ai
from personal_historical_archive.config import Config


def _stamped_current(doc: str, project_root=None) -> str:
    return ai._stamp(getattr(ai, doc))


def test_init_archive_stamps_the_docs(tmp_path):
    p = tmp_path / "arc"
    ai.init_archive(p)
    for name in ("README.md", "AGENTS.md"):
        text = (p / name).read_text(encoding="utf-8")
        assert text.startswith(ai._DOC_MARKER), f"{name} missing template marker"
        body, recorded = ai._split_marker(text)
        assert body is not None and ai._sha256(body) == recorded


def test_refresh_pristine_archive_is_a_noop(tmp_path):
    p = tmp_path / "arc"
    ai.init_archive(p)
    res = ai.refresh_archive_agent_docs(p)
    assert res == [("README.md", "kept"), ("AGENTS.md", "kept")]


def test_refresh_creates_missing_docs(tmp_path):
    p = tmp_path / "arc"
    p.mkdir()
    res = ai.refresh_archive_agent_docs(p)
    assert res == [("README.md", "created"), ("AGENTS.md", "created")]
    assert (p / "README.md").exists() and (p / "AGENTS.md").exists()


def test_refresh_migrates_legacy_unmarked_doc(tmp_path):
    """An archive created by an older pha (docs written WITHOUT the marker) is
    still refreshed, because the file begins like the template."""
    p = tmp_path / "arc"
    ai.init_archive(p)
    # strip the marker -> simulate an old generation
    for name, tmpl in (("README.md", ai.ARCHIVE_README_MD),
                       ("AGENTS.md", ai.ARCHIVE_AGENTS_MD)):
        (p / name).write_text(tmpl, encoding="utf-8")
    res = ai.refresh_archive_agent_docs(p)
    assert res == [("README.md", "updated"), ("AGENTS.md", "updated")]
    # now current -> subsequent reflections no-op
    assert ai.refresh_archive_agent_docs(p) == [
        ("README.md", "kept"), ("AGENTS.md", "kept")]


def test_refresh_preserves_user_edited_doc(tmp_path):
    """A doc that carries the marker but no longer matches the recorded hash
    (i.e. a human edited it) is left untouched."""
    p = tmp_path / "arc"
    ai.init_archive(p)
    custom = (ai._DOC_MARKER + "deadbeef" + ai._DOC_MARKER_END +
              "\n\n# My own AGENTS\ncustom\n")
    (p / "AGENTS.md").write_text(custom, encoding="utf-8")
    res = ai.refresh_archive_agent_docs(p)
    assert ("AGENTS.md", "kept") in res
    assert (p / "AGENTS.md").read_text(encoding="utf-8") == custom


def test_refresh_preserves_user_replaced_doc(tmp_path):
    """A doc with no marker and a different opening line is a user replacement."""
    p = tmp_path / "arc"
    ai.init_archive(p)
    (p / "AGENTS.md").write_text("# My own conventions\nstuff\n", encoding="utf-8")
    res = ai.refresh_archive_agent_docs(p)
    assert ("AGENTS.md", "kept") in res
    assert (p / "AGENTS.md").read_text(encoding="utf-8") == "# My own conventions\nstuff\n"


def test_ensure_dirs_refreshes_dedicated_archive_but_not_project_root(tmp_path):
    """ensure_dirs refreshes the docs in a real (dedicated) archive, but never
    creates/overwrites README.md / AGENTS.md at the project root in the legacy
    archive_dir == project-dir layout."""
    root = tmp_path / "proj"
    root.mkdir()
    arc = tmp_path / "arc"
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {arc}\n")
    cfg = Config.load(root)
    cfg.ensure_dirs()
    # dedicated archive got its agent docs seeded
    assert (arc / "README.md").exists() and (arc / "AGENTS.md").exists()
    # project-checkout docs are NOT auto-created (it may carry its own)
    assert not (root / "AGENTS.md").exists()
    assert not (root / "README.md").exists()

    # simulate an older pha's archive doc, then a later run refreshes it
    (arc / "AGENTS.md").write_text(ai.ARCHIVE_AGENTS_MD, encoding="utf-8")
    cfg.ensure_dirs()
    text = (arc / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith(ai._DOC_MARKER), "ensure_dirs should re-stamp a legacy doc"


def test_ensure_dirs_leaves_project_root_docs_in_place(tmp_path):
    """When archive_dir == project dir (the legacy single-dropbox layout), the
    repo's own README.md/AGENTS.md must never be replaced by the archive ones."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    cfg = Config.load(root)
    sentinel = "# my project's hand-written AGENTS"
    (root / "AGENTS.md").write_text(sentinel, encoding="utf-8")
    cfg.ensure_dirs()
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == sentinel

"""The archive's own `skills/` folder (the pha-specific agent skills).

An agent operating an archive may have pha installed with no access to the pha
source repository, so the skill bodies are embedded in the package and seeded
INTO the archive. These tests pin the three properties that matter: what is
seeded equals the authored `skills/*/SKILL.md` files byte for byte; seeding
happens once and never overwrites; and an archive that is not the project dir
gets the folder on the next pha run.
"""

from __future__ import annotations

from pathlib import Path

from personal_historical_archive import archive_skills as ask
from personal_historical_archive.archive_init import init_archive
from personal_historical_archive.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_SKILLS = REPO_ROOT / "skills"


def _repo_skills() -> dict[str, str]:
    return {
        p.parent.name: p.read_text(encoding="utf-8")
        for p in sorted(REPO_SKILLS.glob("*/SKILL.md"))
    }


def test_embedded_skills_match_the_authored_repo_files():
    """The embedded bodies are what a no-checkout archive is seeded with, so
    they must equal the authored `skills/*/SKILL.md` — byte for byte, and in the
    same set (a new repo skill that was not embedded would seed nothing)."""
    repo = _repo_skills()
    assert repo, "no skills/*/SKILL.md found in the repo"
    assert dict(ask.bundled_skills()) == repo, (
        "embedded skills drifted from skills/*/SKILL.md — re-embed them"
    )


def test_bundled_skills_are_ordered_deterministically():
    names = [name for name, _ in ask.bundled_skills()]
    assert names == sorted(names)


def test_skills_readme_documents_the_format_and_install():
    text = ask.SKILLS_README_MD
    for marker in ("SKILL.md", "name", "description", "~/.agents/skills/",
                   "once", "skills/<name>/SKILL.md"):
        assert marker in text, f"skills/README.md missing {marker!r}"
    # the committed copy (this repo is itself an archive) must not drift either
    committed = (REPO_SKILLS / "README.md").read_text(encoding="utf-8")
    assert committed == text, (
        "skills/README.md drifted from SKILLS_README_MD — re-embed it"
    )


def test_init_archive_seeds_the_skills_folder(tmp_path):
    p = tmp_path / "arc"
    init_archive(p)
    readme = p / "skills" / "README.md"
    assert readme.is_file(), "skills/README.md not seeded"
    assert "SKILL.md" in readme.read_text(encoding="utf-8")
    # every bundled skill is seeded, byte-identical to the repo's authored copy
    for name, body in ask.bundled_skills():
        seeded = (p / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert seeded == body, f"{name} seeded with different content"
        # front-matter name must match the folder (so cp -R installs it right)
        fm = seeded.split("---", 2)[1]
        assert f"name: {name}" in fm


def test_seeding_is_once_and_preserves_user_edits(tmp_path):
    p = tmp_path / "arc"
    init_archive(p)
    skill = p / "skills" / "pha-search-context" / "SKILL.md"
    skill.write_text("my own version\n", encoding="utf-8")
    mine = p / "skills" / "my-skill"
    mine.mkdir()
    (mine / "SKILL.md").write_text("mine\n", encoding="utf-8")
    readme = p / "skills" / "README.md"
    readme.write_text("my readme\n", encoding="utf-8")

    assert ask.seed_archive_skills(p) == []          # nothing left to create
    assert skill.read_text(encoding="utf-8") == "my own version\n"
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "mine\n"
    assert readme.read_text(encoding="utf-8") == "my readme\n"


def test_ensure_dirs_seeds_skills_into_a_dedicated_archive(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    arc = tmp_path / "arc"
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {arc}\n")
    Config.load(root).ensure_dirs()
    for name, body in ask.bundled_skills():
        seeded = (arc / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert seeded == body
    assert (arc / "skills" / "README.md").is_file()


def test_ensure_dirs_does_not_touch_the_project_root_skills(tmp_path):
    """In the legacy archive_dir == project-dir layout the repo's own skills/
    folder IS the source: pha must not write into it."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    Config.load(root).ensure_dirs()
    assert not (root / "skills").exists()

"""The archive's own `skills/` folder (the pha-specific agent skills).

An agent operating an archive may have pha installed with no access to the pha
source repository, so every file of every bundled skill is embedded in the
package and seeded INTO the archive. These tests pin the properties that
matter: what is seeded equals the authored `skills/<name>/` files byte for
byte; seeding never overwrites an existing file but creates missing ones; and a
skill added in a newer pha version reaches an existing archive on the next run.
"""

from __future__ import annotations

from pathlib import Path

from personal_historical_archive import archive_skills as ask
from personal_historical_archive.archive_init import init_archive
from personal_historical_archive.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_SKILLS = REPO_ROOT / "skills"


def _repo_skill_files() -> dict[str, dict[str, str]]:
    """Every file of every authored skill: name -> {relative path: text}."""
    skills: dict[str, dict[str, str]] = {}
    for skill_dir in sorted(p for p in REPO_SKILLS.iterdir() if p.is_dir()):
        files: dict[str, str] = {}
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and path.name != ".DS_Store":
                rel = path.relative_to(skill_dir).as_posix()
                files[rel] = path.read_text(encoding="utf-8")
        if files:
            skills[skill_dir.name] = files
    return skills


def _embedded_skill_files() -> dict[str, dict[str, str]]:
    return {name: dict(files) for name, files in ask.bundled_skill_files()}


def test_embedded_skill_files_match_the_authored_repo_files():
    repo = _repo_skill_files()
    assert repo, "no skill files found in the repo"
    assert _embedded_skill_files() == repo, "embedded skills drifted - re-embed"


def test_bundled_skills_are_ordered_deterministically():
    names = [name for name, _ in ask.bundled_skills()]
    assert names == sorted(names)
    file_names = [name for name, _ in ask.bundled_skill_files()]
    assert file_names == names
    for name, files in ask.bundled_skill_files():
        rel_paths = [rel for rel, _ in files]
        assert rel_paths[0] == "SKILL.md"
        assert len(rel_paths) == len(set(rel_paths))


def test_skills_readme_documents_the_format_and_install():
    text = ask.SKILLS_README_MD
    for marker in ("SKILL.md", "name", "description", "skills/<name>/SKILL.md"):
        assert marker in text
    committed = (REPO_SKILLS / "README.md").read_text(encoding="utf-8")
    assert committed == text, "skills/README.md drifted - re-embed it"


def test_init_archive_seeds_the_skills_folder(tmp_path):
    p = tmp_path / "arc"
    init_archive(p)
    readme = p / "skills" / "README.md"
    assert readme.is_file()
    assert "SKILL.md" in readme.read_text(encoding="utf-8")
    for name, files in ask.bundled_skill_files():
        for rel, body in files:
            seeded_path = p / "skills" / name / rel
            seeded = seeded_path.read_text(encoding="utf-8")
            assert seeded == body
            if rel.endswith(".py"):
                compile(seeded, str(seeded_path), "exec")
        skill_md = (p / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        fm = skill_md.split("---", 2)[1]
        assert f"name: {name}" in fm


def test_seeding_is_once_and_preserves_user_edits(tmp_path):
    p = tmp_path / "arc"
    init_archive(p)
    skill = p / "skills" / "pha-search-context" / "SKILL.md"
    skill.write_text("my own version\n", encoding="utf-8")
    script = p / "skills" / "palaeographers-compare" / "scripts" / "verify_comparison.py"
    script.write_text("print('mine')\n", encoding="utf-8")
    mine = p / "skills" / "my-skill"
    mine.mkdir()
    (mine / "SKILL.md").write_text("mine\n", encoding="utf-8")
    readme = p / "skills" / "README.md"
    readme.write_text("my readme\n", encoding="utf-8")

    assert ask.seed_archive_skills(p) == []
    assert skill.read_text(encoding="utf-8") == "my own version\n"
    assert script.read_text(encoding="utf-8") == "print('mine')\n"
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "mine\n"
    assert readme.read_text(encoding="utf-8") == "my readme\n"


def test_a_missing_bundled_file_is_reseeded(tmp_path):
    p = tmp_path / "arc"
    init_archive(p)
    script = p / "skills" / "palaeographers-compare" / "scripts" / "verify_comparison.py"
    body = script.read_text(encoding="utf-8")
    script.unlink()
    assert ask.seed_archive_skills(p) == [
        "palaeographers-compare/scripts/verify_comparison.py"
    ]
    assert script.read_text(encoding="utf-8") == body


def test_a_newly_bundled_skill_reaches_an_existing_archive(tmp_path, monkeypatch):
    p = tmp_path / "arc"
    init_archive(p)
    edited = p / "skills" / "pha-search-context" / "SKILL.md"
    edited.write_text("my own version\n", encoding="utf-8")

    new_skill = "---\nname: pha-new-skill\ndescription: test\n---\n\n# New\n"
    monkeypatch.setattr(ask, "SKILLS", ask.SKILLS + (("pha-new-skill", new_skill),))

    created = ask.seed_archive_skills(p)
    assert created == ["pha-new-skill/SKILL.md"]
    seeded = (p / "skills" / "pha-new-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert seeded == new_skill
    assert edited.read_text(encoding="utf-8") == "my own version\n"


def test_ensure_dirs_seeds_skills_into_a_dedicated_archive(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    arc = tmp_path / "arc"
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {arc}\n")
    Config.load(root).ensure_dirs()
    for name, files in ask.bundled_skill_files():
        for rel, body in files:
            seeded = (arc / "skills" / name / rel).read_text(encoding="utf-8")
            assert seeded == body
    assert (arc / "skills" / "README.md").is_file()


def test_ensure_dirs_does_not_touch_the_project_root_skills(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    Config.load(root).ensure_dirs()
    assert not (root / "skills").exists()

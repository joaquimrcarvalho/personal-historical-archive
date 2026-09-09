"""Validate the bundled agent skills (skills/*/SKILL.md).

These are the reusable instruction files an agent installs (to ~/.agents/skills/)
to operate a pha archive without reading the source. Each skill's front matter
`name` must match its folder name (so `cp -R` installs it correctly), and the
document-operations skill must carry the re-run-one-document guidance an agent
needs when working only from an archive directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = REPO_ROOT / "skills"

EXPECTED = {
    "pha-search-context": ["pha page", "pha_get_page", "variant", "edited"],
    "pha-document-operations": [
        "--path",
        "--reprocess",
        "pha edit --path collections/COLX --page 3",
        "pha test collections/COLX --pages 3",
        "pha status",
        "pha page",
        "single-model lock",
        "pha_job_start",
        "pha_scan_now",
        "dropbox-relative",
    ],
}


def _read_skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def test_expected_skills_present():
    for name in EXPECTED:
        assert (SKILLS / name / "SKILL.md").is_file(), f"missing skill {name}"


def test_skill_frontmatter_name_matches_folder():
    for name in EXPECTED:
        text = _read_skill(name)
        # front matter between the two leading --- lines
        fm = text.split("---", 2)[1]
        assert f"name: {name}" in fm, (
            f"{name}/SKILL.md front matter name must match the folder name"
        )


@pytest.mark.parametrize("name, markers", EXPECTED.items())
def test_skill_carries_required_guidance(name, markers):
    text = _read_skill(name)
    for m in markers:
        assert m in text, f"{name}/SKILL.md missing {m!r}"

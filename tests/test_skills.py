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
    "lmstudio-model-locality": [
        "LM Link",
        "pha scan",
        "pha edit",
        "lms ls --json",
        "lms ps --json",
        "lms link status --json",
        "deviceIdentifier",
        "local_available_not_loaded",
        "lms load",
        "model:",
        "base_url",
        "server:",
        "scripts/lmstudio_locality.py",
        "--require-local",
        "lms link disable",
    ],
    "pha-document-operations": [
        "--path",
        "--reprocess",
        "pha edit --path collections/COLX --page 3",
        "pha test collections/COLX --pages 3",
        "pha status",
        "pha page",
        "model-server lock",
        "pha_job_start",
        "pha_scan_now",
        "dropbox-relative",
    ],
    "pha-zotero-bibliography": [
        # the trigger the description has to carry
        "exported from Zotero",
        # the three sidecar formats, and the one that wins
        ".dc.json",
        ".mods.xml",
        ".bib",
        "JSON wins",
        # route A: the Zotero local API
        "http://localhost:23119/api/users/0/items/<KEY>?format=mods",
        "<note>",
        "Zotero-Server-ID",
        # the trap: ?itemID= is silently ignored, not a filter
        "NOT a query filter",
        # route B: the RDF export package
        "bib:Memo",
        "z:Attachment",
        # writing and verifying the sidecar
        "pha bib <doc> --to-json --write",
        "to_dc_json_text",
        "pha bib --check",
        "pha cite <doc> <page>",
        # the anti-hallucination rules
        "fetched-from-zotero-unverified",
        "agent-drafted-unverified",
        "human-confirmed",
        "Never invent",
        "Fix the source in Zotero",
        # presence-only, and metadata never re-transcribes
        "no inheritance",
        "never re-transcribes a document",
    ],    "palaeographers-compare": [
        # trigger language in the description
        "compare or collate transcriptions",
        "compare palaeographers or transcription models",
        # the uniform comparative edition
        "uniform comparative edition",
        "## Entry-by-entry comparison",
        "## Key differences on this page",
        "= all readings:",
        "overview.md",
        # the bundled helper scripts and the reference variant
        "scripts/normalize_comparison.py",
        "scripts/verify_comparison.py",
        "scripts/make_reference.py",
        "human/",
        "images/",
        "--require-images",
        "never harmonise",
        "verbatim",
    ],
    "obsidian-vault": [
        # the trigger: vault notes and archive-derived sync
        "Obsidian",
        "[[wikilinks]]",
        "archive_source",
        "archive_source_sha256",
        # the stale check/stamp helper
        "scripts/archive_note_sync.py",
        "UP_TO_DATE",
        "STALE",
        "MISSING_SOURCE",
        # machine-specific paths are configurable, not hardcoded
        "OBSIDIAN_VAULT",
        "PHA_ARCHIVE_DIR",
    ],
    "timelink-kleio-provenance": [
        # the model facts
        "attributes.id = entities.id",
        "sources.kleiofile",
        "attr_id",
        # the citation and helper
        "vscode://file/",
        "timelink_provenance.py",
        "china_coimbra",
        "MHK_HOME",
        # never cite the markdown export as the source
        "Never cite the Obsidian",
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


EXTRA_FILES = {
    "lmstudio-model-locality": ("scripts/lmstudio_locality.py",),
    "palaeographers-compare": (
        "examples/entry-format.md",
        "scripts/make_reference.py",
        "scripts/normalize_comparison.py",
        "scripts/verify_comparison.py",
    ),
    "obsidian-vault": ("scripts/archive_note_sync.py",),
    "timelink-kleio-provenance": ("timelink_provenance.py",),
}


@pytest.mark.parametrize("name", sorted(EXTRA_FILES))
def test_bundled_skill_extra_files_exist_and_compile(name):
    root = SKILLS / name
    for rel in EXTRA_FILES[name]:
        path = root / rel
        assert path.is_file(), f"missing {name}/{rel}"
        text = path.read_text(encoding="utf-8")
        assert text.strip()
        if rel.endswith(".py"):
            compile(text, str(path), "exec")


def test_bundled_skills_do_not_hardcode_personal_paths():
    """Bundled skills are shipped to other machines: machine-specific paths go
    through environment variables or script flags, never into the files."""
    for skill_dir in sorted(p for p in SKILLS.iterdir() if p.is_dir()):
        for path in sorted(skill_dir.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            assert "/Users/jrc" not in text, f"personal path in {path}"
            assert "~jrc" not in text, f"personal home in {path}"

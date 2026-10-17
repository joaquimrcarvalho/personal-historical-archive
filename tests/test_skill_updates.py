"""Updating the bundled skills without losing the archive owner's edits.

Two requirements pull in opposite directions and both matter:

* a skill is guidance an agent obeys, so pha must be able to improve one and
  have the improvement reach an existing archive;
* the skills folder is the owner's (they are told to edit and extend it), so an
  edited file must never be replaced behind their back.

The compromise these tests pin: untouched files are refreshed on update, edited
ones are reported and kept, and `--replace` is the explicit opt-in that backs
the local copy up first.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from personal_historical_archive import archive_skills as ask
from personal_historical_archive import cli
from personal_historical_archive.archive_init import init_archive
from personal_historical_archive.config import Config

SKILL = "pha-search-context/SKILL.md"


def _archive(tmp_path):
    p = tmp_path / "arc"
    init_archive(p)
    return p


def _body(p, rel=SKILL):
    return ask.unstamp_skill(rel, (p / "skills" / rel).read_text(encoding="utf-8"))[0]


def _newer(monkeypatch, body="---\nname: pha-search-context\ndescription: newer\n---\n\n# New\n"):
    """Publish a newer bundled version of one skill."""
    skills = tuple(
        (name, body if name == "pha-search-context" else text)
        for name, text in ask.SKILLS
    )
    monkeypatch.setattr(ask, "SKILLS", skills)
    return body


def test_an_untouched_skill_is_refreshed_on_update(tmp_path, monkeypatch):
    p = _archive(tmp_path)
    newer = _newer(monkeypatch)

    # A plain command stamps the file but does NOT replace outdated text.
    actions = dict(ask.seed_archive_skills(p))
    assert actions[SKILL] == "kept"
    assert _body(p) != ask._normalise(newer)

    report = ask.apply_skill_updates(p)
    assert report["updated"] == [SKILL]
    assert _body(p) == ask._normalise(newer)
    assert ask._file_state(SKILL, (p / "skills" / SKILL).read_text(), newer) == "current"


def _edit_in_place(p, rel=SKILL, marker="## Workflow", replacement="## My workflow"):
    """Edit the body of a seeded skill the way an owner would: the stamp stays."""
    path = p / "skills" / rel
    text = path.read_text(encoding="utf-8")
    assert marker in text, "the fixture phrase must exist in the skill"
    path.write_text(text.replace(marker, replacement, 1), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def test_an_edited_skill_is_kept_and_reported(tmp_path, monkeypatch):
    p = _archive(tmp_path)
    mine = _edit_in_place(p)
    _newer(monkeypatch)

    report = ask.apply_skill_updates(p)
    assert SKILL in report["kept"] and report["replaced"] == []
    path = p / "skills" / SKILL
    assert path.read_text(encoding="utf-8") == mine, "the owner's text must survive"

    summary = ask.skill_update_summary(p)
    assert summary["modified"] == 1
    assert SKILL in summary["paths"]


def test_status_distinguishes_modified_from_unverified(tmp_path, monkeypatch):
    p = _archive(tmp_path)
    _newer(monkeypatch)
    # edited after pha wrote it -> modified
    _edit_in_place(p)
    # a pre-stamp file that differs from the bundle -> unverified, not "modified"
    script = p / "skills" / "palaeographers-compare" / "scripts" / "verify_comparison.py"
    assert ask.unstamp_skill("scripts/verify_comparison.py",
                             script.read_text(encoding="utf-8"))[1] is not None
    script.write_text("#!/usr/bin/env python3\nprint('old')\n", encoding="utf-8")

    states = {s["path"]: s["state"] for s in ask.skill_states(p)}
    assert states[SKILL] == "modified"
    assert states["palaeographers-compare/scripts/verify_comparison.py"] == "unverified"


def test_a_matching_stamp_means_the_file_is_safe_to_update(tmp_path, monkeypatch):
    """A stamp that still matches the body proves pha wrote it and nobody
    touched it — so a newer bundle is applied, not reported as a local edit."""
    p = _archive(tmp_path)
    _newer(monkeypatch)
    path = p / "skills" / SKILL
    # the stamp still matches the body (pha wrote it), the bundle has moved on
    assert ask._file_state(SKILL, path.read_text(), ask.bundled_skill_map()[SKILL]) == "update"
    report = ask.apply_skill_updates(p)
    assert SKILL in report["updated"]


def test_replace_takes_the_bundled_version_after_backing_up(tmp_path, monkeypatch):
    p = _archive(tmp_path)
    mine = _edit_in_place(p)
    newer = _newer(monkeypatch)

    report = ask.apply_skill_updates(p, replace=True)
    assert report["replaced"] == [SKILL]
    assert _body(p) == ask._normalise(newer)

    backup = report["backup"]
    assert backup and ".pha/skills-backup/" in backup
    saved = (tmp_path / "arc" / ".pha" / "skills-backup")
    assert saved.is_dir()
    copies = list(saved.rglob("SKILL.md"))
    assert len(copies) == 1
    assert copies[0].read_text(encoding="utf-8") == mine, "the local version is recoverable"


def test_replace_can_be_limited_to_one_skill(tmp_path, monkeypatch):
    p = _archive(tmp_path)
    other = "pha-document-operations/SKILL.md"
    for rel in (SKILL, other):
        _edit_in_place(p, rel, marker="## " + ("Workflow" if rel == SKILL else "Before you start"),
                       replacement="## Mine")
    _newer(monkeypatch)
    # make the second skill differ in the bundle too
    skills = tuple(
        (name, "---\nname: pha-document-operations\ndescription: newer\n---\n\n# New\n"
         if name == "pha-document-operations" else text)
        for name, text in ask.SKILLS
    )
    monkeypatch.setattr(ask, "SKILLS", skills)

    report = ask.apply_skill_updates(p, replace=True, only=["pha-search-context"])
    assert report["replaced"] == [SKILL]
    assert other in report["kept"]
    assert "mine" in (p / "skills" / other).read_text(encoding="utf-8")


def test_a_deleted_bundled_skill_is_recreated(tmp_path):
    p = _archive(tmp_path)
    path = p / "skills" / SKILL
    path.unlink()
    report = ask.apply_skill_updates(p)
    assert SKILL in report["created"]
    assert path.is_file()


def test_owner_added_skills_are_never_indexed_or_touched(tmp_path, monkeypatch):
    p = _archive(tmp_path)
    mine = p / "skills" / "my-skill"
    mine.mkdir()
    (mine / "SKILL.md").write_text("mine\n", encoding="utf-8")
    _newer(monkeypatch)
    ask.apply_skill_updates(p, replace=True)
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "mine\n"
    assert all(s["path"] != "my-skill/SKILL.md" for s in ask.skill_states(p))


def test_python_helpers_stay_executable_after_stamping(tmp_path):
    p = _archive(tmp_path)
    rel = "palaeographers-compare/scripts/verify_comparison.py"
    text = (p / "skills" / rel).read_text(encoding="utf-8")
    body = ask.unstamp_skill(rel, text)[0]
    compile(body, rel, "exec")
    assert body.startswith("#!"), "the shebang line must remain the first line"


# --------------------------------------------------------------- surfaces

def _cfg(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    arc = tmp_path / "arc"
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {arc}\n")
    cfg = Config.load(root)
    cfg.ensure_dirs()
    return cfg


def test_cli_skills_status_and_update(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    p = cfg.archive_dir
    _edit_in_place(p)
    _newer(monkeypatch)

    cli.cmd_skills(cfg, SimpleNamespace(skills_cmd=None, json=True))
    states = {s["path"]: s["state"] for s in json.loads(capsys.readouterr().out)}
    assert states[SKILL] == "modified"

    cli.cmd_skills(cfg, SimpleNamespace(skills_cmd="update", replace=False, skill=None,
                                        json=True))
    report = json.loads(capsys.readouterr().out)
    assert report["replaced"] == [] and SKILL in report["kept"]

    cli.cmd_skills(cfg, SimpleNamespace(skills_cmd="update", replace=True, skill=None,
                                        json=True))
    report = json.loads(capsys.readouterr().out)
    assert report["replaced"] == [SKILL]
    assert "mine" not in (p / "skills" / SKILL).read_text(encoding="utf-8")


def test_cli_skills_text_output_names_the_kept_file(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    _edit_in_place(cfg.archive_dir)
    _newer(monkeypatch)

    cli.cmd_skills(cfg, SimpleNamespace(skills_cmd="update", replace=False, skill=None,
                                        json=False))
    out = capsys.readouterr().out
    assert "kept because they were edited here" in out
    assert SKILL in out
    assert "--replace" in out


def test_status_reports_pending_skill_updates(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    _newer(monkeypatch)
    cli.cmd_status(cfg, SimpleNamespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["skills"]["updates"] >= 1


def test_text_appended_after_the_stamp_counts_as_an_edit(tmp_path, monkeypatch):
    """The stamp is at the end of the file, so appending is the easiest edit to
    make — and the easiest one to lose if it were read as pristine."""
    p = _archive(tmp_path)
    path = p / "skills" / SKILL
    path.write_text(path.read_text(encoding="utf-8") + "\nMy own note.\n", encoding="utf-8")
    mine = path.read_text(encoding="utf-8")
    _newer(monkeypatch)

    assert ask.skill_update_summary(p)["modified"] == 1
    report = ask.apply_skill_updates(p)
    assert SKILL in report["kept"]
    assert path.read_text(encoding="utf-8") == mine


def test_update_flags_a_locally_edited_skill_through_a_fresh_process(tmp_path):
    """`pha update` runs the NEW code in a child process (the running one holds
    the old package), and that child must name the file it did not touch."""
    cfg = _cfg(tmp_path)
    p = cfg.archive_dir
    _edit_in_place(p)

    msg = cli._skills_after_update(str(p))
    assert "edited in this archive" in msg
    assert SKILL in msg
    assert "pha skills status" in msg
    # the owner's text is still there
    assert "my workflow" in (p / "skills" / SKILL).read_text(encoding="utf-8").lower()


def test_update_message_reports_what_was_applied(tmp_path, monkeypatch):
    """The wording for the applied-updates path, without depending on a real
    bundled-text change (subprocess stubbed)."""
    import subprocess

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "created": [], "stamped": [], "updated": ["pha-search-context/SKILL.md"],
            "replaced": [], "current": [], "kept": [], "backup": None,
        })

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    msg = cli._skills_after_update(str(tmp_path))
    assert "1 updated" in msg
    assert "edited" not in msg


def test_skills_command_is_inert_in_the_source_checkout(tmp_path, capsys):
    """The repo's `skills/` is the authored source: `pha skills update` there
    would stamp pha's own payload (and break the embedded-copy drift test)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config.yaml").write_text("paths:\n  archive_dir: .\n")
    (root / "skills").mkdir()
    sample = root / "skills" / "x"
    sample.mkdir()
    (sample / "SKILL.md").write_text("---\nname: x\n---\n\nbody\n", encoding="utf-8")
    cfg = Config.load(root)

    cli.cmd_skills(cfg, SimpleNamespace(skills_cmd="update", replace=True, skill=[],
                                        json=False))
    assert "source" in capsys.readouterr().out
    assert (sample / "SKILL.md").read_text(encoding="utf-8") == "---\nname: x\n---\n\nbody\n"

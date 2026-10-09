from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive import release_notes

ROOT = Path(__file__).resolve().parents[1]


def _fixture():
    return {
        "schema": 1,
        "releases": {
            "0.41.1": {
                "date": "2026-10-06",
                "summary": "Docs and skill index",
                "changes": [
                    {"type": "docs", "scope": "help", "text": "point at PIPELINE.md"},
                ],
            },
            "0.42.0": {
                "date": "2026-10-08",
                "summary": "Structure registers",
                "changes": [
                    {"type": "feat", "scope": "encode", "text": "resolve @structure"},
                    {"type": "fix", "scope": "encode", "text": "refuse missing register"},
                ],
            },
        },
    }


def _load_generator():
    path = ROOT / "scripts" / "generate_changelog.py"
    spec = importlib.util.spec_from_file_location("generate_changelog", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_packaged_changelog_is_valid_and_has_a_release():
    data = release_notes.load_changelog()
    assert data["schema"] == 1
    assert data["releases"]
    latest = max(data["releases"], key=release_notes.version_key)
    assert data["releases"][latest].get("changes") or data["releases"][latest].get("summary")


def test_select_releases_modes():
    data = _fixture()
    assert release_notes.select_releases(data) == ["0.42.0"]
    assert release_notes.select_releases(data, version="0.41.1") == ["0.41.1"]
    assert release_notes.select_releases(data, since="0.41.1") == ["0.42.0"]
    assert release_notes.select_releases(data, all_releases=True) == ["0.41.1", "0.42.0"]


def test_select_releases_unknown_version_raises():
    with pytest.raises(release_notes.ReleaseNotesError):
        release_notes.select_releases(_fixture(), version="9.9.9")


def test_render_groups_changes_by_type():
    text = release_notes.render(_fixture(), ["0.42.0"])
    assert "pha 0.42.0" in text
    assert "Structure registers" in text
    assert "Features:" in text
    assert "Fixes:" in text
    assert "resolve @structure" in text
    assert "refuse missing register" in text


def test_generator_parses_conventional_and_plain_subjects():
    gen = _load_generator()
    assert gen.parse_subject("feat(search): add field filters") == {
        "type": "feat", "scope": "search", "text": "add field filters",
    }
    assert gen.parse_subject("plain change") == {
        "type": "other", "scope": "", "text": "plain change",
    }


def test_generator_entry_from_git_log_skips_release_commits():
    gen = _load_generator()
    raw = ("abc123\x1ffeat(encode): add register\x1fbody\x1e"
           "def456\x1frelease: 0.42.0\x1fbody\x1e"
           "ghi789\x1ffix(encode): refuse missing register\x1fbody\x1e")
    entry = gen.entry_from_git_log(
        raw, version="0.42.0", summary="Registers", today="2026-10-08",
    )
    assert entry["summary"] == "Registers"
    assert [c["text"] for c in entry["changes"]] == [
        "add register", "refuse missing register",
    ]
    assert all(c["commit"] for c in entry["changes"])


def test_cmd_whatsnew_renders_and_json(monkeypatch, capsys):
    monkeypatch.setattr(release_notes, "load_changelog", lambda: _fixture())
    args = SimpleNamespace(version="0.42.0", since=None, all_releases=False, json=False)
    cli.cmd_whatsnew(SimpleNamespace(), args)
    out = capsys.readouterr().out
    assert "pha 0.42.0" in out
    assert "Features:" in out

    args.json = True
    cli.cmd_whatsnew(SimpleNamespace(), args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    assert payload["versions"] == ["0.42.0"]

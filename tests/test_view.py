"""`pha view` — install/update the dsh-pha plugin in DeepSeek Harness profiles."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from personal_historical_archive import view


def _payload(tmp_path: Path) -> Path:
    root = tmp_path / "payload"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "index.js").write_text("export {}\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": view.PLUGIN_PACKAGE, "version": "0.36.1"}),
        encoding="utf-8",
    )
    return root


def _profile(tmp_path: Path, name: str = "web", patch: str | None = None) -> tuple[Path, Path]:
    home = tmp_path / "dsh"
    prof = home / "profiles" / name
    prof.mkdir(parents=True)
    if patch is not None:
        (prof / "cordis.patch.yml").write_text(patch, encoding="utf-8")
    return home, prof


def _row(patch: Path) -> dict:
    data = yaml.safe_load(patch.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    for entry in data:
        for row in (entry.get("insert") or []):
            if row.get("id") == "dsh-pha":
                return row
    raise AssertionError("dsh-pha row not found")


def test_install_creates_row_and_symlink(tmp_path, monkeypatch):
    payload = _payload(tmp_path)
    monkeypatch.setattr(view, "plugin_payload", lambda: payload)
    home, prof = _profile(tmp_path)

    report = view.install(
        profile="web",
        project_root="/repo",
        archive_dir="/archive",
        home=home,
    )

    assert report["version"] == "0.36.1"
    assert report["restart_required"] is True
    link = view.profile_plugin_link(prof)
    assert link.is_symlink()
    assert link.resolve() == payload.resolve()
    row = _row(view.profile_patch_path(prof))
    assert row["config"]["projectRoot"] == "/repo"
    assert row["config"]["archiveDir"] == "/archive"


def test_install_replaces_empty_flow_patch(tmp_path, monkeypatch):
    payload = _payload(tmp_path)
    monkeypatch.setattr(view, "plugin_payload", lambda: payload)
    home, prof = _profile(tmp_path, patch="[]\n")

    view.install(profile="web", project_root="/repo", home=home)

    row = _row(view.profile_patch_path(prof))
    assert row["name"] == view.PLUGIN_PACKAGE
    assert row["config"]["projectRoot"] == "/repo"


def test_install_updates_existing_row_and_preserves_comments(tmp_path, monkeypatch):
    payload = _payload(tmp_path)
    monkeypatch.setattr(view, "plugin_payload", lambda: payload)
    patch = (
        "# keep this comment\n"
        "- insert:\n"
        "    - id: other-plugin\n"
        "      name: '@example/other'\n"
        "- insert:\n"
        "    - id: dsh-pha\n"
        "      name: '@personal-historical-archive/dsh-pha'\n"
    )
    home, prof = _profile(tmp_path, patch=patch)

    view.install(profile="web", archive_dir="/archive", home=home)

    text = view.profile_patch_path(prof).read_text(encoding="utf-8")
    assert "# keep this comment" in text
    row = _row(view.profile_patch_path(prof))
    assert row["config"]["archiveDir"] == "/archive"
    assert "projectRoot" not in row.get("config", {})


def test_install_refuses_foreign_directory(tmp_path, monkeypatch):
    payload = _payload(tmp_path)
    monkeypatch.setattr(view, "plugin_payload", lambda: payload)
    home, prof = _profile(tmp_path)
    foreign = view.profile_plugin_link(prof)
    foreign.mkdir(parents=True)
    (foreign / "package.json").write_text(
        json.dumps({"name": "@example/not-pha"}),
        encoding="utf-8",
    )

    with pytest.raises(view.ViewError):
        view.install(profile="web", home=home)


def test_status_reports_linked_profile(tmp_path, monkeypatch):
    payload = _payload(tmp_path)
    monkeypatch.setattr(view, "plugin_payload", lambda: payload)
    home, prof = _profile(tmp_path)
    view.install(profile="web", project_root="/repo", archive_dir="/archive", home=home)

    report = view.status(profile="web", home=home)

    assert report["ok"] is True
    assert report["version"] == "0.36.1"
    entry = report["profiles"][0]
    assert entry["profile"] == "web"
    assert entry["linked"] is True
    assert entry["projectRoot"] == "/repo"
    assert entry["archiveDir"] == "/archive"


def test_install_without_profiles_skips(tmp_path, monkeypatch):
    payload = _payload(tmp_path)
    monkeypatch.setattr(view, "plugin_payload", lambda: payload)
    home = tmp_path / "empty-dsh"

    report = view.install(home=home)

    assert report["ok"] is True
    assert report["skipped"]
    assert report["profiles"] == []

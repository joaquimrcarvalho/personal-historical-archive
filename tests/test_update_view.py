"""`pha update` must also run the PHA view plugin step."""
from __future__ import annotations

from types import SimpleNamespace

from personal_historical_archive import cli, update, view


def test_cmd_update_runs_view_install_after_tool_update(tmp_path, monkeypatch, capsys):
    calls = {}

    monkeypatch.setattr(
        update,
        "check",
        lambda *args, **kwargs: {
            "current": "0.36.0",
            "latest": "0.36.1",
            "remote_source": "repo@main",
            "update_available": True,
        },
    )

    def fake_install_update(repo, branch):
        calls["tool"] = (repo, branch)
        return "updated pha tool"

    def fake_view_install(archive_dir=None):
        calls["view_archive"] = archive_dir
        return "view plugin updated"

    monkeypatch.setattr(update, "install_update", fake_install_update)
    monkeypatch.setattr(view, "install_after_tool_update", fake_view_install)

    archive = tmp_path / "archive"
    cfg = SimpleNamespace(
        root=tmp_path,
        update_repo="repo",
        update_branch="main",
        update_timeout=5,
        archive_source_kind="env",
        archive_dir=archive,
        db_path=archive / "archive.db",
    )
    args = SimpleNamespace(check=False, yes=True)

    cli.cmd_update(cfg, args)

    assert calls["tool"] == ("repo", "main")
    assert calls["view_archive"] == str(archive)
    out = capsys.readouterr().out
    assert "updated pha tool" in out
    assert "view plugin updated" in out

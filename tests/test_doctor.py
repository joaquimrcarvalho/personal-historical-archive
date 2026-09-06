from __future__ import annotations

import subprocess

import pytest

from personal_historical_archive import doctor


def _patch(monkeypatch, paths=None, on_path=None, runner=None):
    """Patch doctor's binary resolution and subprocess.run.

    paths:  mapping binary -> resolved path (or missing/None = not found).
    on_path: mapping binary -> bool; whether that binary is on pha's own PATH.
            Defaults to True for every found binary (found on PATH).
    runner: the subprocess.run replacement.
    """
    paths = paths or {}
    if on_path is None:
        on_path = {b: p is not None for b, p in paths.items()}
    monkeypatch.setattr(doctor, "find_engine_binary", lambda binary: paths.get(binary))
    monkeypatch.setattr(
        doctor.shutil, "which",
        lambda binary, **kw: ("/p/" + binary) if on_path.get(binary) else None,
    )
    if runner is not None:
        monkeypatch.setattr(doctor.subprocess, "run", runner)


def _runner(version_outputs=None, default_rc=0):
    """A subprocess.run replacement keyed by (cmd[0], cmd[1]).

    version_outputs: {(binary, flag): str} -> that string is the stdout.
    default_rc: returncode for any command not listed (used for the
    `lit parse --help` LiteParse probe).
    """
    version_outputs = version_outputs or {}
    calls: list[list[str]] = []

    def run(cmd, **kw):
        calls.append(list(cmd))
        key = tuple(cmd[:2])
        if key in version_outputs:
            return subprocess.CompletedProcess(cmd, 0, stdout=version_outputs[key], stderr="")
        return subprocess.CompletedProcess(cmd, default_rc, stdout="", stderr="")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_all_engines_missing_and_required_fails(monkeypatch):
    _patch(monkeypatch, paths={})
    rep = doctor.diagnose(declared={"liteparse": ["liteparse.md"]})
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["liteparse"]["found"] is False
    assert by["liteparse"]["required"] is True
    assert by["liteparse"]["ok"] is False
    assert by["tesseract"]["required"] is False  # not declared -> informational
    assert rep["ok"] is False
    assert rep["broken"] == ["liteparse"]


def test_tesseract_found_is_ok(monkeypatch):
    _patch(
        monkeypatch,
        paths={"tesseract": "/opt/homebrew/bin/tesseract"},
        runner=_runner({("/opt/homebrew/bin/tesseract", "--version"): "tesseract 5.3.4\n LICENSE..."}),
    )
    rep = doctor.diagnose()
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["tesseract"]["ok"] is True
    assert by["tesseract"]["found"] is True
    assert "5.3.4" in by["tesseract"]["version"]
    assert rep["ok"] is True


def test_liteparse_ok_via_version_marker(monkeypatch):
    runner = _runner({("/usr/local/bin/lit", "--version"): "LiteParse lit 2.14.3"})
    _patch(monkeypatch, paths={"lit": "/usr/local/bin/lit"}, runner=runner)
    rep = doctor.diagnose(declared={"liteparse": ["liteparse.md"]})
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["liteparse"]["ok"] is True
    # version marker confirmed it; no need for the `lit parse --help` probe
    assert not any(c[1] == "parse" for c in runner.calls)  # type: ignore[attr-defined]
    assert rep["ok"] is True


def test_liteparse_ok_via_parse_probe_when_version_ambiguous(monkeypatch):
    # A version string without a LiteParse marker (e.g. LLVM's `lit 18.1.8`)
    # is only accepted if `lit parse --help` also works.
    runner = _runner({("/usr/bin/lit", "--version"): "lit 18.1.8"}, default_rc=0)
    _patch(monkeypatch, paths={"lit": "/usr/bin/lit"}, runner=runner)
    rep = doctor.diagnose(declared={"liteparse": ["liteparse.md"]})
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["liteparse"]["ok"] is True
    assert any(c[1] == "parse" for c in runner.calls)  # type: ignore[attr-defined]


def test_liteparse_wrong_lit_is_caught(monkeypatch):
    runner = _runner({("/usr/bin/lit", "--version"): "lit 18.1.8"}, default_rc=1)
    _patch(monkeypatch, paths={"lit": "/usr/bin/lit"}, runner=runner)
    rep = doctor.diagnose(declared={"liteparse": ["liteparse.md"]})
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["liteparse"]["ok"] is False
    assert any("does not look like LiteParse" in p for p in by["liteparse"]["problems"])
    assert rep["ok"] is False
    assert rep["broken"] == ["liteparse"]


def test_require_flag_marks_engine_required(monkeypatch):
    _patch(monkeypatch, paths={})
    rep = doctor.diagnose(require={"tesseract"})
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["tesseract"]["required"] is True
    assert rep["ok"] is False
    assert rep["broken"] == ["tesseract"]


def test_declared_models_reported(monkeypatch):
    _patch(monkeypatch, paths={})
    rep = doctor.diagnose(declared={"tesseract": ["tesseract.md", "ocr-x.md"]})
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["tesseract"]["declared_by"] == ["ocr-x.md", "tesseract.md"]
    assert by["liteparse"]["declared_by"] == []


def test_render_fail_and_ok(monkeypatch):
    _patch(monkeypatch, paths={})
    fail = doctor.diagnose(declared={"liteparse": ["liteparse.md"]})
    assert "RESULT: FAIL" in doctor.render(fail)
    ok = doctor.diagnose()
    assert "RESULT: ok" in doctor.render(ok)
    assert "fix:" in doctor.render(fail)


def test_found_via_fallback_not_on_path(monkeypatch):
    resolved = "/Users/jrc/.pyenv/shims/lit"
    runner = _runner({(resolved, "--version"): "lit 2.14.3 (LiteParse)"})
    _patch(monkeypatch, paths={"lit": resolved}, on_path={}, runner=runner)
    rep = doctor.diagnose(declared={"liteparse": ["liteparse.md"]})
    by = {e["engine"]: e for e in rep["engines"]}
    assert by["liteparse"]["ok"] is True
    assert by["liteparse"]["on_path"] is False
    assert by["liteparse"]["path"] == resolved
    assert "not on pha's PATH" in doctor.render(rep)

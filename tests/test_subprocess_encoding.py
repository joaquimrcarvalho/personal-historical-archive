"""Regression tests for Windows-safe text-mode subprocess calls.

The bug: CPython's ``subprocess`` resolves a missing text-mode ``encoding`` with
``locale.getencoding()`` (CPython 3.12 ``subprocess._text_encoding``).  On the
Traditional Chinese Windows machine that reported it that is ``cp950``, while
pnpm/Node and pha's own Python children write UTF-8, so the reader thread died
with ``UnicodeDecodeError: 'cp950' codec can't decode byte 0xe2`` in the middle
of ``pha update`` (the view-plugin step runs ``pnpm add``).

The fix adds ``subproc.run_utf8`` / ``subproc.run_text`` / ``utf8_child_env`` /
``harden_stdio`` and routes every text-mode call through it.  These tests pin
the root cause, the helpers' contract, the two call sites from the report, and a
static guard so a future call cannot drift back to ``text=True`` without an
explicit encoding.
"""

from __future__ import annotations

import ast
import io
import locale
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from personal_historical_archive import subproc, view

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "personal_historical_archive"
SKILLS = REPO_ROOT / "skills"

# The child writes U+2014 EM DASH as raw UTF-8 bytes: 0xe2 is the byte whose
# cp950 decode failure appeared in the reported traceback.
EM_DASH_CHILD = "import sys; sys.stdout.buffer.write('\\u2014'.encode('utf-8'))"


# ---------------------------------------------------------------------------
# 1. The root cause, and that run_utf8 is immune to it


def test_text_mode_without_encoding_follows_cp950_locale(monkeypatch):
    """Plain ``text=True`` decodes with the locale code page, and crashes.

    Simulates the reporter's Windows box on any platform: CPython's
    ``subprocess._text_encoding()`` falls back to ``locale.getencoding()``, so
    patching that to ``cp950`` makes the UTF-8 output undecodable.  The same
    child read through ``subproc.run_utf8`` comes back intact.
    """
    if sys.flags.utf8_mode:
        pytest.skip("UTF-8 mode makes subprocess ignore the locale encoding")
    monkeypatch.setattr(locale, "getencoding", lambda: "cp950")

    if os.name == "nt":
        # On Windows the decode error happens in the reader thread and is
        # swallowed there — that IS the reported bug — so the call returns with
        # stdout lost instead of raising.
        plain = subprocess.run(
            [sys.executable, "-c", EM_DASH_CHILD],
            capture_output=True,
            text=True,
        )
        assert plain.stdout is None
    else:
        with pytest.raises(UnicodeDecodeError):
            subprocess.run(
                [sys.executable, "-c", EM_DASH_CHILD],
                capture_output=True,
                text=True,
            )

    proc = subproc.run_utf8([sys.executable, "-c", EM_DASH_CHILD])
    assert proc.returncode == 0
    assert proc.stdout == "\u2014"


# ---------------------------------------------------------------------------
# 2-3. run_utf8 / run_text decoding guarantees


def test_run_utf8_replaces_undecodable_bytes():
    """One stray byte is diagnostic noise, not a reader-thread crash."""
    proc = subproc.run_utf8(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"]
    )
    assert proc.returncode == 0
    assert isinstance(proc.stdout, str)
    assert "\ufffd" in proc.stdout


def test_run_text_decodes_native_tool_in_locale_encoding(monkeypatch):
    """Native tools (git/ps/ssh) answer in the locale code page, not UTF-8."""
    monkeypatch.setattr(locale, "getencoding", lambda: "cp950")
    proc = subproc.run_text(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write('\\u2014'.encode('cp950'))",
        ]
    )
    assert proc.returncode == 0
    assert proc.stdout == "\u2014"


# ---------------------------------------------------------------------------
# 4. The Python-child environment


def test_utf8_child_env_preserves_env_and_applies_extra(monkeypatch):
    monkeypatch.setenv("PHA_TEST_INHERITED", "inherited")
    env = subproc.utf8_child_env({"PHA_TEST_EXTRA": "extra"})

    assert env is not os.environ
    assert env["PHA_TEST_INHERITED"] == "inherited"
    assert env["PHA_TEST_EXTRA"] == "extra"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert set(os.environ) <= set(env)


def test_utf8_child_env_forces_utf8_keys_last():
    """An ``extra`` mapping cannot opt a child back out of UTF-8 mode."""
    env = subproc.utf8_child_env(
        {"PYTHONUTF8": "0", "PYTHONIOENCODING": "latin-1"}
    )
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


# ---------------------------------------------------------------------------
# 5. harden_stdio: relax the error handler, keep the inherited encoding


def test_harden_stdio_relaxes_errors_but_keeps_encoding(monkeypatch):
    """A cp950 stdout must print the check mark as '?', not raise.

    The encoding is deliberately left alone: an older pha (pre-fix) reads this
    process's output with the Windows code page, so a transitional direct child
    must keep writing that code page; a current pha forces UTF-8 on its Python
    children through ``utf8_child_env`` instead.
    """
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp950", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    with pytest.raises(UnicodeEncodeError):
        print("\u2713")  # cp950 cannot encode it, and errors="strict"

    subproc.harden_stdio()
    print("\u2713")  # now replaced, not raised
    stream.flush()

    data = raw.getvalue()
    assert data.rstrip(b"\r\n") == "\u2713".encode("cp950", "replace")  # b"?"
    assert b"\xe2\x9c\x93" not in data  # and NOT silently re-encoded as UTF-8
    assert stream.encoding.lower() == "cp950"  # encoding kept on purpose
    assert stream.errors == "replace"


def test_harden_stdio_ignores_streams_without_reconfigure(monkeypatch):
    """Test capture objects (and anything else) may lack ``reconfigure``."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    subproc.harden_stdio()  # must not raise


# ---------------------------------------------------------------------------
# 6. The reported crash: `pha view install` running pnpm


@pytest.mark.skipif(
    os.name == "nt", reason="a shell-script pnpm stand-in needs POSIX"
)
def test_ensure_plugin_link_survives_pnpm_utf8_output(tmp_path, monkeypatch):
    """Under cp950, the pnpm step of the view install must not decode-crash.

    The pnpm stand-in writes the em dash's raw UTF-8 bytes (0xe2 ...) -- exactly
    the output that killed the reader thread -- and the fake profile makes
    ``ensure_plugin_link`` take the pnpm branch.  A byte-order marker file proves
    pnpm actually ran (otherwise the symlink fallback could mask the test).
    """
    monkeypatch.setattr(locale, "getencoding", lambda: "cp950")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "pnpm-ran"
    pnpm = bin_dir / "pnpm"
    pnpm.write_text(
        "#!/bin/sh\n"
        "printf '\\342\\200\\224'\n"
        f"touch {shlex.quote(str(marker))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    pnpm.chmod(0o755)
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    )

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "pnpm-workspace.yaml").write_text(
        "packages: []\n", encoding="utf-8"
    )
    payload = tmp_path / "payload"
    payload.mkdir()

    dest = view.ensure_plugin_link(profile, payload)

    assert marker.exists(), "ensure_plugin_link did not run the pnpm stand-in"
    assert dest == profile / "node_modules" / "@personal-historical-archive" / "dsh-pha"


# ---------------------------------------------------------------------------
# 7. The fresh-pha child spawned after `pha update`


def test_install_after_tool_update_spawns_utf8_python_child(tmp_path, monkeypatch):
    """The post-update child is forced into UTF-8 mode and decoded as UTF-8."""
    home = tmp_path / "dsh-home"
    (home / "profiles" / "desktop").mkdir(parents=True)
    monkeypatch.setattr(view, "dsh_home", lambda: home)

    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(subproc.subprocess, "run", fake_run)
    line = view.install_after_tool_update()

    assert line == "ok"
    argv = captured["argv"]
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "personal_historical_archive"]
    assert argv[3:6] == ["view", "install", "--from-update"]

    kwargs = captured["kwargs"]
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    env = kwargs["env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PHA_NO_UPDATE_CHECK"] == "1"
    root = view.source_checkout_root()
    if root is not None:  # a source checkout: the child runs against these files
        assert kwargs["cwd"] == str(root)


def test_cli_update_helpers_spawn_utf8_children(monkeypatch):
    """`pha update` spawns whatsnew and skills update as UTF-8 children.

    Both are pha's own Python processes, so they must be forced into UTF-8 mode
    and decoded as UTF-8 regardless of the parent's code page.
    """
    from personal_historical_archive import cli

    captured: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        captured.append((list(argv), kwargs))
        if "skills" in argv:
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        return subprocess.CompletedProcess(argv, 0, "notes", "")

    monkeypatch.setattr(subproc.subprocess, "run", fake_run)

    assert cli._whatsnew_after_update("0.46.5") == "notes"
    cli._skills_after_update("/tmp/pha-archive")

    assert len(captured) == 2
    for _argv, kwargs in captured:
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "whatsnew" in captured[0][0] and "--since" in captured[0][0]
    assert "skills" in captured[1][0] and "--json" in captured[1][0]
    assert captured[1][1]["env"]["PHA_ARCHIVE_DIR"] == "/tmp/pha-archive"


# ---------------------------------------------------------------------------
# 8. Static guard: no text-mode subprocess call without an explicit encoding

_TEXT_MODE_FUNCTIONS = {"run", "Popen", "check_output", "check_call", "call"}
_TEXT_MODE_KEYWORDS = ("text", "universal_newlines")


def _text_mode_offenses(tree: ast.AST, *, prefix: str) -> list[str]:
    """Calls to a known subprocess entry point in text mode without ``encoding``."""
    offenses: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _TEXT_MODE_FUNCTIONS:
            continue
        keywords = {kw.arg for kw in node.keywords if kw.arg}
        text_mode = any(
            kw.arg in _TEXT_MODE_KEYWORDS
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if text_mode and "encoding" not in keywords:
            offenses.append(
                f"{prefix}:{node.lineno}: .{node.func.attr}(...) is text mode "
                "without an explicit encoding"
            )
    return offenses


def _embedded_python_scripts(path: Path) -> list[tuple[str, int, str]]:
    """``(constant name, line, source)`` for every embedded Python-script string.

    ``archive_skills.py`` ships helpers as string constants starting with a
    ``#!/usr/bin/env python3`` shebang; they are written to disk as files, so a
    checker that only parses ``*.py`` would never see them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    scripts: list[tuple[str, int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        if not node.value.value.startswith("#!/usr/bin/env python3"):
            continue
        name = targets[0].id if isinstance(targets[0], ast.Name) else "<constant>"
        scripts.append((name, node.lineno, node.value.value))
    return scripts


def test_no_text_mode_subprocess_call_without_encoding():
    """No text-mode subprocess call anywhere in the shipped code lacks encoding.

    Covers ``src/personal_historical_archive``, the standalone scripts under
    ``skills/`` and the Python programs embedded in ``archive_skills.py``.  The
    reported crash was a ``pnpm`` call here; this guard stops the next one.
    """
    problems: list[str] = []
    scanned = 0
    for root in (SRC, SKILLS):
        for path in sorted(root.rglob("*.py")):
            scanned += 1
            source = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_ROOT)
            problems.extend(
                _text_mode_offenses(
                    ast.parse(source, filename=str(path)), prefix=str(rel)
                )
            )

    archive_skills = SRC / "archive_skills.py"
    embedded = _embedded_python_scripts(archive_skills)
    assert embedded, "no embedded Python helpers found in archive_skills.py"
    for name, line, script in embedded:
        try:
            tree = ast.parse(script)
        except SyntaxError as exc:  # a broken embedded helper is a defect too
            problems.append(f"{archive_skills.relative_to(REPO_ROOT)}:{line}: "
                            f"{name} does not parse: {exc}")
            continue
        problems.extend(
            _text_mode_offenses(
                tree,
                prefix=f"{archive_skills.relative_to(REPO_ROOT)}:{line} "
                       f"{name}",
            )
        )

    assert scanned > 0, "guard scanned no files (wrong repo root?)"
    assert not problems, "text-mode subprocess call(s) without encoding:\n" + "\n".join(
        problems
    )

"""Text-mode subprocess calls that behave the same on every platform.

``subprocess.run(..., text=True)`` decodes a child's output with the process
locale encoding unless ``encoding`` is given.  On Windows that is the ANSI code
page -- cp950 on the Traditional Chinese machine that reported the bug -- while
the tools that produce non-ASCII output here write UTF-8: Node tools such as
``pnpm``/``lms`` and the Python children pha spawns for itself.  The Windows
reader thread then died with ``UnicodeDecodeError: 'cp950' codec can't decode
byte 0xe2`` in the middle of ``pha update`` (the view-plugin step runs
``pnpm add``).

Three rules keep that from coming back:

* every text-mode call goes through :func:`run_utf8` (a child known to speak
  UTF-8) or :func:`run_text` (a platform tool whose output encoding follows the
  locale) -- both state an encoding and pass ``errors="replace"`` so one
  undecodable byte never kills a reader thread;
* every Python child pha spawns gets an environment from
  :func:`utf8_child_env`, which forces PEP 540 UTF-8 mode
  (``PYTHONUTF8``/``PYTHONIOENCODING``) so its stdout is UTF-8 even when the
  parent inherited a cp950 console;
* :func:`harden_stdio` gives pha's own stdout/stderr the same tolerance: pha
  prints glyphs a Windows code page cannot encode (``⚠`` in doctor, ``⏱``/
  ``↻`` in status, ``📌`` in pins, ``✓``/``✗`` in the encoder report), so a
  redirected cp950 stream would otherwise raise ``UnicodeEncodeError``.  It
  deliberately keeps the inherited encoding: a pha spawned by an older pha
  writes the code page that older parent expects to decode, while a child of a
  current pha is forced to UTF-8 by :func:`utf8_child_env`.

Tests in ``tests/test_subprocess_encoding.py`` enforce the first rule: a
text-mode ``subprocess`` call without an explicit encoding fails the suite.
"""

from __future__ import annotations

import locale
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence

#: UTF-8 is what our own Python children and the Node tools we drive speak.
UTF8 = "utf-8"
#: A byte we cannot decode is diagnostic noise, not a reason to crash.
ERRORS = "replace"


def utf8_child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment that forces a Python child's stdio to UTF-8.

    ``extra`` entries are applied on top of the inherited environment; the two
    UTF-8 keys are set last so they cannot be overridden.  Harmless for
    non-Python children (they simply ignore the variables).
    """
    env = dict(os.environ)
    if extra:
        env.update(extra)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_utf8(argv: Sequence[str], *, timeout: float | None = None,
             check: bool = False, env: Mapping[str, str] | None = None,
             cwd: str | os.PathLike[str] | None = None,
             input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a child known to write UTF-8 (Node tools, our own Python children)."""
    return _run(argv, UTF8, timeout=timeout, check=check, env=env, cwd=cwd,
                input=input)


def run_text(argv: Sequence[str], *, timeout: float | None = None,
             check: bool = False, env: Mapping[str, str] | None = None,
             cwd: str | os.PathLike[str] | None = None,
             input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a platform tool whose output encoding follows the locale.

    Used for ``ps``, ``ssh``, ``launchctl``, the keychain helpers and the like:
    on Windows they answer in the ANSI code page, on POSIX in the locale
    encoding.  Decoding with that encoding keeps localized messages readable;
    ``errors="replace"`` keeps an unexpected byte from crashing.  ``git`` is the
    mixed case (Git for Windows often emits UTF-8) -- only its error text is
    shown, so a stray ``?`` there is acceptable.
    """
    encoding = locale.getencoding() or UTF8
    return _run(argv, encoding, timeout=timeout, check=check, env=env, cwd=cwd,
                input=input)


def _run(argv: Sequence[str], encoding: str, *, timeout: float | None,
         check: bool, env: Mapping[str, str] | None,
         cwd: str | os.PathLike[str] | None,
         input: str | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding=encoding,
        errors=ERRORS,
        timeout=timeout,
        check=check,
        env=dict(env) if env is not None else None,
        cwd=cwd,
        input=input,
    )


def harden_stdio() -> None:
    """Make pha's own printing survive a non-UTF-8 locale or redirection.

    A Windows console already speaks UTF-8 (PEP 528); this matters when stdout
    or stderr is a pipe/file encoded with cp950/cp1252, where printing a glyph
    such as ``⚠`` or ``✓`` raises ``UnicodeEncodeError``.  Keep the inherited
    encoding on purpose: when an older pha spawns this one and reads its output
    as text, the child must keep writing the parent's code page.
    Never raises: test capture objects are not reconfigurable.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors=ERRORS)
        except (AttributeError, ValueError, OSError):
            pass


__all__ = ["ERRORS", "UTF8", "harden_stdio", "run_text", "run_utf8",
           "utf8_child_env"]

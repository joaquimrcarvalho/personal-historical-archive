"""Ergonomic entry point for pha stage filters.

A filter is normally just::

    from personal_historical_archive.filter_api import run   # optional

    def run(value, ctx):
        return value        # or None to leave it unchanged

pha imports that function and calls it in-process (``filters.py``). This
module also provides the documented **subprocess** shim, used when a filter's
manifest sets ``command:`` or when a subprocess is requested explicitly::

    python -m personal_historical_archive.filter_api --input in.json --output out.json

The envelope is the real contract — a filter may read/write it directly
instead of importing anything. See FILTERS_PLAN.md §2.2 and the sample
manifest in ``filters/_sample/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def read_envelope(path: str | None):
    """Read the input envelope from a file (or stdin when path is '-')."""
    if path in (None, "-"):
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_envelope(raw, path: str | None) -> None:
    """Write a result envelope to a file (or stdout when path is '-')."""
    text = json.dumps(raw, ensure_ascii=False)
    if path in (None, "-"):
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")


def load_run(target: Path | None = None):
    """The filter's ``run(value, ctx)`` callable, for the in-process path.

    Kept here so a filter can be executed the same way in either mode. The
    source is compiled directly (not imported) so a stale ``__pycache__``
    entry can never shadow an edited filter — see ``filters._load_module``.
    """
    import types

    script = target or (Path.cwd() / "filter.py")
    try:
        code = compile(script.read_bytes(), str(script), "exec")
    except OSError as e:
        raise RuntimeError(f"cannot read filter {script}: {e}") from e
    except SyntaxError as e:
        raise RuntimeError(f"cannot compile filter {script}: {e}") from e
    mod = types.ModuleType("_pha_filter_entry")
    mod.__file__ = str(script)
    exec(code, mod.__dict__)
    run = getattr(mod, "run", None)
    if not callable(run):
        raise RuntimeError(f"{script} defines no run(value, ctx)")
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m personal_historical_archive.filter_api",
        description="Run one pha stage filter on a JSON envelope.",
    )
    ap.add_argument("--input", default="-", help="input envelope JSON file ('-' = stdin)")
    ap.add_argument("--output", default="-", help="output envelope JSON file ('-' = stdout)")
    ap.add_argument("--script", default=None,
                    help="filter.py to run (default: ./filter.py)")
    args = ap.parse_args(argv)

    envelope = read_envelope(args.input)
    ctx = envelope.get("context") or {}
    value = envelope.get("value")
    kind = envelope.get("kind", "text")

    run = load_run(Path(args.script) if args.script else None)
    result = run(value, ctx)

    if result is None:
        # pass-through: an empty result means "leave the value unchanged"
        write_envelope({}, args.output)
        return 0
    if isinstance(result, dict) and ("value" in result or "kind" in result):
        write_envelope(result, args.output)
        return 0
    write_envelope({"kind": kind, "value": result}, args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI shim
    sys.exit(main())

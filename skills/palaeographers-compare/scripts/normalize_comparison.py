#!/usr/bin/env python3
"""Normalise the layout of palaeographers-compare output files.

For every page file (not overview.md) in a comparison folder, collapses
per-entry fenced code blocks into ONE fenced block (the uniform skeleton):
  - removes lines that are exactly ```
  - trims leading/trailing blank lines of the entry section
  - collapses runs of 2+ blank lines into one
  - wraps the whole entry section in a single pair of fences
  - restores a single blank line before the opening fence
Also collapses double blank lines in the header area and optionally drops
stray label lines (--drop-label "...").
"""
import argparse
import pathlib
import re
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", nargs="?", default="comparison",
                    help="comparison output folder (default: comparison)")
    ap.add_argument("--drop-label", action="append", default=[],
                    metavar="TEXT",
                    help="drop lines that exactly equal TEXT (repeatable)")
    args = ap.parse_args()

    folder = pathlib.Path(args.folder)
    if not folder.is_dir():
        print(f"error: {folder} is not a directory", file=sys.stderr)
        return 2

    changed = 0
    for p in sorted(folder.glob("*.md")):
        if p.name == "overview.md":
            continue
        text = p.read_text()
        original = text

        for label in args.drop_label:
            text = re.sub(rf"^\s*{re.escape(label)}\s*$\n?", "", text, flags=re.M)

        m1 = "## Entry-by-entry comparison"
        m2 = "## Key differences on this page"
        if m1 not in text or m2 not in text:
            print(f"skip  {p.name}: section markers not found")
            continue
        i = text.index(m1)
        j = text.index(m2)
        head, middle, tail = text[:i], text[i + len(m1):j], text[j:]

        lines = [ln for ln in middle.splitlines() if ln.strip() != "```"]
        while lines and lines[0].strip() == "":
            lines.pop(0)
        while lines and lines[-1].strip() == "":
            lines.pop()
        out, prev_blank = [], False
        for ln in lines:
            blank = (ln.strip() == "")
            if blank and prev_blank:
                continue
            out.append(ln)
            prev_blank = blank
        body = "\n".join(out)

        head = re.sub(r"\n{3,}(## Entry-by-entry comparison)", r"\n\n\1", head)
        new_text = head + m1 + "\n\n```\n" + body + "\n```\n\n" + tail
        if new_text != original:
            p.write_text(new_text)
            changed += 1
            n_entries = sum(1 for ln in out if ln.startswith("["))
            print(f"fixed {p.name}: {n_entries} entries in one fenced block")
        else:
            print(f"ok    {p.name}")

    print(f"\n{changed} file(s) normalised")
    return 0


if __name__ == "__main__":
    sys.exit(main())

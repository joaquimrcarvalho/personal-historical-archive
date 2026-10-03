#!/usr/bin/env python3
"""Verify the uniform skeleton of palaeographers-compare output files.

Checks per page file (not overview.md):
  - exactly 2 fenced code blocks
  - exactly the two sections "## Entry-by-entry comparison" and
    "## Key differences on this page", in that order
  - a blank line before the opening fence
  - all [n] entry markers contiguous inside the single fenced block
  - entry numbers form a continuous sequence [1]..[N]
  - with --readings N: every entry carries exactly N readings (or is an
    "= all …" entry); catches silent mis-merges on corpora with a different
    number of palaeographers
  - the corpus images/ folder sits next to palaeographers/ and every
    compared page has its image there (warn by default;
    --require-images makes it a failure)
Exits non-zero if any check fails.
"""
import argparse
import pathlib
import re
import sys


def find_images_dir(folder):
    """Locate the images/ folder that should sit next to the corpus.

    The comparison folder is normally <corpus>/palaeographers/comparison, so
    images/ can be a sibling of the comparison folder, of palaeographers/, or
    of the corpus. Return the nearest existing one, or None.
    """
    for cand in (folder / "images", folder.parent / "images",
                 folder.parent.parent / "images"):
        if cand.is_dir():
            return cand
    return None


def reading_counts(middle):
    """Count the reading markers of every [n] entry in a fenced block.

    Reading markers are only recognised in sequence (1., 2., 3., …), matching
    make_reference.py, so embedded numbering ("3. Nouicos.") is not counted.
    "= all …" entries count as 0 readings.
    """
    counts = {}
    cur = None
    expected = 0
    for ln in middle.splitlines():
        s = ln.strip()
        if s == "" or s == "```":
            continue
        m = re.match(r"^\[(\d+)\]", s)
        if m:
            cur = int(m.group(1))
            counts[cur] = 0
            expected = 1
            s = s[m.end():].strip()
        if cur is None:
            continue
        mm = re.match(r"^(\d+)\.\s+", s)
        if mm and int(mm.group(1)) == expected:
            counts[cur] += 1
            expected += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", nargs="?", default="comparison")
    ap.add_argument("--readings", type=int, default=None, metavar="N",
                    help="expected number of readings per entry")
    ap.add_argument("--require-images", action="store_true",
                    help="fail when the images/ folder or a page image is missing")
    args = ap.parse_args()

    folder = pathlib.Path(args.folder)
    if not folder.is_dir():
        print(f"error: {folder} is not a directory", file=sys.stderr)
        return 2

    failures = 0
    files = [p for p in sorted(folder.glob("*.md")) if p.name != "overview.md"]

    images_dir = find_images_dir(folder)
    image_exts = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")
    if images_dir is None:
        msg = (f"no images/ folder for the corpus (looked in {folder}/images, "
               f"{folder.parent}/images, {folder.parent.parent}/images)")
        if args.require_images:
            print(f"FAIL {msg}", file=sys.stderr)
            return 1
        print(f"WARN {msg} - copy each page image next to palaeographers/")
    else:
        print(f"images: {images_dir}")
    for p in files:
        text = p.read_text()
        problems = []

        fences = [ln for ln in text.splitlines() if ln.strip() == "```"]
        if len(fences) != 2:
            problems.append(f"expected 2 fence lines, found {len(fences)}")

        sections = re.findall(r"^## (.+)$", text, flags=re.M)
        if sections != ["Entry-by-entry comparison", "Key differences on this page"]:
            problems.append(f"sections are {sections!r}")

        if "## Entry-by-entry comparison" in text and "## Key differences on this page" in text:
            i = text.index("## Entry-by-entry comparison")
            j = text.index("## Key differences on this page")
            middle = text[i + len("## Entry-by-entry comparison"):j]

            if not middle.startswith("\n\n```"):
                problems.append("no blank line before the opening fence")
            if not middle.rstrip().endswith("```"):
                problems.append("entry block not closed by a fence")

            body = [ln for ln in middle.splitlines()
                    if ln.strip() not in ("", "```")]
            nums = []
            for ln in body:
                m = re.match(r"^\[(\d+)\]", ln.strip())
                if m:
                    nums.append(int(m.group(1)))
            if not nums:
                problems.append("no [n] entry markers found")
            elif nums != list(range(1, len(nums) + 1)):
                problems.append(f"entry numbers not continuous: {nums[:8]}...")

            if args.readings is not None:
                counts = reading_counts(middle)
                nonzero = [c for c in counts.values() if c]
                divergent = bool(nonzero) and all(c == 1 for c in nonzero)
                if divergent:
                    pass  # page kept unconsolidated (one full reading per entry)
                else:
                    bad = {n: c for n, c in counts.items()
                           if c not in (0, args.readings)}
                    if bad:
                        preview = ", ".join(f"[{n}]={c}" for n, c in sorted(bad.items())[:6])
                        problems.append(
                            f"entries whose reading count != {args.readings}: {preview}")

        if images_dir is not None and not any(
                (images_dir / (p.stem + e)).exists() for e in image_exts):
            msg = f"no image for {p.name} in {images_dir}"
            if args.require_images:
                problems.append(msg)
            else:
                print(f"warn {p.name}: {msg}")

        if problems:
            failures += 1
            print(f"FAIL {p.name}:")
            for pr in problems:
                print(f"     - {pr}")
        else:
            print(f"ok   {p.name}")

    print(f"\n{len(files) - failures}/{len(files)} files OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build a reference/ folder (consolidated variant lines) from a comparison/ folder.

Works with ANY number of readings per entry (2, 3, 4, …).

For every page file in comparison/, produce reference/<page>.md where each entry
is ONE line: common words written once, differing words joined with '/' in
reading order. Token-level sequence alignment handles insertions/deletions;
trailing periods/commas and line-end hyphenation are treated as padding; em-dash
layout leaders are kept as variants, bare '/' and '-' name<->role connectors are
dropped.

Usage:
  python3 scripts/make_reference.py comparison/ [reference/] [--readings N] [--divergent PAGE ...]

  --readings N     expected number of readings per entry; the script refuses to
                   write anything if a page turns out to have MORE readings than
                   declared (silent mis-merges are the failure mode to avoid)
  --divergent PAGE page stem(s) to keep unconsolidated (repeatable). Pages whose
                   entries each carry only a single reading are detected as
                   divergent automatically.
"""
import argparse
import pathlib
import re
import sys

M1 = "## Entry-by-entry comparison"
M2 = "## Key differences on this page"

ANNOT = re.compile(r"\s*\([^)]*(?:reading|renders|is written|not transcribed|omitted)[^)]*\)")
# matches "= all three:", "= all readings:", "= all:" …
ALL_RE = re.compile(r"^=\s*all\b[^:]*:\s*(.*)$", re.I)
READ_RE = re.compile(r"^(\d+)\.\s+(.*)$")

# standalone tokens that act as name<->role connectors (padding for equality)
CONN = {"--", "-", "/"}


def canon_conn(tok):
    return "--" if tok in CONN else tok


def dehyphenate(lines):
    """Join transcription lines, resolving line-end hyphenation (- or =)."""
    out = ""
    for ln in lines:
        s = ANNOT.sub("", ln.strip())
        if not s:
            continue
        if out and out[-1] in "-=" and s[0] not in "-=":
            out = out[:-1] + s
        elif out:
            out = out + " " + s
        else:
            out = s
    return out


def parse_entries(path):
    """Parse a comparison page file.

    Returns (entries, max_reading):
      entries      -> list of (num, kind, payload)
                      kind 'all'  -> payload is the shared text (str)
                      kind 'diff' -> payload is a LIST of the readings present
      max_reading  -> highest reading index seen on the page (0 if none)

    Reading markers are recognised only in sequence (1., 2., 3., …), so a
    continuation line that happens to start with a number ("3. Nouicos.") is
    not mistaken for a new reading.
    """
    text = path.read_text()
    i = text.index(M1)
    j = text.index(M2)
    block = text[i + len(M1):j].splitlines()
    raw = []
    cur = None
    for ln in block:
        s = ln.strip()
        if s == "" or s == "```":
            continue
        m = re.match(r"^\[(\d+)\]", s)
        if m:
            if cur:
                raw.append(cur)
            cur = {"num": int(m.group(1)), "rest": s[m.end():], "lines": []}
        elif cur is not None:
            cur["lines"].append(ln)
    if cur:
        raw.append(cur)

    entries = []
    max_reading = 0
    for e in raw:
        rest = e["rest"].strip()
        m = ALL_RE.match(rest)
        if m:
            txt = dehyphenate([m.group(1)] + e["lines"])
            entries.append((e["num"], "all", txt))
            continue
        reads = {}
        cur_n = None
        expected = 1
        for ln in [rest] + e["lines"]:
            s = ln.strip()
            m = READ_RE.match(s)
            if m and int(m.group(1)) == expected:
                cur_n = int(m.group(1))
                reads[cur_n] = [m.group(2)]
                expected += 1
            elif cur_n is not None:
                reads[cur_n].append(s)
        if not reads:                       # no reading markers at all
            reads[1] = [rest] + [l.strip() for l in e["lines"]]
        idxs = sorted(reads)
        max_reading = max(max_reading, idxs[-1])
        entries.append((e["num"], "diff", [dehyphenate(reads[k]) for k in idxs]))
    return entries, max_reading


def tok_eq(a, b):
    return a.rstrip(".,") == b.rstrip(".,") or (
        a in CONN and b in CONN)


def align(a, b):
    """Token-level sequence alignment; returns list of (a_tok|None, b_tok|None)."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = 0 if tok_eq(a[i - 1], b[j - 1]) else 1
            dp[i][j] = min(dp[i - 1][j - 1] + c, dp[i - 1][j] + 1, dp[i][j - 1] + 1)
    i, j = n, m
    pairs = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (0 if tok_eq(a[i - 1], b[j - 1]) else 1):
            pairs.append((a[i - 1], b[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            pairs.append((a[i - 1], None))
            i -= 1
        else:
            pairs.append((None, b[j - 1]))
            j -= 1
    pairs.reverse()
    return pairs


def merge(readings):
    """readings: list of N token lists -> consolidated string with '/' variants."""
    lists = []
    for toks in readings:
        # bare '/' and '-' are name<->role connectors (padding): drop them before
        # alignment; em-dash runs ('—', '————') are layout leaders and are kept.
        lists.append([t for t in toks if t not in ("/", "-")])
    lists = [l for l in lists if l]
    if not lists:
        return ""
    if len(lists) == 1:
        return " ".join(lists[0])

    base = lists[0]
    cols = [{0: t0, 1: t1} for t0, t1 in align(base, lists[1])]

    for idx in range(2, len(lists)):
        col = 0
        for t0, ti in align(base, lists[idx]):
            if t0 is not None:
                while col < len(cols) and (cols[col].get(0) is None or cols[col][0] != t0):
                    col += 1
                if col < len(cols):
                    cols[col][idx] = ti
                    col += 1
            else:
                nxt = col
                while nxt < len(cols) and cols[nxt].get(0) is None:
                    nxt += 1
                # merge into the preceding insertion column (same position, no token for this reading yet)
                if nxt > 0 and cols[nxt - 1].get(0) is None and cols[nxt - 1].get(idx) is None:
                    cols[nxt - 1][idx] = ti
                else:
                    cols.insert(nxt, {0: None, idx: ti})

    def norm(tok):
        return tok.rstrip(".,;:")

    parts = []
    for k in cols:
        vals = [k.get(i) for i in range(len(lists))]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        vals = [canon_conn(v) for v in vals]
        if all(norm(v) == norm(vals[0]) for v in vals):
            parts.append(vals[0])
            continue
        # first variant kept raw (keeps its punctuation, e.g. 'P.'), the
        # remaining variants are punctuation-normalised ('P[adr]e')
        uniq = [vals[0]]
        for v in vals[1:]:
            if norm(v) not in [norm(u) for u in uniq]:
                uniq.append(v)
        rendered = uniq[0]
        if len(uniq) > 1:
            rendered += "/" + "/".join(norm(u) for u in uniq[1:])
        parts.append(rendered)
    return " ".join(parts)


def reference_text(entries):
    """Render the consolidated lines for a page (str for the fenced block)."""
    lines = []
    for num, kind, payload in entries:
        if kind == "all":
            lines.append(f"[{num}] {payload}")
        else:
            lines.append(f"[{num}] {merge([t.split() for t in payload])}")
    return "\n".join(lines)


def page_meta(src_text):
    raw_title = re.match(r"^# (.+)$", src_text, re.M).group(1)
    title = "# " + raw_title.replace("Comparative readings", "Reference text with variants")
    order = re.search(r"\((.*)\)\s*$", raw_title)
    order = order.group(1) if order else "reading order"
    ctx = re.search(r"^Context: .*$", src_text, re.M)
    context = ctx.group(0) if ctx else "Context: (see comparison file)"
    return title, order, context


def write_page(src, dst, force_divergent=False):
    """Write one reference page. Returns (n_entries, n_readings_max, divergent)."""
    src_text = src.read_text()
    title, order, context = page_meta(src_text)
    entries, max_reading = parse_entries(src)
    diff_entries = [e for e in entries if e[1] == "diff"]

    # a page where every entry carries a single reading cannot be consolidated
    divergent = force_divergent or (
        bool(diff_entries) and all(len(e[2]) == 1 for e in diff_entries))

    if divergent:
        block = re.search(r"```\n(.*?)\n```", src_text, re.S).group(1)
        section = (
            "No consolidation possible — the readings for this page diverge fundamentally "
            "(see comparison/{0}). Each reading is given in full:\n\n"
            "```\n{1}\n```".format(src.name, block)
        )
        notes = [
            "- The readings for this page cannot be aligned; every reading is kept in full.",
            "- See comparison/{0} for the full discussion.".format(src.name),
        ]
        print(f"wrote {dst.name}: divergent readings kept in full")
    else:
        section = "```\n" + reference_text(entries) + "\n```"
        notes = [
            "- Convention: common words are written once; where the readings differ the variants are "
            "joined with '/' in reading order ({0}).".format(order),
            "- Trailing periods/commas and line-end hyphenation are treated as padding; the name<->role "
            "connector ('—', '/', '-') is rendered as '—'. Entries identical in all readings are given as-is.",
            "- Generated from comparison/{0} — see that file for the full stacked readings and the key "
            "differences.".format(src.name),
        ]
        print(f"wrote {dst.name}: {len(entries)} entries")

    content = (
        title + "\n\n" + context + "\n\n"
        "## Reference text (variants in slashes)\n\n"
        + section + "\n\n"
        "## Notes\n\n" + "\n".join(notes) + "\n"
    )
    dst.write_text(content)
    return len(entries), max_reading, divergent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comparison", help="comparison folder (input)")
    ap.add_argument("reference", nargs="?", default=None,
                    help="reference folder (output; default: sibling 'reference' of the comparison folder)")
    ap.add_argument("--readings", type=int, default=None, metavar="N",
                    help="expected number of readings per entry; causes an error if a page has more")
    ap.add_argument("--divergent", action="append", default=[], metavar="PAGE",
                    help="page stem(s) to keep unconsolidated (repeatable)")
    args = ap.parse_args()

    comp = pathlib.Path(args.comparison)
    if not comp.is_dir():
        print(f"error: {comp} is not a directory", file=sys.stderr)
        return 2
    if args.readings is not None and args.readings < 2:
        print("error: --readings must be at least 2", file=sys.stderr)
        return 2

    pages = [p for p in sorted(comp.glob("*.md")) if p.name != "overview.md"]
    if not pages:
        print(f"error: no page files found in {comp}", file=sys.stderr)
        return 2

    # validate BEFORE writing anything, so a wrong --readings never produces bad output
    if args.readings is not None:
        too_many, too_few = [], []
        for p in pages:
            entries, max_reading = parse_entries(p)
            if max_reading > args.readings:
                too_many.append((p.name, max_reading))
            diffs = [e for e in entries if e[1] == "diff"]
            divergent = bool(diffs) and all(len(e[2]) == 1 for e in diffs)
            if divergent:
                continue
            for num, kind, payload in entries:
                if kind == "diff" and len(payload) < args.readings:
                    too_few.append((p.name, num, len(payload)))
                    break
        if too_many:
            print(f"error: --readings {args.readings} was declared, but these pages have MORE readings:",
                  file=sys.stderr)
            for name, mx in too_many:
                print(f"  - {name}: reading {mx} found", file=sys.stderr)
            print("  (nothing was written — fix --readings or the comparison files)", file=sys.stderr)
            return 1
        if too_few:
            print(f"warning: --readings {args.readings} declared; pages with fewer readings detected:",
                  file=sys.stderr)
            for name, num, n in too_few[:10]:
                print(f"  - {name} entry [{num}]: {n} reading(s)", file=sys.stderr)

    ref = pathlib.Path(args.reference) if args.reference else comp.parent / "reference"
    ref.mkdir(parents=True, exist_ok=True)

    divergent = set(args.divergent)
    n_div = 0
    for p in pages:
        _, _, was_divergent = write_page(p, ref / p.name, force_divergent=p.stem in divergent)
        n_div += int(was_divergent)
    print(f"\nreference folder: {ref}  ({len(pages)} pages, {n_div} kept unconsolidated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

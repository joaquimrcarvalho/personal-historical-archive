#!/usr/bin/env python3
"""Consolidate a document's dated library folders after the timezone bug.

Ships with the `pha-library-folders` skill. Run it from the skill's own folder:

    python scripts/consolidate_library_date_dupes.py            # dry run
    python scripts/consolidate_library_date_dupes.py --apply
    python scripts/consolidate_library_date_dupes.py --doc 38 --doc 39 --apply

WHY THIS EXISTS. A document's library folder is
`library/<collection>/<stem>_<YYYY-MM-DD>/`. pha used to recompute that date from
the document's creation time **in the machine's current timezone**, so a machine
whose zone changed looked for — and wrote — a folder under a different date than
the one on disk: one document ended up with two folders. Since pha 0.43.1 the
name is stored on the document row (`documents.library_slug`), so no NEW split is
created; this repairs the ones already on disk.

THE OTHER FOLDER IS NOT A DUPLICATE. The folder pha now resolves to holds the
pages (transcription + edited); the other usually holds the encoder deliverables
(`records-*.json`, `concatenated-*.md`, `segments-*`, `structure.json`,
`.structure-*.sha256`, `.filter-stamps/`). Deleting it would destroy the output
the archive exists for. So this script MOVES every file that is absent from (or
differs from) the canonical folder into it, repoints the `source:` front-matter
line of the moved `segments-*/*.md` at the new location, and parks the
remainder — files already byte-identical to the canonical copy — under
`bin/<stamp>-library-date-dupes/` with a README explaining how to revert.

No model call and no database write: the connection is READ-ONLY, and the folder
name is stamped on the document by the next real scan/edit/encode pass.

The dry run is the default on purpose; nothing is written without `--apply`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import shutil
import sys
from pathlib import Path


def _import_pha():
    """Use the INSTALLED pha; fall back to a checkout found above this file.

    An agent running this from an archive has pha installed and no source
    checkout — that is the normal case. The fallback is for running the same
    file from the pha repository itself.
    """
    try:
        from personal_historical_archive import db, ingest
        from personal_historical_archive.config import Config
        return db, ingest, Config
    except ImportError:
        pass
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "src"
        if (candidate / "personal_historical_archive").is_dir():
            sys.path.insert(0, str(candidate))
            break
    from personal_historical_archive import db, ingest
    from personal_historical_archive.config import Config
    return db, ingest, Config


db, ingest, Config = _import_pha()


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): _digest(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def _rewrite_source(text: str, old_name: str, new_name: str) -> tuple[str, bool]:
    """Point a moved artifact's `source:` front matter at its new folder."""
    out, changed = [], False
    for line in text.split("\n"):
        if line.startswith("source:") and f"/{old_name}/" in line:
            line = line.replace(f"/{old_name}/", f"/{new_name}/")
            changed = True
        out.append(line)
    return "\n".join(out), changed


def _groups(cfg, conn, wanted: set[int]) -> list[tuple[dict, Path, list[Path]]]:
    out = []
    for row in db.list_documents(conn, limit=100000):
        doc = dict(row)
        if wanted and int(doc["id"]) not in wanted:
            continue
        canon = ingest._resolve_library_dir(cfg, doc)
        others = [f for f in ingest._existing_doc_folders(cfg, doc) if f != canon]
        if others:
            out.append((doc, canon, others))
    return out


def _free_dir(base: Path) -> Path:
    cand, n = base, 1
    while cand.exists():
        n += 1
        cand = base.with_name(f"{base.name}-{n}")
    return cand


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate a document's dated library folders (dry run by default).")
    parser.add_argument("--apply", action="store_true",
                        help="perform the moves (default: dry run, nothing is written)")
    parser.add_argument("--doc", type=int, action="append", default=[],
                        help="only this document id (repeatable)")
    args = parser.parse_args()

    cfg = Config.load()
    # READ-ONLY on purpose: this repair is a filesystem move and must not run a
    # schema migration or write the database at all.
    conn = db.connect(cfg.db_path, readonly=True)
    groups = _groups(cfg, conn, set(args.doc))
    if not groups:
        print("nothing to consolidate — every document has exactly one library folder")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%d")
    bin_root = _free_dir(cfg.bin / f"{stamp}-library-date-dupes")
    today = _dt.date.today().isoformat()

    moved_total = binned_total = 0
    report: list[str] = []
    for doc, canon, leftovers in groups:
        print(f'\n#{doc["id"]} {doc["filename"]}')
        print(f"  canonical : {canon.name}")
        canon_tree = _tree(canon) if canon.is_dir() else {}
        for leftover in leftovers:
            left_tree = _tree(leftover)
            move = [k for k, h in left_tree.items() if canon_tree.get(k) != h]
            size = sum((leftover / k).stat().st_size for k in move)
            print(f"  duplicate : {leftover.name}")
            print(f"      move into the canonical folder : {len(move)} file(s), {size/1e6:.1f} MB")
            print(f"      already identical there        : {len(left_tree) - len(move)}")
            if not args.apply:
                for k in move[:4]:
                    print(f"          + {k}")
                if len(move) > 4:
                    print(f"          … and {len(move) - 4} more")
                continue
            for rel in move:
                src, dst = leftover / rel, canon / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    dst.unlink()
                if src.suffix == ".md" and "segments-" in rel:
                    text = src.read_text(encoding="utf-8")
                    text, changed = _rewrite_source(text, leftover.name, canon.name)
                    if changed:
                        dst.write_text(text, encoding="utf-8")
                        src.unlink()
                        continue
                shutil.move(str(src), str(dst))
            dest = bin_root / "library" / Path(doc["dir_path"] or "") / leftover.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(leftover), str(dest))
            moved_total += len(move)
            binned_total += 1
            rel_dest = dest.relative_to(cfg.archive_dir)
            print(f"      → {rel_dest}")
            report.append(
                f'## #{doc["id"]} {doc["filename"]}\n\n'
                f"- canonical folder (kept): `{canon.name}`\n"
                f"- folder parked here: `{leftover.name}` — {len(move)} file(s) were "
                f"MOVED into the canonical folder; the other {len(left_tree) - len(move)} "
                f"are byte-for-byte identical to the canonical copies.\n\n"
                f"To revert (this is only a PARTIAL revert — the moved files stay in "
                f"the canonical folder; move them back too):\n\n"
                f'    mv "{rel_dest}" \\\n'
                f'       "{Path("library") / Path(doc["dir_path"] or "") / leftover.name}"\n'
            )

    if not args.apply:
        print(f"\nDRY RUN — nothing written. {sum(len(o) for _, _, o in groups)} folder(s) "
              f"would be consolidated; backup under {bin_root.relative_to(cfg.archive_dir)}")
        return 0

    bin_root.mkdir(parents=True, exist_ok=True)
    (bin_root / "README.md").write_text(
        "# Duplicated library folders (timezone) — consolidated\n\n"
        f"Moved here on {today} while repairing the bug where a document's library\n"
        "folder name was recomputed from the machine's timezone (fixed in pha 0.43.1;\n"
        "see `pha-library-slug-timezone-bug-report.md` in the pha source repository).\n\n"
        "Each document had the SAME version in two folders with different dates. The\n"
        "canonical folder is the one holding the transcription; the other held the\n"
        "records and the generated segments. Before anything was moved, **every** file\n"
        "that did not exist (or differed) in the canonical folder was MOVED into it —\n"
        "so what is left here is, file by file, byte-for-byte identical to the\n"
        "canonical copy. Nothing that existed only here was lost. There is no\n"
        "`manifest.json`: `pha bin list` deliberately ignores this folder.\n\n"
        "## What was done, per document\n\n"
        + "\n".join(report) +
        "\n## Verify\n\n"
        "    pha status        # the library-missing marker must be gone\n"
        "    pha doctor        # the `library` section must not appear\n",
        encoding="utf-8",
    )
    print(f"\nDONE — {moved_total} file(s) moved into the canonical folders, "
          f"{binned_total} folder(s) parked under {bin_root.relative_to(cfg.archive_dir)}")
    print("verify with `pha status` (the library-missing marker must be gone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

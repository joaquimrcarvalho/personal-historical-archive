from __future__ import annotations

import argparse
import os
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import addresses
from . import bibliography
from . import db
from .config import Config
from .extract import is_supported, resolve_editor_id, resolve_encoder_id, resolve_palaeographer_id, resolve_prompt, encoder_files_for
from .ingest import (
    edit_all,
    encode_all,
    make_vision_client,
    prune_orphan_renders,
    prune_redundant_edited_dirs,
    reindex_all,
    remove_library_artifact,
    remove_render_if_orphaned,
    scan_once,
    sync_bibliography,
    watch,
    write_document_pages,
)
from .model_client import ModelClient, ModelError
from .filters import FilterError
from .sidecar import resolve_sidecar
from .doctor import ENGINES as DOCTOR_ENGINES


def _client(cfg: Config, base_url: str, timeout_s: int) -> ModelClient:
    return ModelClient(base_url, timeout_s=timeout_s)


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- commands

def cmd_scan(cfg: Config, args) -> None:
    client, pal = make_vision_client(cfg, args.palaeographer)
    print(f"palaeographer: {pal.id} ({pal.description or pal.model})")
    if getattr(args, "path", None):
        print(f"target: {args.path}")
    try:
        if args.watch:
            watch(cfg, client, pal, explicit_prompt=args.prompt, debounce_s=args.debounce,
                  path=getattr(args, "path", None))
            return
        res = scan_once(cfg, client, pal, explicit_prompt=args.prompt, reprocess=args.reprocess,
                        path=getattr(args, "path", None),
                        include_leased=getattr(args, "include_leased", False))
    finally:
        client.close()
    summary = {"ingested": 0, "skipped": 0, "error": 0}
    for r in res["results"]:
        summary[r["action"]] = summary.get(r["action"], 0) + 1
        if r["action"] == "ingested":
            print(f"  + {r['filename']} ({r['pages']} pages, prompt: {r['prompt']})")
        elif r["action"] == "error":
            print(f"  ! {r['filename']}: {r['error']}", file=sys.stderr)
    print(f"scanned {res['scanned']} file(s): {summary}")


def cmd_search(cfg: Config, args) -> None:
    conn = db.connect(cfg.db_path)
    client = _client(cfg, cfg.embed_base_url, cfg.embed_timeout_s)
    try:
        res = None
        try:
            from .search import search as run_search

            res = run_search(conn, client, cfg, args.query, mode=args.mode, limit=args.limit,
                             collection=args.collection,
                             allow_embed=bool(getattr(args, "force", False)))
        except ModelError as e:
            print(f"model error: {e}", file=sys.stderr)
            sys.exit(2)
        # Point each hit at its full page: the library file + the pha page cmd.
        if res and res.get("results"):
            from .ingest import library_page_path

            for r in res["results"]:
                doc = db.get_document(conn, r["document_id"])
                r["page_file"] = None
                if doc is None:
                    continue
                doc = dict(doc)
                pg = conn.execute(
                    "SELECT source_name FROM pages WHERE document_id=? AND page_no=?",
                    (doc["id"], r["page_no"]),
                ).fetchone()
                edited = r.get("variant") == "edited"
                r["page_file"] = library_page_path(
                    cfg, doc, r["page_no"], variant="edited" if edited else "raw",
                    source_name=pg["source_name"] if pg else None,
                    editor_id=doc.get("editor") or None)
                if r["page_file"] is not None:
                    r["page_file"] = str(r["page_file"])
    finally:
        client.close()
        conn.close()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if res.get("note"):
        print(f"note: {res['note']}")
    if not res["results"]:
        print("no results")
        return
    for i, r in enumerate(res["results"], 1):
        print(f"{i:2d}. [{r['source']:8s}][{r.get('variant','raw'):6s}] {r['filename']}  [{r['collection']}]  p.{r['page_no']}  score={r['score']}")
        print(f"     {r['snippet']}")
        if r.get("page_file"):
            edited = r.get("variant") == "edited"
            print(f"     full page: {r['page_file']}")
            print(f"               pha page {r['document_id']} {r['page_no']}" + (" --edited" if edited else ""))
    print(f"\n{len(res['results'])} result(s) in mode '{res['mode']}'")


def _resolve_doc_for_page(conn, target: str):
    """Find a document by numeric id or filename substring; returns (doc, matches)
    where matches lists candidates when the target is ambiguous."""
    from . import db as _db

    docs = _db.list_documents(conn, limit=10000)
    try:
        doc_id = int(str(target).strip())
        for d in docs:
            if d["id"] == doc_id:
                return d, []
    except ValueError:
        pass
    matches = [d for d in docs if str(target).lower() in (d["filename"] or "").lower()]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def cmd_page(cfg: Config, args) -> None:
    """Print the FULL transcription of one page: `pha page <doc> <page> [--edited]`."""
    from .ingest import library_page_path

    conn = db.connect(cfg.db_path)
    try:
        doc, matches = _resolve_doc_for_page(conn, args.doc)
        if doc is None:
            if matches:
                names = ", ".join(f"#{d['id']} {d['filename']}" for d in matches[:8])
                print(f"ambiguous document {args.doc!r} — matches: {names}", file=sys.stderr)
            else:
                print(f"no document matching {args.doc!r}", file=sys.stderr)
            sys.exit(1)
        page = conn.execute(
            "SELECT * FROM pages WHERE document_id=? AND page_no=?",
            (doc["id"], args.page)).fetchone()
        if page is None:
            print(f"document #{doc['id']} ({doc['filename']}) has no page {args.page}",
                  file=sys.stderr)
            sys.exit(1)
        edited = bool(getattr(args, "edited", False))
        editor_id = None
        text = page["raw_text"] or ""
        if edited:
            editor_id = args.editor or doc["editor"]
            # A missing edited variant is a config/state problem, not a crash: name the
            # pass that produces it. Callers (the GUI, MCP clients) show this verbatim.
            edit_hint = (f"pha edit --path {doc['dir_path']}" if doc["dir_path"] else "pha edit")
            if not editor_id:
                print("this document has no editor configured; nothing to show for --edited"
                      " — set an editor in the collection's pha.yaml",
                      file=sys.stderr)
                sys.exit(1)
            e = db.get_page_edit(conn, page["id"], editor_id)
            if e is not None and e["text"]:
                text = e["text"]
            else:
                print(f"no edited text for page {args.page} (editor {editor_id})"
                      f" — run: {edit_hint}",
                      file=sys.stderr)
                sys.exit(1)
        pf = library_page_path(cfg, doc, args.page,
                               variant="edited" if edited else "raw",
                               source_name=page["source_name"], editor_id=editor_id)
        # Stable identity + everything an external consumer needs (the render, and
        # the full variant set) so it never re-derives the sha256 join or the
        # `edited-<editor>[@model]` directory grammar itself.
        rel_path = addresses.document_rel_path(cfg, doc)
        slug = addresses.doc_slug(rel_path)
        render = addresses.render_path(cfg, doc, args.page, page["source_name"])
        total = doc["page_count"] or 0
        bib, _bib_warning = bibliography.load_bibliography(doc)
        meta = {
            "document_id": doc["id"],
            "filename": doc["filename"],
            "collection": doc["dir_path"] or "(root)",
            "source": doc["path"],
            "page_no": args.page,
            "variant": "edited" if edited else "raw",
            "editor": editor_id,
            "palaeographer": doc["palaeographer"],
            "reviewed": bool(page["reviewed_at"]),
            "page_file": str(pf) if pf else None,
            "slug": slug,
            "rel_path": rel_path,
            "sha256": doc["sha256"],
            "render": str(render) if render else None,
            "render_exists": render is not None,
            "variants": addresses.variant_files(cfg, doc, args.page, page["source_name"]),
            # Navigation: the served viewer flips pages, and these are the same
            # links so an agent can walk a document without a second lookup.
            "page_count": total,
            "prev_page": args.page - 1 if args.page > 1 else None,
            "next_page": args.page + 1 if (total and args.page < total) else None,
            # The document's bibliographic reference, when it has a sidecar.
            "reference": bibliography.compose_reference(bib) if bib else None,
            "reference_source": bib.source_path if bib else None,
            "reference_verified": (not bib.is_unverified()) if bib else None,
            "bibliography": bib.to_dict() if bib else None,
            "page_url": addresses.viewer_url(cfg.serve_base_url, slug, args.page),
            "overview_url": addresses.overview_url(cfg.serve_base_url, slug),
        }
        if getattr(args, "json", False):
            print(json.dumps({**meta, "text": text}, ensure_ascii=False, indent=2))
            return
        print(f"== {doc['filename']} — page {args.page}"
              + (f" [{editor_id}]" if edited else " [raw]") + " ==")
        print(f"   slug: {slug}")
        if pf:
            print(f"   file: {pf}")
        print("")
        print(text or "(no text)")
    finally:
        conn.close()


def cmd_cite(cfg: Config, args) -> None:
    """Print a durable citation for one page: `pha cite <doc> <page> [--edited]`.

    The citation names the exact FILLED variant (never an empty `*waiting*`
    stub) and carries the stable slug — so the footnote survives a re-scan and
    still says which reading the claim rests on.
    """
    conn = db.connect(cfg.db_path)
    try:
        doc, matches = _resolve_doc_for_page(conn, args.doc)
        if doc is None:
            if matches:
                names = ", ".join(f"#{d['id']} {d['filename']}" for d in matches[:8])
                print(f"ambiguous document {args.doc!r} — matches: {names}", file=sys.stderr)
            else:
                print(f"no document matching {args.doc!r}", file=sys.stderr)
            sys.exit(1)
        if args.editor and args.palaeographer:
            print("give only one of --editor / --palaeographer", file=sys.stderr)
            sys.exit(2)
        page = conn.execute(
            "SELECT * FROM pages WHERE document_id=? AND page_no=?",
            (doc["id"], args.page)).fetchone()
        if page is None:
            print(f"document #{doc['id']} ({doc['filename']}) has no page {args.page}",
                  file=sys.stderr)
            sys.exit(1)

        stage = "edited" if (args.edited or args.editor) else "transcription"
        wanted_id = args.editor if stage == "edited" else args.palaeographer
        variants = addresses.variant_files(cfg, doc, args.page, page["source_name"])
        selected = {k: v for k, v in variants.items() if v["stage"] == stage}
        if wanted_id:
            selected = {k: v for k, v in selected.items() if v["id"] == wanted_id}
        filled = {k: v for k, v in selected.items() if v["filled"]}

        if not filled:
            if selected:
                names = ", ".join(sorted(selected))
                print(f"the {stage} variant(s) for doc {doc['id']} p.{args.page} exist "
                      f"but are empty (waiting): {names}\n"
                      f"run the pass that fills them first "
                      f"(`pha {'edit' if stage == 'edited' else 'scan'} --path {doc['dir_path'] or '.'}`).",
                      file=sys.stderr)
            else:
                hint = f" (with id {wanted_id!r})" if wanted_id else ""
                print(f"no {stage} variant{hint} for doc {doc['id']} p.{args.page}",
                      file=sys.stderr)
            sys.exit(1)

        if len(filled) > 1:
            # Prefer the document's configured id for the stage, but never guess
            # between two filled readings: the caller must say which one.
            preferred = (doc["editor"] if stage == "edited" else doc["palaeographer"]) or None
            narrowed = {k: v for k, v in filled.items() if preferred and v["id"] == preferred}
            if narrowed:
                filled = narrowed
            if len(filled) > 1:
                listed = "\n".join(f"  {k}" for k in sorted(filled))
                flag = "--editor" if stage == "edited" else "--palaeographer"
                print(f"several filled {stage} variants for doc {doc['id']} "
                      f"p.{args.page} — choose one:\n{listed}\n  re-run with {flag} <id>",
                      file=sys.stderr)
                sys.exit(2)

        name = next(iter(filled))
        variant = filled[name]
        rel_path = addresses.document_rel_path(cfg, doc)
        slug = addresses.doc_slug(rel_path)
        label = addresses.variant_label(name)
        render = addresses.render_path(cfg, doc, args.page, page["source_name"])
        # The sidecar on disk is the source of truth, read live so an edited
        # reference shows up without a scan. Absent a sidecar this is exactly
        # the citation `pha cite` has always printed.
        bib, bib_warning = bibliography.load_bibliography(doc)
        if bib_warning:
            print(f"warning: {bib_warning}", file=sys.stderr)
        citation = bibliography.format_citation(
            bib, doc_id=doc["id"], page_no=args.page, variant_label=label,
            filename=doc["filename"])
        payload = {
            "ok": True,
            "citation": citation,
            "slug": slug,
            "rel_path": rel_path,
            "sha256": doc["sha256"],
            "document_id": doc["id"],
            "filename": doc["filename"],
            "page": args.page,
            "stage": stage,
            "variant": name,
            "label": label,
            "filled": True,
            "file": variant["file"],
            "render": str(render) if render else None,
            "render_exists": render is not None,
            # The bibliographic reference behind the citation, so a client can
            # format its own house style without re-parsing the sidecar.
            "reference": bibliography.compose_reference(bib) if bib else None,
            "reference_source": bib.source_path if bib else None,
            "reference_verified": (not bib.is_unverified()) if bib else None,
            "bibliography": bib.to_dict() if bib else None,
            # The viewer (not the bare jpg): a citation should land somewhere the
            # reader can page forward/back from.
            "url": addresses.viewer_url(cfg.serve_base_url, slug, args.page),
            "overview_url": addresses.overview_url(cfg.serve_base_url, slug),
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(citation)
        print(f"  slug:  {slug}")
        print(f"  page:  {args.page}")
        print(f"  file:  {variant['file']}")
        if bib:
            print(f"  ref:   {bib.source_path}")
        print(f"  url:   {payload['url']}")
    finally:
        conn.close()


def _bib_emit(cfg: Config, conn, args) -> None:
    """`pha bib --to-json | --to-bibtex [<doc>] [--write]` — rewrite a reference
    in the format that suits its next reader.

    MODS is what Zotero exports (a machine interchange format); JSON is the
    structured form a person edits; **BibTeX is the one an agent can draft from
    a scan** with no external tool, and a human can still correct. Without
    `--write` it prints; with it, the sidecar is written beside the document and
    the other formats are removed (they would otherwise win the lookup) unless
    `--keep-others`.
    """
    write = bool(getattr(args, "write", False))
    keep_others = bool(getattr(args, "keep_others", False))
    qualified = bool(getattr(args, "qualified", False))
    origin = getattr(args, "origin", None)
    to_bibtex = bool(getattr(args, "to_bibtex", False))
    target_fmt = "bib" if to_bibtex else "dc"

    if args.doc:
        doc, matches = _resolve_doc_for_page(conn, args.doc)
        if doc is None:
            if matches:
                names = ", ".join(f"#{d['id']} {d['filename']}" for d in matches[:8])
                print(f"ambiguous document {args.doc!r} — matches: {names}", file=sys.stderr)
            else:
                print(f"no document matching {args.doc!r}", file=sys.stderr)
            sys.exit(1)
        docs = [doc]
    else:
        docs = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()

    converted = 0
    skipped = 0
    for doc in docs:
        bib, _warning = bibliography.load_bibliography(doc)
        if bib is None:
            if args.doc:
                print(f"{doc['filename']}: no reference to convert", file=sys.stderr)
                sys.exit(1)
            skipped += 1
            continue
        text = (bibliography.to_bibtex(bib, origin=origin) if to_bibtex
                else bibliography.to_dc_json_text(bib, qualified=qualified))
        if not write:
            print(text, end="")
            return
        target = bibliography.sidecar_path_for(doc, target_fmt)
        target.write_text(text, encoding="utf-8")
        removed = []
        if not keep_others:
            for fmt in bibliography.FORMAT_PRECEDENCE:
                if fmt == target_fmt:
                    continue
                other = bibliography.sidecar_path_for(doc, fmt)
                if other.is_file():
                    other.unlink()
                    removed.append(other.name)
        converted += 1
        print(f"  {doc['filename']}: wrote {target.name}"
              + (f" (removed {', '.join(removed)})" if removed else ""))
    if write:
        sync_bibliography(cfg, conn, verbose=False)
        print(f"{converted} written as {'BibTeX' if to_bibtex else 'JSON'}"
              + (f", {skipped} without a reference" if skipped else ""))


def cmd_bib(cfg: Config, args) -> None:
    """Bibliographic references: coverage, one document's reference, or problems.

    `pha bib`            refresh the stored snapshot and summarise coverage
    `pha bib <doc>`      one document's reference, field by field
    `pha bib --check`    report broken / empty / duplicated sidecars (exit 1)

    The sidecar on disk is the source of truth; this reads it live, and also
    refreshes the DB snapshot that `pha serve` and the MCP tools read.
    """
    conn = db.connect(cfg.db_path)
    try:
        if getattr(args, "to_json", False) or getattr(args, "to_bibtex", False):
            _bib_emit(cfg, conn, args)
            return
        if getattr(args, "doc", None):
            doc, matches = _resolve_doc_for_page(conn, args.doc)
            if doc is None:
                if matches:
                    names = ", ".join(f"#{d['id']} {d['filename']}" for d in matches[:8])
                    print(f"ambiguous document {args.doc!r} — matches: {names}", file=sys.stderr)
                else:
                    print(f"no document matching {args.doc!r}", file=sys.stderr)
                sys.exit(1)
            path, fmt, conflict = bibliography.find_sidecar(doc)
            bib, err = bibliography.load_bibliography(doc)
            warning = conflict or err
            payload = {
                "ok": bib is not None,
                "document_id": doc["id"],
                "filename": doc["filename"],
                "collection": doc["dir_path"] or "(root)",
                "sidecar": str(path) if path else None,
                "source_format": fmt,
                "reference": bibliography.compose_reference(bib) if bib else None,
                "verified": (not bib.is_unverified()) if bib else None,
                "warning": warning,
                "bibliography": bib.to_dict() if bib else None,
            }
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return
            print(f"{doc['filename']}  (#{doc['id']})")
            if bib is None:
                print("  reference: none" + (f" — {warning}" if warning else ""))
                print("  (pha cite falls back to the filename-only citation)")
                return
            print(f"  reference: {payload['reference']}")
            print(f"  sidecar:   {path}  [{fmt}]")
            if bib.creators:
                print("  creators:  " + "; ".join(
                    n.name + (f" ({n.role})" if n.role else "") for n in bib.creators))
            rows = (
                ("volume", bib.part_number),
                ("host", bib.host.title if bib.host else None),
                ("imprint", ", ".join(x for x in (bib.place, bib.publisher, bib.date_issued) if x)),
                ("shelfmark", bib.shelfmark),
                ("repository", bib.repository),
                ("identifiers", "; ".join(
                    f"{i.type}:{i.value}" if i.type else i.value for i in bib.identifiers)),
                ("origin", bib.record_origin),
            )
            for label, value in rows:
                if value:
                    print(f"  {label + ':':<11}{value}")
            print("  verified:  " + ("yes" if payload["verified"]
                                    else "NO — machine-drafted; confirm it or fix the source"))
            if warning:
                print(f"  warning:   {warning}")
            return

        stats = sync_bibliography(cfg, conn, verbose=False)
        docs = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
        entries: list[dict] = []
        problems: list[tuple] = []
        for d in docs:
            path, fmt, conflict = bibliography.find_sidecar(d)
            bib, err = bibliography.load_bibliography(d)
            entries.append({
                "document_id": d["id"],
                "filename": d["filename"],
                "collection": d["dir_path"] or "(root)",
                "sidecar": str(path) if path else None,
                "source_format": fmt,
                "reference": bibliography.compose_reference(bib) if bib else None,
                "verified": (not bib.is_unverified()) if bib else None,
                "warning": conflict or err,
            })
            if conflict:
                problems.append((d["id"], d["filename"], "two sidecars", conflict))
            elif err:
                problems.append((d["id"], d["filename"], "unreadable sidecar", err))
            elif path is not None and bib is None:
                problems.append((d["id"], d["filename"], "empty sidecar", str(path)))

        referenced = [e for e in entries if e["reference"]]
        unverified = [e for e in entries if e["verified"] is False]
        missing = [e for e in entries if not e["reference"]]

        if getattr(args, "json", False):
            print(json.dumps({
                "ok": not problems,
                "documents": len(entries),
                "referenced": len(referenced),
                "unreferenced": len(missing),
                "unverified": len(unverified),
                "refreshed": stats["parsed"],
                "problems": [{"document_id": i, "filename": f, "kind": k, "detail": det}
                             for i, f, k, det in problems],
                "entries": entries,
            }, ensure_ascii=False, indent=2))
            return

        if getattr(args, "check", False):
            if problems:
                for _i, name, kind, detail in problems:
                    print(f"{name}: {kind} — {detail}")
                sys.exit(1)
            print(f"bibliographic sidecars: {len(referenced)} reference(s), "
                  f"all readable and unambiguous")
            return

        print(f"bibliographic references: {len(referenced)}/{len(entries)} documents"
              f"   ({stats['parsed']} refreshed)")
        if unverified:
            print(f"  {len(unverified)} unverified (machine-drafted) — marked in `pha cite`")
        if problems:
            print("\nproblems")
            for _i, name, kind, detail in problems:
                print(f"  {name}: {kind} — {detail}")
        if missing:
            print(f"\nno reference ({len(missing)})")
            for e in missing:
                print(f"  #{e['document_id']:<4} {e['collection']}/{e['filename']}")
    finally:
        conn.close()


def cmd_open(cfg: Config, args) -> None:
    """Open a page or archive file in the OS-default application.

    `pha open <doc> <page> [--edited]` resolves a page exactly like `pha page`
    and opens its library .md; `pha open <path>` opens an existing markdown
    file anywhere under the archive (a library page, a note, ...). The OS
    decides which app handles the file — e.g. your markdown editor. pha never
    edits the text itself; the human edits and `pha review` imports."""
    from .ingest import library_page_path

    conn = None
    try:
        if args.page is None:
            path = Path(args.doc).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
        else:
            conn = db.connect(cfg.db_path)
            doc, matches = _resolve_doc_for_page(conn, args.doc)
            if doc is None:
                if matches:
                    names = ", ".join(f"#{d['id']} {d['filename']}" for d in matches[:8])
                    print(f"ambiguous document {args.doc!r} — matches: {names}", file=sys.stderr)
                else:
                    print(f"no document matching {args.doc!r}", file=sys.stderr)
                sys.exit(1)
            page = conn.execute(
                "SELECT * FROM pages WHERE document_id=? AND page_no=?",
                (doc["id"], args.page)).fetchone()
            if page is None:
                print(f"document #{doc['id']} ({doc['filename']}) has no page {args.page}",
                      file=sys.stderr)
                sys.exit(1)
            edited = bool(getattr(args, "edited", False))
            editor_id = getattr(args, "editor", None) or (doc["editor"] if edited else None)
            path = library_page_path(cfg, doc, args.page,
                                     variant="edited" if edited else "raw",
                                     source_name=page["source_name"], editor_id=editor_id)
            if path is None:
                print(f"no library file on disk for {doc['filename']} page {args.page}"
                      + (" (edited)" if edited else "")
                      + " — run `pha scan`/`pha export` first", file=sys.stderr)
                sys.exit(1)
        target = path.resolve()
        arc = Path(cfg.archive_dir).resolve()
        try:
            target.relative_to(arc)
        except ValueError:
            print(f"{target} is not inside the archive ({arc})", file=sys.stderr)
            sys.exit(1)
        if not target.is_file():
            print(f"no such file: {target}", file=sys.stderr)
            sys.exit(1)
        if target.suffix.lower() not in (".md", ".yaml", ".yml"):
            print(f"can only open markdown or yaml files in the archive: {target}", file=sys.stderr)
            sys.exit(1)
        _open_with_os_default(target)
    finally:
        if conn is not None:
            conn.close()


def _open_with_os_default(path: Path) -> None:
    """Open `path` with the OS-default application (fire and forget).

    macOS uses `open`, Linux `xdg-open`, Windows `os.startfile`; the OS
    decides which app handles the file (e.g. a markdown editor for .md).
    Exits non-zero with a message when no opener is available (headless or
    remote runs) or the opener fails."""
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        r = subprocess.run([opener, str(path)], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print(f"no '{opener}' opener on this system — open {path} yourself", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"'{opener}' did not return — open {path} yourself", file=sys.stderr)
        sys.exit(1)
    if r.returncode != 0:
        print(f"could not open {path}: {(r.stderr or r.stdout or '').strip() or 'opener failed'}",
              file=sys.stderr)
        sys.exit(1)
    print(f"opened: {path}")


def cmd_pending(cfg: Config, args) -> None:
    """List library page files a human edited that are not yet imported into the DB.

    This is where the database is out of sync with the library files on disk: the
    file was edited (in an external editor, say) but `pha review` has not imported
    the correction. `--doc N` limits the walk to one document (cheap); otherwise
    every library page file is checked. The `--json` form is what the PHA view
    uses to mark pages that need attention.
    """
    from .ingest import pending_review_files

    conn = db.connect(cfg.db_path)
    try:
        pending = pending_review_files(cfg, conn, doc_id=getattr(args, "doc", None))
        needs_edit = any(x["variant"].startswith("transcription-") for x in pending)
        if getattr(args, "json", False):
            print(json.dumps({
                "ok": True,
                "count": len(pending),
                # the pha passes that bring the DB back in sync, in order
                "needs": {"review": bool(pending), "edit": needs_edit, "reindex": bool(pending)},
                "pending": [
                    {"document_id": x["document_id"], "page_no": x["page_no"],
                     "variant": x["variant"], **({"editor": x["editor"]} if "editor" in x else {})}
                    for x in pending
                ],
            }, ensure_ascii=False, indent=2))
            return
        if not pending:
            print("up to date: no library corrections are waiting to be imported")
            return

        def get_doc(d_id):
            return conn.execute(
                "SELECT filename, dir_path FROM documents WHERE id=?", (d_id,)).fetchone()

        print("\n".join(_pending_summary_lines(pending, get_doc)))
    finally:
        conn.close()


def cmd_info(cfg: Config, args) -> None:
    """Print the archive's resolved paths (read-only and fast).

    Config only — it opens no database and probes no engines. That matters for
    callers that just need to LOCATE the archive (the PHA view, an agent):
    `pha status` walks every library page file (minutes on a large archive) and
    `pha doctor` probes the engine binaries, while this is just config loading.
    """
    # `archive_source` says WHY this archive: env / legacy .env / config.yaml /
    # the default. A wrong archive is otherwise silent (an empty archive is a
    # valid archive), so this is the answer to "which one am I actually on?".
    if os.environ.get("PHA_ARCHIVE_DIR"):
        source = "PHA_ARCHIVE_DIR environment variable"
    elif _dotenv_archive_dir(cfg) is not None:
        source = "PHA_ARCHIVE_DIR in .env (legacy)"
    elif (cfg.root / "config.yaml").exists():
        source = "paths.archive_dir in config.yaml"
    else:
        source = "default (project root)"
    info = {
        "archive_dir": str(cfg.archive_dir),
        "archive_source": source,
        "db_path": str(cfg.db_path),
        "dropbox": str(cfg.dropbox),
        "library": str(cfg.library),
        "renders": str(cfg.renders),
        "notes": str(cfg.notes),
        "models": str(cfg.models_dir),
        "palaeographers": str(cfg.palaeographers_dir),
        "editors": str(cfg.editors_dir),
        "encoders": str(cfg.encoders_dir),
        "filters": str(cfg.filters_dir),
    }
    # WHERE the tool itself is on this machine: the archive's agent-facing
    # trace (pha-location.md / .pha/location.json) says the same thing, but an
    # agent that can already run pha should not have to read a file to learn
    # how to run pha. Best-effort: never let discovery break `pha info`.
    try:
        from .location import build_location

        loc = build_location(cfg)
        info["pha_version"] = loc["pha"]["version"]
        info["pha_command"] = loc["pha"]["command"] or " ".join(loc["pha"]["module"])
        info["pha_module_command"] = " ".join(loc["pha"]["module"])
        info["pha_python"] = loc["pha"]["python"]
        info["location_file"] = loc["location_file"]
    except Exception:  # noqa: BLE001 - info must stay read-only and unbreakable
        pass
    if getattr(args, "json", False):
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    for key, value in info.items():
        print(f"{key}: {value}")


def cmd_config(cfg: Config, args) -> None:
    """Show (and if needed generate) how a document/collection is processed.

    Resolves the effective palaeographer / editor / prompt, preferring a `pha.yaml`
    sidecar over the legacy `palaeographer` / `editor` selection files. With
    `--write` — or automatically when no pha.yaml exists or legacy selection files
    are still present — it generates the pha.yaml from the resolved configuration,
    so the collection stops relying on the legacy layout.

    Encoders are deliberately not written: leaving them out keeps the current
    directory-discovery behaviour for the encoder stage.
    """
    import yaml

    from .migrate import _SCHEMA_MODELINE
    from .sidecar import resolve_stages

    conn = None
    document = None
    try:
        if getattr(args, "path", None):
            p = (cfg.dropbox / args.path).resolve()
            is_dir = p.is_dir()
            sel_dir, stem = (p, None) if is_dir else (p.parent, p.stem)
            label = args.path
        else:
            if not getattr(args, "doc", None):
                print("give --doc <id|filename substring> or --path <dropbox-relative path>",
                      file=sys.stderr)
                sys.exit(2)
            conn = db.connect(cfg.db_path)
            doc, matches = _resolve_doc_for_page(conn, args.doc)
            if doc is None:
                if matches:
                    names = ", ".join(f"#{d['id']} {d['filename']}" for d in matches[:8])
                    print(f"ambiguous document {args.doc!r} — matches: {names}", file=sys.stderr)
                else:
                    print(f"no document matching {args.doc!r}", file=sys.stderr)
                sys.exit(1)
            p = Path(doc["path"]).resolve()
            sel_dir, stem = p.parent, p.stem
            label = doc["filename"]
            rel_path = addresses.document_rel_path(cfg, doc)
            document = {
                "id": doc["id"],
                "filename": doc["filename"],
                "slug": addresses.doc_slug(rel_path),
                "rel_path": rel_path,
                "sha256": doc["sha256"],
                "page_count": doc["page_count"],
            }

        resolved = resolve_stages(cfg, sel_dir, stem=stem, sidecar_stem=stem)
        sc = resolved.pop("sidecar")

        # the legacy layout: a `palaeographer` / `editor` file next to the documents
        legacy = [f.name for f in
                  [sel_dir / n for n in ("palaeographer", "palaeographer.txt", "palaeographer.md",
                                         "editor", "editor.txt", "editor.md")]
                  if f.is_file()]
        target = sc.source if sc.source is not None else (sel_dir / "pha.yaml")

        generated = False
        # Generate only when the collection has no pha.yaml anywhere on its chain.
        # Regenerating whenever the legacy files merely still exist would clobber a
        # pha.yaml the historian has since edited (the sidecar already wins over
        # them), so the write is idempotent.
        if getattr(args, "write", False) and sc.source is None:
            body: dict = {}
            # `model:` in pha.yaml is the model ID (the file stem in models/), which
            # is `model_ref` — not the server-side model name in `model`.
            if resolved["palaeographer"]["id"]:
                body["palaeographer"] = {
                    "rules": resolved["palaeographer"]["id"],
                    "model": (resolved["palaeographer"]["model_ref"]
                              or resolved["palaeographer"]["model"]),
                }
            if resolved["editor"]["id"]:
                body["editor"] = {
                    "rules": resolved["editor"]["id"],
                    "model": (resolved["editor"]["model_ref"] or resolved["editor"]["model"]),
                }
            text = _SCHEMA_MODELINE + "\n" + yaml.safe_dump(
                body, sort_keys=False, default_flow_style=False, allow_unicode=True)
            target = sel_dir / "pha.yaml"
            target.write_text(text, encoding="utf-8")
            generated = True

        content = target.read_text(encoding="utf-8") if target.is_file() else None
        # A document/collection that inherits its pha.yaml from an upper folder is
        # NEVER given one of its own: that would freeze the inherited values locally
        # and shadow later changes to the parent. `inherited` tells the reader the
        # file shown is in scope from above, not owned by this directory.
        inherited = (sc.source is not None
                     and sc.source.parent.resolve() != sel_dir.resolve())
        if getattr(args, "json", False):
            print(json.dumps({
                "ok": True, "target": label, "path": str(target),
                "document_dir": str(sel_dir), "inherited": inherited,
                "generated": generated, "legacy_files": legacy,
                "document": document,
                "resolved": resolved, "content": content,
            }, ensure_ascii=False, indent=2))
            return
        print(("generated: " if generated else "config: ") + str(target))
        if inherited:
            print(f"  (inherited from an upper folder — this directory has no pha.yaml of its own)")
        if legacy and not generated:
            print(f"  (note: legacy selection file(s) present: {', '.join(legacy)})")
        print("")
        print(content or "(no pha.yaml — run `pha config --doc … --write` to generate it)")
    finally:
        if conn is not None:
            conn.close()


def _pending_summary_lines(pending: list[dict], get_doc) -> list[str]:
    """Build the 'corrections not yet imported' section of `pha status`.

    Groups pending corrections (from `pending_review_files`) by document so the
    user sees WHICH documents (and which pages) need review, not just a count.
    `get_doc(document_id)` returns a row-like with filename/dir_path or None.
    """
    if not pending:
        return []
    total_pages = len({(x['document_id'], x['page_no']) for x in pending})
    by_doc: dict[int, dict] = {}
    for x in pending:
        by_doc.setdefault(x['document_id'], set()).add(x['page_no'])
    lines = [
        f"  ✏️  {total_pages} page(s) with corrections in the library files not yet imported",
        f"     (across {len(by_doc)} document(s)):",
    ]
    for d_id in sorted(by_doc):
        doc = get_doc(d_id)
        name = doc["filename"] if doc else f"doc#{d_id}"
        col = doc["dir_path"] if doc and doc["dir_path"] else "(root)"
        pages = ", ".join(str(p) for p in sorted(by_doc[d_id]))
        lines.append(f"       #{d_id:<3d} [{col}] {name}  — pages {pages}")
    if any(x.get("variant", "").startswith("transcription-") for x in pending):
        lines.append("     Run:  pha review   (imports your corrections)")
        lines.append("          then  pha edit   — you corrected a TRANSCRIPTION, so the")
        lines.append("                editor re-runs on your corrected text")
        lines.append("          then  pha reindex")
    else:
        lines.append("     Run:  pha review   (imports your corrections, then pha reindex)")
    return lines


_STATUS_ORDER = ("done", "processing", "error", "pending")


def _status_summary(statuses: dict) -> str:
    """Compact `n status` list, e.g. '5 done · 2 processing'."""
    return " · ".join(f"{statuses[st]} {st}" for st in _STATUS_ORDER if statuses.get(st))


def _collection_status_line(statuses: dict, total: int) -> str:
    """Human status summary for one collection's archived documents."""
    if total <= 0:
        return "no documents in archive"
    noun = "document" if total == 1 else "documents"
    present = [st for st in _STATUS_ORDER if statuses.get(st)]
    if len(present) == 1:
        st = present[0]
        if statuses[st] == total:
            return f"{total} {noun} ({st})"
        return f"{total} {noun} ({statuses[st]} {st})"
    return f"{total} {noun} ({_status_summary(statuses)})"


def _term_width(default: int = 100) -> int:
    """Terminal width in columns for output that must never wrap.

    Uses the real terminal width when stdout is a tty (so long lines are
    actually trimmed to fit), otherwise a generous default (piped/redirected
    output needn't wrap)."""
    import shutil
    try:
        w = shutil.get_terminal_size(fallback=(default, 24)).columns
    except Exception:
        w = default
    return max(w, 40)


def _fit(line: str, width: int) -> str:
    """Truncate `line` to `width` columns so it never wraps."""
    if len(line) <= width:
        return line
    return line[: width - 1] + "…"


def _snip(names: list[str], width: int, max_names: int = 3, name_len: int = 32) -> str:
    """Short, width-bounded listing of file names for the 'new' leaves.

    Shows as many names as fit within `width` columns and always keeps the
    trailing '+N more' count so the total is never lost to truncation."""
    names = sorted(names)
    if not names:
        return ""
    shown: list[str] = []
    for name in names:
        if len(shown) >= max_names:
            break
        disp = name if len(name) <= name_len else name[: name_len - 1] + "…"
        cand = ", ".join(shown + [disp])
        more = len(names) - len(shown) - 1
        if len(cand) + (len(f", … +{more}") if more else 0) > width:
            break
        shown.append(disp)
    more = len(names) - len(shown)
    text = ", ".join(shown)
    if more > 0:
        text += f", … +{more}"
    return text


def cmd_status(cfg: Config, args) -> None:
    conn = db.connect(cfg.db_path)
    try:
        width = _term_width()

        # ---- gather everything up front so rendering stays simple --------
        # One grouped scan of `chunks` feeds both the per-document lines and the
        # overview totals (the chunks table carries the embeddings, so every
        # extra pass over it is expensive).
        stats = db.chunk_stats(conn)
        s = db.summary(conn, chunk_stats=stats)
        docs_status = s["documents"] or {}
        total_docs = sum(docs_status.values())

        archived: dict[str, dict[str, int]] = {}
        for r in conn.execute(
            "SELECT COALESCE(NULLIF(dir_path, ''), '(root)') AS col, status, COUNT(*) n "
            "FROM documents GROUP BY col, status"
        ):
            archived.setdefault(r["col"], {})[r["status"]] = r["n"]

        from .ingest import discover, pending_review_files

        known = {r["path"] for r in conn.execute("SELECT path FROM documents").fetchall()}
        try:
            units = discover(cfg.dropbox, cfg.dir_documents, exclude=[cfg.inbox])
        except Exception:
            units = []
        unscanned: dict[str, list[str]] = {}
        for u in (u for u in units if str(u) not in known):
            rel = u.relative_to(cfg.dropbox)
            key = str(rel.parent) if str(rel.parent) != "." else "(root)"
            unscanned.setdefault(key, []).append(rel.name)
        total_new = sum(len(v) for v in unscanned.values())

        # documents parked in the inbox (on hold): never scanned, shown as held.
        holds: dict[str, list[str]] = {}
        try:
            hold_units = discover(cfg.dropbox, cfg.dir_documents, root=cfg.inbox)
        except Exception:
            hold_units = []
        for u in hold_units:
            rel = u.relative_to(cfg.inbox)
            key = str(rel.parent) if str(rel.parent) != "." else "(inbox root)"
            holds.setdefault(key, []).append(rel.name)
        total_hold = sum(len(v) for v in holds.values())

        docs = db.list_documents(conn, limit=10000)
        stat_w = max((len(str(d["status"])) for d in docs), default=0)
        stat_w = max(stat_w, len("processing"))
        docs_by_key: dict[str, list] = {}
        for d in docs:
            docs_by_key.setdefault(d["dir_path"] or "(root)", []).append(d)

        try:
            pending = pending_review_files(cfg, conn)
        except Exception:
            pending = []

        if getattr(args, "json", False):
            # The same numbers the text report below shows, from the same computation —
            # so an agent or a view never has to re-derive "what is not scanned yet"
            # (document units, image-directories, sidecars and the inbox exclusion are
            # the CLI's rules, not a caller's).
            keys = sorted(set(archived) | set(unscanned), key=lambda k: (k == "(root)", k))
            print(json.dumps({
                "ok": True,
                "archive": str(cfg.db_path),
                "documents": total_docs,
                "statuses": docs_status,
                "pages": s["pages_done"],
                "chunks": s["chunks"],
                "chunks_embedded": s["chunks_embedded"],
                "new": total_new,
                "on_hold": total_hold,
                "pending_review": len(pending),
                "collections": [
                    {"dir_path": k,
                     "documents": sum(archived.get(k, {}).values()),
                     "new": len(unscanned.get(k, []))}
                    for k in keys
                ],
                "unscanned": [
                    {"dir_path": k, "count": len(v), "documents": sorted(v)}
                    for k, v in sorted(unscanned.items(), key=lambda kv: (kv[0] == "(root)", kv[0]))
                ],
                "in_inbox": [
                    {"dir_path": k, "count": len(v), "documents": sorted(v)}
                    for k, v in sorted(holds.items(), key=lambda kv: (kv[0] == "(inbox root)", kv[0]))
                ],
            }, ensure_ascii=False, indent=2))
            return

        # ---- render ------------------------------------------------------
        print(f"archive: {cfg.db_path}")
        print()
        print("overview")
        if total_docs:
            print(f"  documents: {total_docs}   ({_status_summary(docs_status)})")
        else:
            print("  documents: none")
        print(f"  pages:     {s['pages_done']} extracted")
        print(f"  chunks:    {s['chunks']} indexed   ({s['chunks_embedded']} embedded)")
        if total_new:
            print(f"  new:       {total_new} file(s) not yet scanned")
        if total_hold:
            print(f"  inbox:     {total_hold} file(s) on hold (not scanned)")

        keys = sorted(set(archived) | set(unscanned), key=lambda k: (k == "(root)", k))
        if keys:
            print()
            print("collections")
            for key in keys:
                display = key[len("collections/"):] if key.startswith("collections/") else key
                print(_fit(f"  {display}", width))
                sts = archived.get(key, {})
                n_docs = sum(sts.values())
                if n_docs:
                    print(_fit(f"    {_collection_status_line(sts, n_docs)}", width))
                for d in sorted(docs_by_key.get(key, []), key=lambda d: d["id"]):
                    print(_fit(f"    #{d['id']:>3d}  {d['status']:<{stat_w}}  {d['filename']}", width))
                    meta = [d["kind"], f"{d['page_count'] or 0} pages"]
                    if d["palaeographer"]:
                        meta.append(d["palaeographer"])
                    cs = stats.get(d["id"])
                    kw = False
                    if cs and cs["chunks"]:
                        if cs["embedded"] == cs["chunks"]:
                            meta.append(f"{cs['chunks']} chunks")
                        else:
                            meta.append(f"{cs['chunks']} chunks ({cs['embedded']} embedded)")
                            kw = True
                    elif d["status"] == "done":
                        # `done` with no chunks at all: the document was reported
                        # complete while its index was never written (or was lost).
                        meta.append("0 chunks — NOT INDEXED")
                    if d["status"] == "error" and d["error"]:
                        meta.append(f"error: {d['error'][:40]}")
                    meta.append(f"updated {_fmt_ts(d['updated_at'])}")
                    line = f"      {' · '.join(meta)}"
                    if kw:
                        line += "   [keyword-only — run pha reindex]"
                    print(_fit(line, width))
                if unscanned.get(key):
                    names = unscanned[key]
                    prefix = f"    ~ {len(names)} new  ("
                    listing = _snip(names, width - len(prefix) - 1)
                    print(_fit(prefix + listing + ")", width))

        if holds:
            print()
            print("on hold (inbox)")
            for hkey in sorted(holds, key=lambda k: (k == "(inbox root)", k)):
                display_h = hkey[len("collections/"):] if hkey.startswith("collections/") else hkey
                print(_fit(f"  {display_h}", width))
                names = holds[hkey]
                prefix = f"    {len(names)} file(s)  ("
                listing = _snip(names, width - len(prefix) - 1)
                print(_fit(prefix + listing + ")", width))
            print("  →  run `pha inbox --move` to put them in the dropbox, then `pha scan`")

        # Documents lent to another machine (: the model-server lock cannot span
        # two boxes, so a lease is how this archive knows to keep its hands off.
        try:
            from . import handoff as _ho
            out_lines = _ho.status_lines(cfg)
        except Exception:  # noqa: BLE001 - never let status fail over this
            out_lines = []
        if out_lines:
            print()
            print("out on hand-over")
            for line in out_lines:
                print(f"  {line}")

        if pending:
            print()
            for line in _pending_summary_lines(pending, lambda d_id: db.get_document(conn, d_id)):
                print(line)

        # `done` is written after the editor and indexer (ingest.ingest_file), so
        # a NEW document cannot end up done-without-chunks -- but archives that
        # predate that fix still hold such rows (doc 57, documenta-indica,
        # 2026-09-15: done, 961 pages, 0 chunks, invisible to a status-only check).
        unindexed = [d for d in docs
                     if d["status"] == "done" and (d["page_count"] or 0) > 0
                     and not (stats.get(d["id"]) or {}).get("chunks")]
        if unindexed:
            print()
            print(f"  ⚠  {len(unindexed)} document(s) marked done with no index "
                  "(search cannot see them):")
            for d in sorted(unindexed, key=lambda d: d["id"]):
                print(_fit(f"       #{d['id']:>3d}  [{d['dir_path'] or '(root)'}] "
                           f"{d['filename']}  — {d['page_count']} page(s)", width))
            print("     Run:  pha reindex   (or `pha edit --path <doc>` to repair)")
    finally:
        conn.close()


def _inbox_held(cfg: Config) -> list:
    """Document units parked in the inbox (absolute paths), or [] if absent."""
    from .ingest import discover
    try:
        return discover(cfg.dropbox, cfg.dir_documents, root=cfg.inbox)
    except Exception:
        return []


def _move_into(src: Path, dst: Path) -> None:
    """Move `src` to `dst`, MERGING when `dst` already exists as a directory
    (so `inbox/collections/COLX` merges into `dropbox/collections/COLX` rather
    than nesting). An existing file of the same name is replaced by `src`."""
    if src.is_dir():
        if dst.exists() and dst.is_dir():
            for child in src.iterdir():
                _move_into(child, dst / child.name)
            try:
                src.rmdir()  # prune the now-empty source dir
            except OSError:
                pass
            return
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        shutil.move(str(src), str(dst))
        return
    # src is a file
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def _inbox_units(cfg: Config) -> list[Path]:
    """Document units parked in the inbox (absolute paths): files and
    image-directories, the same units `discover()` would hand a scan."""
    return _inbox_held(cfg)


def _inbox_resolve(cfg: Config, rel: str) -> Path:
    """Resolve an inbox-relative path, refusing anything that escapes the inbox."""
    root = cfg.inbox.resolve()
    cand = (cfg.inbox / rel).resolve()
    if cand != root and root not in cand.parents:
        raise ValueError(f"{rel!r} is outside the inbox")
    if cand.name.startswith("."):
        raise ValueError(f"{rel!r} is a dot-path; refusing to touch it")
    return cand


def _inbox_file_count(path: Path) -> int:
    """Files a move of `path` would relocate (dot-files stay behind)."""
    if path.is_file():
        return 1
    return sum(1 for p in path.rglob("*") if p.is_file() and not p.name.startswith("."))


def _inbox_tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _inbox_plan(cfg: Config, rel: str | None) -> list[tuple[Path, Path]]:
    """`(src, dst)` pairs for moving one inbox entry — or the whole inbox — into
    the dropbox, preserving the relative layout. Raises ValueError on a bad path."""
    if rel:
        src = _inbox_resolve(cfg, rel)
        if not src.exists():
            raise ValueError(f"nothing in the inbox at {rel!r}")
        return [(src, cfg.dropbox / src.relative_to(cfg.inbox))]
    if not cfg.inbox.is_dir():
        return []
    return [(entry, cfg.dropbox / entry.name)
            for entry in sorted(cfg.inbox.iterdir())
            if not entry.name.startswith(".")]


def _inbox_execute(cfg: Config, plan: list[tuple[Path, Path]]) -> None:
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _move_into(src, dst)


def _handoff_inbox_targets(cfg: Config, targets: list, force: bool = False) -> list:
    """Resolve `handoff out` targets, relocating any `inbox/<rel>` entry into the
    dropbox first (mirroring its relative layout) so the hand-out then runs
    normally.

    Only entries NAMED on the command line move — `handoff out` never relocates
    the whole inbox. A dropbox collision is REFUSED rather than silently
    overwriting the file already there (`--force-inbox` overrides)."""
    from .handoff import HandoffError

    resolved: list[str] = []
    for t in targets:
        if t == "inbox" or t == "inbox/":
            raise HandoffError(
                "name what to hand over (e.g. `inbox/collections/COLX`) — "
                "`handoff out` never moves the whole inbox")
        if not t.startswith("inbox/"):
            resolved.append(t)
            continue
        rel = t[len("inbox/"):].strip("/")
        if not rel:
            raise HandoffError(f"nothing named in the inbox target {t!r}")
        try:
            plan = _inbox_plan(cfg, rel)
        except ValueError as e:
            raise HandoffError(str(e)) from e
        if not plan:
            raise HandoffError(f"nothing in the inbox at {rel!r}")
        for src, dst in plan:
            if dst.exists() and not force:
                raise HandoffError(
                    f"refusing to move inbox/{src.relative_to(cfg.inbox)}: "
                    f"dropbox/{dst.relative_to(cfg.dropbox)} already exists "
                    f"(pass --force-inbox to replace it)")
        _inbox_execute(cfg, plan)
        for src, dst in plan:
            print(f"  → moved inbox/{src.relative_to(cfg.inbox)}  →  "
                  f"dropbox/{dst.relative_to(cfg.dropbox)}", flush=True)
            resolved.append(dst.relative_to(cfg.dropbox).as_posix())
    return resolved


def _inbox_json(cfg: Config) -> dict:
    """Structured inbox listing: one group per directory holding parked units, the
    documents in it, and the totals a move would carry."""
    held = _inbox_units(cfg)
    groups: dict[str, dict] = {}
    for p in held:
        rel = p.relative_to(cfg.inbox)
        key = "" if str(rel.parent) == "." else rel.parent.as_posix()
        label = "(inbox root)" if key == "" else key[len("collections/"):] if key.startswith("collections/") else key
        g = groups.setdefault(key, {"rel_path": key, "label": label, "documents": 0, "files": 0,
                                    "bytes": 0, "units": []})
        g["documents"] += 1
        g["units"].append({
            "rel_path": rel.as_posix(),
            "name": p.name,
            "kind": p.suffix.lstrip(".").lower() if p.is_file() else "folder",
            "bytes": _inbox_tree_bytes(p),
        })
    for key, g in groups.items():
        d = cfg.inbox if key == "" else cfg.inbox / key
        g["files"] = _inbox_file_count(d) if d.exists() else 0
        g["bytes"] = sum(u["bytes"] for u in g["units"])
        g["units"].sort(key=lambda u: u["name"])
        g["move_target"] = "dropbox/" + key if key else "dropbox/"
    total_files = _inbox_file_count(cfg.inbox) if cfg.inbox.is_dir() else 0
    return {
        "ok": True,
        "inbox": str(cfg.inbox),
        "dropbox": str(cfg.dropbox),
        "exists": cfg.inbox.is_dir(),
        "documents": len(held),
        "files": total_files,
        "bytes": _inbox_tree_bytes(cfg.inbox) if cfg.inbox.is_dir() else 0,
        "collections": [groups[k] for k in sorted(groups, key=lambda k: (k == "", k))],
    }


def cmd_inbox(cfg: Config, args) -> None:
    """Manage documents parked ON HOLD in the inbox.

    Documents sitting in <archive_dir>/inbox are never scanned; `pha status`
    reports them as 'on hold'. `pha inbox` lists them; `pha inbox --dry-run`
    shows the move plan; `pha inbox --move` relocates them into the dropbox
    (preserving the relative layout) so a following `pha scan` ingests them.
    An optional PATH (relative to the inbox) scopes both the plan and the move
    to one document or one folder — what the Harness view uses to move a single
    selected item."""
    rel = getattr(args, "path", None)
    move = bool(getattr(args, "move", False))
    dry = bool(getattr(args, "dry_run", False))
    as_json = bool(getattr(args, "json", False))

    if (move or dry) and rel:
        try:
            plan = _inbox_plan(cfg, rel)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
    else:
        plan = _inbox_plan(cfg, None)

    # --dry-run takes precedence: show the move plan without touching anything.
    if dry:
        if not plan:
            if as_json:
                print(json.dumps({"ok": True, "would_move": [], "files": 0}, indent=2))
            else:
                print("inbox is empty (nothing to move)")
            return
        pairs = [{"from": f"inbox/{src.relative_to(cfg.inbox).as_posix()}",
                  "to": f"dropbox/{dst.relative_to(cfg.dropbox).as_posix()}",
                  "files": _inbox_file_count(src)} for src, dst in plan]
        if as_json:
            print(json.dumps({"ok": True, "path": rel, "files": sum(p["files"] for p in pairs),
                              "would_move": pairs}, indent=2, ensure_ascii=False))
            return
        if rel:
            print(f"would move {sum(p['files'] for p in pairs)} file(s) from the inbox:")
        else:
            print(f"would move {len(_inbox_units(cfg))} file(s) from the inbox into the dropbox:")
        for p in pairs:
            print(f"  {p['from'][len('inbox/'):]}  →  {p['to']}")
        print("  (run `pha inbox --move` to move them, then `pha scan`)")
        return

    if move:
        if not plan:
            if as_json:
                print(json.dumps({"ok": True, "moved": [], "files": 0}, indent=2))
            else:
                print("inbox is empty (nothing to move)")
            return
        units_before = len(_inbox_units(cfg))  # the tally `pha status` reports
        moved = [{"from": f"inbox/{src.relative_to(cfg.inbox).as_posix()}",
                  "to": f"dropbox/{dst.relative_to(cfg.dropbox).as_posix()}",
                  "files": _inbox_file_count(src)} for src, dst in plan]
        _inbox_execute(cfg, plan)
        if as_json:
            print(json.dumps({"ok": True, "path": rel, "files": sum(m["files"] for m in moved),
                              "moved": moved}, indent=2, ensure_ascii=False))
            return
        if rel:
            print(f"moved {sum(m['files'] for m in moved)} file(s): {moved[0]['from']}  →  {moved[0]['to']}")
        else:
            print(f"moved {units_before} file(s) from the inbox into the dropbox")
        print("  →  run `pha scan` to ingest them")
        return

    held = _inbox_units(cfg)
    if as_json:
        print(json.dumps(_inbox_json(cfg), indent=2, ensure_ascii=False))
        return
    if not held:
        print("inbox is empty — park documents for later in " + str(cfg.inbox))
        return

    width = _term_width()
    holds: dict[str, list[str]] = {}
    for p in held:
        r = p.relative_to(cfg.inbox)
        key = str(r.parent) if str(r.parent) != "." else "(inbox root)"
        holds.setdefault(key, []).append(r.name)
    print("inbox (on hold, never scanned): " + str(cfg.inbox))
    for key in sorted(holds, key=lambda k: (k == "(inbox root)", k)):
        d = key[len("collections/"):] if key.startswith("collections/") else key
        print(_fit(f"  {d}", width))
        names = holds[key]
        prefix = f"    {len(names)} file(s)  ("
        listing = _snip(names, width - len(prefix) - 1)
        print(_fit(prefix + listing + ")", width))
    print("  →  `pha inbox --move` to put them in the dropbox, then `pha scan`")

def cmd_filters(cfg: Config, args) -> None:
    """List the archive's stage filters (`filters/<id>/`)."""
    from .filters import HOOK_KINDS, FilterError, discover_filters

    try:
        found = discover_filters(cfg.filters_dir)
    except FilterError as e:
        print(f"! {e}", file=sys.stderr)
        sys.exit(2)
    if not found:
        print(f"no filters in {cfg.filters_dir}")
        print("  add one by copying filters/_sample/ to filters/<my-filter>/")
        return
    if args.json:
        print(json.dumps({
            "filters_dir": str(cfg.filters_dir),
            "filters": [
                {"name": f.name, "accepts": f.accepts, "returns": f.returns,
                 "timeout_s": f.timeout_s, "params": f.params, "inputs": f.inputs,
                 "description": f.description, "path": str(f.path)}
                for f in sorted(found.values(), key=lambda x: x.name)
            ],
        }, indent=2, ensure_ascii=False))
        return
    print(f"filters ({cfg.filters_dir})")
    for f in sorted(found.values(), key=lambda x: x.name):
        hooks = [h for h, k in HOOK_KINDS.items()
                 if f.accepts in ("any", k) and f.returns in ("none", k)]
        print(f"  {f.name}")
        if f.description:
            print(f"    {f.description}")
        print(f"    accepts {f.accepts} · returns {f.returns} · hooks: {', '.join(hooks)}")
        if f.params:
            print(f"    params: {', '.join(sorted(f.params))}")
        if f.inputs:
            print(f"    inputs: {', '.join(f.inputs)}")
    print()
    print("adopt one in a document/collection pha.yaml:")
    print("  editor: {rules: modernise, model: deepseek-v4-flash, pre: [<filter>]}")


def cmd_filter(cfg: Config, args) -> None:
    """Run ONE filter over text (or a file), for authoring and testing.

    Writes the result to stdout; `--json` prints the result envelope (so a
    filter that returns records, or nothing at all, is visible too).
    """
    from .filters import (HOOK_KINDS, FilterError, FilterSpec, apply_filters,
                          build_context, load_filter)

    if args.input:
        try:
            text = Path(args.input).read_text(encoding="utf-8")
        except OSError as e:
            print(f"! cannot read {args.input}: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        text = sys.stdin.read()
    hook = args.hook
    if hook not in HOOK_KINDS:
        print(f"! unknown --hook {hook!r} (expected one of {', '.join(HOOK_KINDS)})",
              file=sys.stderr)
        sys.exit(2)
    params: dict = {}
    for kv in (args.params or []):
        if "=" not in kv:
            print(f"! --params expects key=value, got {kv!r}", file=sys.stderr)
            sys.exit(2)
        k, v = kv.split("=", 1)
        params[k.strip()] = v.strip()
    ctx_extra: dict = {}
    for kv in (args.ctx or []):
        if "=" in kv:
            k, v = kv.split("=", 1)
            ctx_extra[k.strip()] = v.strip()
    try:
        f = load_filter(cfg.filters_dir, args.name)
    except FilterError as e:
        print(f"! {e}", file=sys.stderr)
        sys.exit(2)
    doc = None
    if args.doc is not None:
        conn = db.connect(cfg.db_path)
        try:
            row = db.get_document(conn, int(args.doc))
            doc = dict(row) if row else None
        finally:
            conn.close()
    kind = HOOK_KINDS[hook]
    value = text
    if kind == "records" and text.strip().startswith(("[", "{")):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"! --input is not valid JSON for a records hook: {e}", file=sys.stderr)
            sys.exit(2)
    ctx = build_context(cfg=cfg, document=doc, stage=hook.split(".")[0], hook=hook,
                        kind=kind, params=params, inputs={})
    ctx.update(ctx_extra)
    try:
        out, ran = apply_filters(value, [FilterSpec(name=f.name, params=params)],
                                 hook=hook, ctx=ctx, filters_dir=cfg.filters_dir,
                                 verbose=False)
    except FilterError as e:
        print(f"! {e}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps({"kind": kind, "value": out, "ran": ran},
                         indent=2, ensure_ascii=False))
    elif isinstance(out, str):
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    else:
        print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_reindex(cfg: Config, args) -> None:
    client = _client(cfg, cfg.embed_base_url, cfg.embed_timeout_s)
    try:
        res = reindex_all(cfg, client, path=args.path)
    finally:
        client.close()
    failed = res.get("failed") or []
    if res.get("reason"):
        print(f"! {res['reason']}", file=sys.stderr)
        sys.exit(2)
    print(f"reindexed {res['reindexed']} document(s)")
    if failed:
        # The documents are untouched (chunks and vectors intact) — this is the
        # safe failure, not a degraded index.
        print(f"! {len(failed)} document(s) NOT reindexed — left unchanged; "
              f"re-run when the embed model is available:", file=sys.stderr)
        for f in failed:
            print(f"  # {f['id']} {f['filename']}: {f['error']}", file=sys.stderr)
        sys.exit(3)


def cmd_review(cfg: Config, args) -> None:
    """Import human corrections from the library markdown files into the DB.

    The historian edits library/.../transcription-<pal>/<stem>.md or
    edited-<editor>/<stem>.md; `pha review` reads those files back and updates
    pages.raw_text / page_edits.text, stamping them reviewed.

    Only files actually changed since pha last wrote them are imported — the
    same set `pha status` reports. Use `--all` for the deliberate blanket
    import, and `--unset` to lift the reviewed protection again.

    Correcting a transcription-* page fixes the palaeographer's reading: that
    page is never re-read by `pha scan`, and you should then run `pha edit` so
    the editor re-processes just that page from your corrected text. Correcting
    an edited-* page fixes the final output, which neither `pha scan` nor
    `pha edit` will overwrite. Run `pha reindex` afterwards so search uses the
    corrected text.
    """
    from .ingest import review_import, unreview_import
    if args.page is not None and args.doc is None:
        print("--page requires --doc (refusing to touch every page in the archive)",
              file=sys.stderr)
        sys.exit(2)
    conn = db.connect(cfg.db_path)
    try:
        if getattr(args, "unset", False):
            if getattr(args, "all", False):
                print("note: --all is ignored with --unset (nothing is imported)",
                      file=sys.stderr)
            res = unreview_import(cfg, conn, doc_id=args.doc, page_no=args.page,
                                  verbose=True)
            print(f"unreviewed: {res['pages']} transcription page(s), "
                  f"{res['edits']} edit(s) — they can be re-scanned/re-edited now")
            return
        res = review_import(cfg, conn, doc_id=args.doc, verbose=True,
                            include_all=getattr(args, "all", False))
    finally:
        conn.close()
    scope = " (--all: every library file)" if getattr(args, "all", False) else ""
    extra = f", {res['missing']} file(s) naming no page" if res.get("missing") else ""
    print(f"reviewed: {res['pages']} transcription page(s), {res['edits']} edit(s) "
          f"from {res['scanned']} candidate file(s){scope} "
          f"(skipped {res['skipped']} unparsed{extra})")


def cmd_handoff(cfg: Config, args) -> None:
    """`pha handoff ...` — lend a document to a second machine and take the
    results back. See the hand-off section in README.md."""
    from . import handoff as _ho

    sub = getattr(args, "handoff_cmd", None)

    def _fail(e):
        print(f"! {e}", file=sys.stderr)
        sys.exit(2)

    if sub == "status":
        lines = _ho.status_lines(cfg)
        if getattr(args, "json", False):
            print(json.dumps({
                "handoffs": [
                    {"handoff_id": l.handoff_id, "worker": l.worker, "state": l.state,
                     "created_at": l.created_at, "documents": l.documents}
                    for l in _ho.active_leases(cfg)
                ]
            }, indent=2, ensure_ascii=False))
            return
        if not lines:
            print("no documents are out on hand-over")
            return
        print("out on hand-over")
        for line in lines:
            print(f"  {line}")
        return

    if sub == "cancel":
        try:
            lease = _ho.release(cfg, args.handoff_id, _ho.STATE_CANCELLED)
        except _ho.HandoffError as e:
            _fail(e)
        print(f"released {lease.handoff_id} ({len(lease.documents)} document(s)); "
              f"they are usable on this machine again")

    elif sub == "out":
        out = Path(args.out) if args.out else Path.cwd() / f"{Path(args.targets[0]).name}.pha-handoff"
        try:
            # `inbox/<rel>` targets are relocated into the dropbox first, then
            # the hand-out proceeds exactly as for a dropbox document.
            targets = _handoff_inbox_targets(cfg, args.targets,
                                             force=getattr(args, "force_inbox", False))
            res = _ho.export_handoff(cfg, targets, out, worker=args.worker,
                                     force=args.force, verbose=True)
        except (_ho.HandoffError, FileExistsError) as e:
            _fail(e)
        print(f"handed out {len(res['documents'])} document(s) as {res['handoff_id']}")
        if res["stubs_dropped"]:
            print(f"  {res['stubs_dropped']} not-yet-extracted page(s) left behind "
                  f"(the worker resumes them)")
        print(f"  payload: {res['out']}")
        print(f"  these are now leased: the pipeline skips them until `pha handoff fetch`")

    elif sub in ("in", "back", "fetch"):
        target = Path(args.directory)
        dry = getattr(args, "dry_run", False)
        try:
            if sub == "in":
                res = _ho.import_handoff(cfg, target, verbose=True, dry_run=dry,
                                         keep_defs=getattr(args, "keep_defs", False))
                if dry:
                    print(json.dumps(res, indent=2, ensure_ascii=False))
                    return
                print(f"imported hand-off {res['handoff_id']}")
                for d in res["documents"]:
                    print(f"  #{d['id']} {d['relpath']}: {d['pages_done']}/"
                          f"{d['page_count']} pages done ({d['status']})")
                defs = res.get("installed_defs") or {}
                for kind, names in (defs.get("replaced") or {}).items():
                    if names:
                        print(f"  ~ adopted the hand-out's {kind}: {' '.join(names)}")
                conflicts = {k: v for k, v in (defs.get("conflicts") or {}).items() if v}
                if conflicts:
                    flat = ", ".join(f"{k}/{'/'.join(v)}" for k, v in conflicts.items())
                    print(f"  ! kept this archive's definitions where they differ "
                          f"from the hand-out: {flat}", file=sys.stderr)
                    print("    the worker will process with ITS versions, so the "
                          "returned pages may be reported stale at `pha handoff fetch`",
                          file=sys.stderr)
                print("  next: pha scan --path <the document>   # finishes the pending pages")
            elif sub == "back":
                out = Path(args.out) if args.out else target.parent / f"{target.name}-back"
                res = _ho.build_result(cfg, target, out, verbose=True, dry_run=dry)
                if dry:
                    print(json.dumps(res, indent=2, ensure_ascii=False))
                    return
                print(f"  result directory: {res['out']}")
            else:
                res = _ho.apply_result(cfg, target, verbose=True, dry_run=dry)
                if dry:
                    print(json.dumps(res, indent=2, ensure_ascii=False))
                    return
                c = res["counts"]
                print(
                    f"applied {len(res['documents'])} document(s): "
                    f"{c['took-worker']} page(s)/edit(s) from the worker, "
                    f"{c['kept-local']} skipped (you had corrected them), "
                    f"{c['conflict']} conflict(s), {c['skipped']} dropped"
                )
                if res["conflicts"]:
                    print("  conflicts (both sides corrected differently; local kept):",
                          file=sys.stderr)
                    for c2 in res["conflicts"]:
                        where = c2.get("relpath") or "?"
                        page = c2.get("page")
                        print(f"    {where}" + (f" p.{page}" if page else ""),
                              file=sys.stderr)
                if res["stale"]:
                    print(f"  ! {len(res['stale'])} document(s) were processed under a "
                          f"different config; their pages are stale under the current "
                          f"one: {', '.join(res['stale'])}", file=sys.stderr)
                if res["refused"]:
                    print(f"  ! refused: {', '.join(res['refused'])}", file=sys.stderr)
        except _ho.HandoffError as e:
            _fail(e)

    elif sub == "work":
        target = Path(args.directory)
        try:
            manifest = _ho.read_manifest(target)
        except _ho.HandoffError as e:
            _fail(e)
        paths = [str(d.get("relpath")) for d in manifest.get("documents") or []]
        if not paths:
            print("nothing to work", file=sys.stderr)
            sys.exit(2)
        if getattr(args, "dry_run", False):
            print("would run, for each document:")
            for p in paths:
                print(f"  pha scan --path {p} && pha edit --path {p} && pha encode --path {p}")
            return
        print("running the explicit stage commands (each reports its own outcome):")
        for p in paths:
            for stage in ("scan", "edit", "encode"):
                print(f"=== pha {stage} --path {p}")
                rc = subprocess.call([sys.executable, "-m", "personal_historical_archive",
                                      stage, "--path", p])
                if rc != 0:
                    print(f"! pha {stage} failed for {p} (exit {rc})", file=sys.stderr)


def cmd_bundle(cfg: Config, args) -> None:
    """Export collections/documents into a portable bundle (for another
    archive). Carries the finished scan+edit output — no re-extraction on
    the receiving side. With --move, deletes the bundled documents from THIS
    archive after the bundle is written (a true move)."""
    from .bundle import export_bundle
    if args.out:
        out = Path(args.out)
    else:
        import datetime
        date = datetime.date.today().isoformat()
        out = Path.cwd() / f"{Path(args.targets[0]).name}_{date}.pha-bundle"
    try:
        res = export_bundle(cfg, args.targets, out, force=args.force, move=args.move, verbose=True)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return
    print(f"bundled {res['documents']} document(s) into {res['out']}")
    if res["skipped"]:
        print(f"  skipped (no archive record yet): {', '.join(res['skipped'])}")
    for kind, names in res["defs"].items():
        if names:
            print(f"  defs: {kind}: {' '.join(names)}")
    if res.get("moved"):
        m = res["moved"]
        print(f"moved {m['documents']} document(s) OUT of {cfg.archive_dir} "
              f"({m['dropbox_paths']} dropbox path(s) removed)")
        print(f"  the bundle at {res['out']} is your backup — verify the target "
              f"archive before deleting it")


def cmd_unbundle(cfg: Config, args) -> None:
    """Import a pha bundle into this archive: new DB rows (new ids), pages/
    edits/records and reviewed stamps carried over, then indexed for search.
    Does not re-run the palaeographer or editor."""
    from .bundle import import_bundle
    res = import_bundle(cfg, args.bundle, force=args.force, verbose=True)
    if res["action"] == "skipped":
        print(f"unbundle: {res['reason']}")
        return
    print(f"imported {len(res['imported'])} document(s) into {cfg.archive_dir}")
    if res["imported"]:
        for d in res["imported"]:
            extra = ""
            if d["editors"]:
                extra += f", editors: {' '.join(d['editors'])}"
            if d["encoders"]:
                extra += f", encoders: {' '.join(d['encoders'])}"
            print(f"  + #{d['id']:3d} {d['relpath']}  ({d['status']}, {d['pages']} pages, {d['chunks']} chunks){extra}")
    if res["skipped_documents"]:
        print(f"  skipped (already in this archive): {', '.join(res['skipped_documents'])}")
    if res["files_skipped"]:
        print(f"  {len(res['files_skipped'])} dropbox file(s) already present, not overwritten "
              f"(--force to overwrite)")
    defs = res["defs_installed"]
    for kind, names in defs.get("installed", {}).items():
        if names:
            print(f"  installed defs: {kind}: {' '.join(names)}")
    for kind, names in defs.get("conflicts", {}).items():
        if names:
            print(f"  ! def {kind} differ from the bundle; kept this archive's: "
                  f"{' '.join(names)}")
    print("  search index updated; run `pha reindex` only if embeddings failed above")


def cmd_export(cfg: Config, args) -> None:
    """Regenerate per-page transcription + editor files from the DB (no
    re-extraction / re-editing)."""
    from .ingest import write_edited_pages

    conn = db.connect(cfg.db_path)
    try:
        docs = db.list_documents(conn, limit=10000)
        n = 0
        for d in docs:
            out = write_document_pages(cfg, conn, d["id"])
            if out:
                n += 1
            if d["editor"]:
                write_edited_pages(cfg, conn, d["id"], d["editor"],
                                   model=d["editor_model"] or None)
        print(f"exported {n} document(s) to {cfg.library}")
    finally:
        conn.close()


def cmd_prompts(cfg: Config, args) -> None:
    if args.file:
        p = cfg.dropbox / args.file if not (cfg.root / args.file).exists() else cfg.root / args.file
        if not p.exists():
            print(f"not found: {args.file}")
            return
        text, source = resolve_prompt(p.stem, p.parent, cfg.dropbox, cfg.prompts)
        print(f"prompt source: {source}")
        print("---")
        print(text)
        return
    print(f"default: {cfg.prompts / 'default_prompt.md'}")
    for f in sorted(cfg.prompts.glob("*.prompt.md")):
        print(f"  {f}")
    for f in sorted(cfg.dropbox.rglob("*.prompt.md")):
        print(f"dropbox: {f}")


def cmd_rm(cfg: Config, args) -> None:
    conn = db.connect(cfg.db_path)
    try:
        target = args.target
        if target.isdigit():
            docs = [db.get_document(conn, int(target))] if db.get_document(conn, int(target)) else []
        else:
            docs = [d for d in db.list_documents(conn, limit=1000) if target in d["filename"]]
        if not docs:
            print(f"no document matches {target!r}")
            return
        for d in docs:
            remove_library_artifact(cfg, d)
            db.delete_document(conn, d["id"])
            print(f"removed #{d['id']} {d['filename']}")
        conn.commit()
        # drop the render cache for the removed docs (skipped when another
        # live document still shares the content hash)
        for d in docs:
            remove_render_if_orphaned(cfg, conn, d["sha256"])
    finally:
        conn.close()


def cmd_prune(cfg: Config, args) -> None:
    """Remove orphaned generated artifacts (render image caches whose document
    is gone or was superseded), or with `--library-variants` the bare
    `edited-<rules>` folders that only duplicate their `@<model>` sibling."""
    conn = db.connect(cfg.db_path)
    try:
        if getattr(args, "library_variants", False):
            res = prune_redundant_edited_dirs(cfg, conn, dry_run=args.dry_run, verbose=True,
                                              doc_id=getattr(args, "doc", None))
            verb = "would remove" if args.dry_run else "removed"
            print(f"{verb} {len(res['removed'])} redundant edited folder(s) "
                  f"({res['bytes'] / 1e6:.1f} MB)")
            if res["refused"]:
                print(f"kept {len(res['refused'])} folder(s) that are not provably redundant")
            if res["kept_bare_only"]:
                print(f"kept {res['kept_bare_only']} bare folder(s) with no model-qualified "
                      f"sibling (the document's only edited output)")
            if res["failed"]:
                print(f"FAILED to delete {len(res['failed'])} folder(s) — they are still on "
                      f"disk (the reason is printed above each path)")
                sys.exit(3)
            return
        n = prune_orphan_renders(cfg, conn, dry_run=args.dry_run, verbose=True)
        verb = "would remove" if args.dry_run else "removed"
        print(f"{verb} {n} orphaned render folder(s)")
    finally:
        conn.close()


def _sidecar_summary(path: Path) -> str:
    """One-line summary of a pha.yaml's own palaeographer/editor keys."""
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return "(unreadable)"
    parts = []
    for key in ("palaeographer", "editor"):
        if key not in data:
            continue
        v = data[key]
        if v is None:
            parts.append(f"{key}: none")
        elif isinstance(v, str):
            parts.append(f"{key}: {v}")
        elif isinstance(v, dict):
            r = v.get("rules", "")
            m = v.get("model")
            parts.append(f"{key}: {r}" + (f" (model {m})" if m else ""))
    return ", ".join(parts) or "(empty)"


def cmd_palaeographer(cfg: Config, args) -> None:
    if args.file:
        p = cfg.dropbox / args.file if not (cfg.root / args.file).exists() else cfg.root / args.file
        if not p.exists():
            print(f"not found: {args.file}")
            return
        file_dir = p if p.is_dir() else p.parent
        sc = resolve_sidecar(cfg.dropbox, file_dir, stem=(p.stem if not p.is_dir() else None))
        model_id = None
        if sc.palaeographer:
            pal_id = sc.palaeographer.rules
            source = f"{sc.source} (pha.yaml)"
            model_id = sc.palaeographer.model
        else:
            pal_id, source = resolve_palaeographer_id(p.stem, file_dir, cfg.dropbox)
        pal = cfg.get_palaeographer(pal_id) if pal_id else cfg.get_palaeographer()
        pal = cfg.resolve_model(pal, model_id)
        print(f"palaeographer: {pal.id} ({pal.description or pal.model})")
        print(f"model: {pal.model_ref} ({pal.model})")
        print(f"source: {source or 'config default (vision.palaeographer)'}")
        return
    print(f"default (vision.palaeographer): {cfg.active_palaeographer}")
    print(f"configured palaeographers ({cfg.palaeographers_dir}):")
    for pal_id in sorted(cfg.palaeographers):
        pal = cfg.palaeographers[pal_id]
        print(f"  {pal_id}: {pal.description or pal.model} @ {pal.model}")
    print("selection files in the dropbox:")
    pal_files = []
    for pat in ("palaeographer", "palaeographer.txt", "palaeographer.md",
                "*.palaeographer", "*.palaeographer.txt", "*.palaeographer.md"):
        pal_files.extend(cfg.dropbox.rglob(pat))
    for f in sorted(set(pal_files)):
        pal_id = re.sub(r"^[#\-*\s]+", "", f.read_text(encoding="utf-8").strip().splitlines()[0]).strip() if f.read_text(encoding="utf-8").strip() else ""
        print(f"  {f}: {pal_id or '(empty)'}")
    print("pha.yaml sidecars in the dropbox:")
    for f in sorted(set(cfg.dropbox.rglob("pha.yaml"))):
        print(f"  {f}: {_sidecar_summary(f)}")


def cmd_editor(cfg: Config, args) -> None:
    if args.file:
        p = cfg.dropbox / args.file if not (cfg.root / args.file).exists() else cfg.root / args.file
        if not p.exists():
            print(f"not found: {args.file}")
            return
        file_dir = p if p.is_dir() else p.parent
        sc = resolve_sidecar(cfg.dropbox, file_dir, stem=(p.stem if not p.is_dir() else None))
        model_id = None
        if sc.editor_set:
            ed_id = sc.editor.rules if sc.editor else None
            source = str(sc.source) if sc.source else None
            model_id = sc.editor.model if sc.editor else None
        else:
            ed_id, source = resolve_editor_id(p.stem, file_dir, cfg.dropbox)
        if ed_id and ed_id in cfg.editors:
            ed = cfg.editors[ed_id]
            ed = cfg.resolve_model(ed, model_id)
            print(f"editor: {ed.id} ({ed.description or ed.model})")
            print(f"model: {ed.model_ref} ({ed.model})")
        else:
            print(f"editor: {ed_id or 'none (no editing)'}")
        print(f"source: {source or '(none — no editor configured)'}")
        return
    print(f"configured editors ({cfg.editors_dir}):")
    for ed_id in sorted(cfg.editors):
        ed = cfg.editors[ed_id]
        print(f"  {ed_id}: {ed.description or ed.model} @ {ed.model}")
    print("selection files in the dropbox:")
    ed_files = []
    for pat in ("editor", "editor.txt", "editor.md",
                "*.editor", "*.editor.txt", "*.editor.md"):
        ed_files.extend(cfg.dropbox.rglob(pat))
    for f in sorted(set(ed_files)):
        ed_id = re.sub(r"^[#\-*\s]+", "", f.read_text(encoding="utf-8").strip().splitlines()[0]).strip() if f.read_text(encoding="utf-8").strip() else ""
        print(f"  {f}: {ed_id or '(empty)'}")
    print("pha.yaml sidecars in the dropbox:")
    for f in sorted(set(cfg.dropbox.rglob("pha.yaml"))):
        print(f"  {f}: {_sidecar_summary(f)}")


def cmd_edit(cfg: Config, args) -> None:
    from .ingest import edit_documents_under

    page_no = getattr(args, "page", None)
    if getattr(args, "path", None):
        res = edit_documents_under(cfg, args.path, reprocess=args.reprocess,
                                   verbose=True, page_no=page_no)
    else:
        res = edit_all(cfg, reprocess=args.reprocess, verbose=True, page_no=page_no)
    edited = sum(1 for r in res["results"] if r["action"] == "edited")
    print(f"edited {edited} document(s)")
    for r in res["results"]:
        if r["action"] == "edited":
            print(f"  + {r['filename']} [{r['editor']}] ({r['pages']} pages)")
        elif r["reason"] != "no editor configured":
            print(f"  ! {r['filename']}: {r.get('reason', r['action'])}")
    # The edit pass indexes the documents it touched (and repairs a document
    # whose index was never written); report that, rather than silently
    # leaving search on the pre-edit text. A failure is reported, not fatal.
    indexed = res.get("indexed", 0)
    if indexed:
        print(f"re-indexed {indexed} document(s) so search covers the edited text")
    for msg in res.get("index_failed", []):
        print(f"  ! not re-indexed: {msg} — run: pha reindex", file=sys.stderr)


def cmd_key(cfg: Config, args) -> None:
    """Manage secrets referenced as ${NAME} in palaeographer/editor files.

    `pha key --set NAME` reads the value from stdin and stores it in the
    platform secret store (Keychain / secret-tool / DPAPI), falling back to the gitignored .env file.
    `pha key` shows which referenced variables are resolvable.
    """
    from .config import _secret_get, _secret_set

    if args.set:
        name = args.set
        value = sys.stdin.readline().strip()
        if not value:
            print(f"no value provided for {name}")
            return
        if _secret_set(name, value):
            print(f"stored {name} in the OS secret store (service pha)")
        else:
            envp = cfg.root / ".env"
            lines = [l for l in envp.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.startswith(f"{name}=")] if envp.exists() else []
            lines.append(f"{name}={value}")
            envp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"OS secret store unavailable; stored {name} in {envp} (gitignored)")
        return
    names = set()
    # models/ matters as much as the content dirs: a model file is where the
    # api_key actually lives (palaeographer/editor files are content-only and
    # carry no key), so scanning only those two missed every wired-up key.
    for d in (cfg.models_dir, cfg.palaeographers_dir, cfg.editors_dir):
        for f in d.glob("*.md"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("api_key:"):
                    m = re.search(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line)
                    if m:
                        names.add(m.group(1))
    if not names:
        print("no ${...} api_key references found in models/, palaeographers/, editors/")
        return
    for name in sorted(names):
        src = "environment" if os.environ.get(name) else ("OS secret store" if _secret_get(name) else "unset")
        print(f"  {name}: {src}")


def cmd_upload(cfg: Config, args) -> None:
    """`pha upload document|collection <PATH>` — copy a document/collection
    into the dropbox at the conventional location."""
    from .upload import upload as do_upload
    kind = getattr(args, "kind", None)
    src = args.path
    try:
        report = do_upload(
            cfg, src, kind,
            name=getattr(args, "name", None),
            replace=getattr(args, "replace", False),
            merge=getattr(args, "merge", False),
        )
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return
    print(f"uploaded {report['kind']}: {report['source']}")
    print(f"  -> {report['destination']}  ({report['files_copied']} file(s))")


def _set_env_in_dotenv(cfg: Config, env_name: str, display: str, current: str,
                       path: str | None = None) -> None:
    """Prompt for (or accept) a path and store it as env_name in the
    gitignored project .env. Used by the DEPRECATED `pha set dropbox` only —
    the archive location goes in `config.yaml` (see
    `_set_archive_dir_in_config`)."""
    if not path:
        try:
            if not sys.stdin.isatty():
                path = sys.stdin.readline().strip()
        except Exception:
            path = None
    if not path:
        print(f"{display}:")
        print(f"  current: {current}")
        try:
            path = input("Path (Enter to keep current): ").strip()
        except (EOFError, KeyboardInterrupt):
            path = ""
    if path:
        expanded = os.path.expanduser(path).strip()
        if not os.path.isabs(expanded):
            expanded = str((cfg.root / expanded).resolve())
        envp = cfg.root / ".env"
        lines = [l for l in envp.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith(f"{env_name}=")] if envp.exists() else []
        lines.append(f"{env_name}={expanded}")
        envp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"stored {env_name} -> {expanded}  (in {envp}, gitignored)")
    else:
        print(f"{env_name} unchanged: {current}")


def _write_paths_archive_dir(text: str, value: str) -> str:
    """Set `paths.archive_dir` in config.yaml, preserving everything else.

    A targeted line edit rather than a YAML round-trip: config.yaml carries
    explanatory comments that `yaml.safe_dump` would strip. Only a
    `archive_dir:` key inside the `paths:` block is touched.
    """
    lines = text.splitlines()
    paths_at = None
    for i, line in enumerate(lines):
        if re.match(r"^paths:\s*(#.*)?$", line):
            paths_at = i
            break
    if paths_at is None:
        block = ["paths:", f"  archive_dir: {value}"]
        if lines and lines[-1].strip():
            lines.append("")
        return "\n".join(lines + block) + "\n"
    # inside the block, up to the next top-level key
    end = len(lines)
    for j in range(paths_at + 1, len(lines)):
        if lines[j].strip() and not lines[j][:1].isspace():
            end = j
            break
    for j in range(paths_at + 1, end):
        if re.match(r"^\s+archive_dir:", lines[j]):
            indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
            lines[j] = f"{indent}archive_dir: {value}"
            return "\n".join(lines) + "\n"
    lines.insert(paths_at + 1, f"  archive_dir: {value}")
    return "\n".join(lines) + "\n"


def _clear_dotenv_archive_dir(cfg: Config) -> bool:
    """Drop a legacy PHA_ARCHIVE_DIR line from .env. True if one was removed."""
    envp = cfg.root / ".env"
    if not envp.exists():
        return False
    try:
        lines = envp.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    kept = [l for l in lines if not l.strip().startswith("PHA_ARCHIVE_DIR=")]
    if len(kept) == len(lines):
        return False
    try:
        envp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    except OSError:
        return False
    return True


def _set_archive_dir_in_config(cfg: Config, path: str | None) -> None:
    """Prompt for (or accept) an archive root and store it in config.yaml.

    `config.yaml` is the tracked, reviewable home for the archive location
    (DEC: one visible pointer instead of a gitignored `.env` line that can go
    stale unnoticed). `PHA_ARCHIVE_DIR` in the real environment still wins, so
    a one-off or per-machine override needs no file edit.
    """
    current = str(getattr(cfg, "archive_dir", "") or "")
    if not path:
        try:
            if not sys.stdin.isatty():
                path = sys.stdin.readline().strip()
        except Exception:
            path = None
    if not path:
        print("Archive directory:")
        print(f"  current: {current}")
        try:
            path = input("Path (Enter to keep current): ").strip()
        except (EOFError, KeyboardInterrupt):
            path = ""
    if not path:
        print(f"archive_dir unchanged: {current}")
        return
    expanded = os.path.expanduser(path).strip()
    if not os.path.isabs(expanded):
        expanded = str((cfg.root / expanded).resolve())
    cfgp = cfg.root / "config.yaml"
    try:
        text = cfgp.read_text(encoding="utf-8") if cfgp.exists() else ""
    except OSError as e:
        print(f"error: cannot read {cfgp}: {e}", file=sys.stderr)
        return
    try:
        cfgp.write_text(_write_paths_archive_dir(text, expanded), encoding="utf-8")
    except OSError as e:
        print(f"error: cannot write {cfgp}: {e}", file=sys.stderr)
        return
    print(f"stored paths.archive_dir -> {expanded}  (in {cfgp})")
    if _clear_dotenv_archive_dir(cfg):
        print("removed the legacy PHA_ARCHIVE_DIR line from .env "
              "(config.yaml is now the source of truth)")


def cmd_set_archive_dir(cfg: Config, args) -> None:
    """`pha set archive-dir` (or `pha archive-dir`) — set the archive data root.

    Stores `paths.archive_dir` in config.yaml, so the archive location is a
    tracked, reviewable line rather than a gitignored `.env` value.
    `PHA_ARCHIVE_DIR` in the environment still overrides it for a one-off run.
    All data — documents (dropbox), model definitions
    (palaeographers/editors/encoders) and generated output (library, renders,
    db) — lives under this directory."""
    path = getattr(args, "path", None)
    _set_archive_dir_in_config(cfg, path)


def cmd_init_archive(cfg: Config, args) -> None:
    """`pha init-archive <PATH>` — create a new self-contained pha archive.

    Creates the default structure (dropbox/documents, dropbox/collections,
    library, renders, notes, palaeographers/editors/encoders with zero-config
    defaults, skills/ with the pha-specific agent skills) plus a README.md,
    AGENTS.md and a .gitignore. If PATH does not exist it is created; if it
    exists it must be empty (never touches an existing archive)."""
    from .archive_init import init_archive
    try:
        p = init_archive(args.path, project_root=cfg.root)
    except (FileExistsError, NotADirectoryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return
    print(f"created archive at {p}")
    print("  dropbox/documents/  dropbox/collections/   (drop your sources here)")
    print("  library/  renders/  notes/  palaeographers/  editors/  encoders/")
    print("  skills/                                  (pha agent skills)")
    print("  README.md + AGENTS.md + .gitignore written")
    print("point pha at it with:  pha set archive-dir " + str(p))


def cmd_set_dropbox(cfg: Config, args) -> None:
    """DEPRECATED alias for setting just the dropbox (documents) folder.

    Use `pha set archive-dir` instead; this only relocates the documents
    folder, not the rest of the archive."""
    path = getattr(args, "path", None) or (getattr(args, "dropbox", None) or None)
    _set_env_in_dotenv(cfg, "PHA_DROPBOX", "Dropbox (documents) folder",
                       str(getattr(cfg, "dropbox", "")), path)


def cmd_encoder(cfg: Config, args) -> None:
    if getattr(args, "new", False):
        cmd_encoder_new(cfg, args)
        return
    if args.file:
        p = cfg.dropbox / args.file if not (cfg.root / args.file).exists() else cfg.root / args.file
        if not p.exists():
            print(f"not found: {args.file}")
            return
        enc_files = encoder_files_for(p.stem, p if p.is_dir() else p.parent, cfg.dropbox)
        if enc_files:
            for f in enc_files:
                e = cfg.encoder_from_file(f)
                pages = f" pages={e.pages}" if e and e.pages else ""
                print(f"encoder: {f.stem} ({e.description or e.model if e else '?'}){pages}")
                print(f"  source: {f}")
        else:
            print("encoder: none (no encoding)")
        return
    # list all collection-local encoders
    found = sorted(cfg.dropbox.rglob("encoders/*.md"))
    found = [f for f in found if not f.name.startswith("_")
             and not re.search(r"\.(prompt|langextract)\.md$", f.name)]
    if not found:
        print("no encoders configured (drop encoders/*.md files next to your documents)")
        return
    print("encoders (next to their sources):")
    for f in found:
        e = cfg.encoder_from_file(f)
        pages = f" pages={e.pages}" if e and e.pages else ""
        print(f"  {f}: {e.description if e else '?'}{pages}")


def cmd_encode(cfg: Config, args) -> None:
    res = encode_all(cfg, reprocess=args.reprocess, verbose=True)
    encoded = sum(1 for r in res["results"] if r["action"] == "encoded")
    print(f"encoded {encoded} document(s)")
    for r in res["results"]:
        if r["action"] == "encoded":
            print(f"  + {r['filename']} [{r['encoder']}] ({r['records']} records)")
        elif r["reason"] not in ("no encoder configured", "records up to date"):
            print(f"  ! {r['filename']}: {r.get('reason', r['action'])}")


def cmd_test(cfg: Config, args) -> None:
    """`pha test [target]` — run transcription/editing/encoding on a sample.

    Tests the resolved pha.yaml configuration on a handful of pages, writing
    the output (and a report.md) to a scratch dir WITHOUT touching the real
    archive. `--show` re-prints the most recent saved report.
    """
    from .testrun import run_test, show_latest, list_runs, clean_runs

    if getattr(args, "show", False):
        sys.exit(show_latest(cfg, getattr(args, "target", None)))

    if getattr(args, "list", False):
        runs = list_runs(cfg)
        if not runs:
            print("no pha test reports found")
            return
        for r in runs:
            ts = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {r['name']}  {r['target']!r}  "
                  f"({r['documents']} doc(s), pages={r['pages']})  {ts}")
        print(f"\n{len(runs)} test run(s). `pha test --clean` removes them.")
        return

    if getattr(args, "clean", False):
        res = clean_runs(cfg, getattr(args, "target", None),
                         dry_run=bool(getattr(args, "dry_run", False)))
        verb = "would remove" if getattr(args, "dry_run", False) else "removed"
        for c in res["cleaned"]:
            print(f"  {verb} {c}")
        if not res["cleaned"]:
            msg = "nothing to clean"
            if getattr(args, "target", None):
                msg += f" matching {args.target!r}"
            print(msg)
        else:
            print(f"{verb} {res['removed']} test run(s)")
        return

    if not getattr(args, "target", None):
        print("error: specify a document or collection to test, e.g.\n"
              "  pha test collections/COLX --pages 3\n"
              "  pha test documents/ms123 --pages 2 --random\n"
              "  pha test --show          # re-print the most recent report\n"
              "  pha test --list          # list saved test reports\n"
              "  pha test --clean         # delete them",
              file=sys.stderr)
        sys.exit(1)

    try:
        res = run_test(
            cfg,
            getattr(args, "target", None),
            pages=getattr(args, "pages", 3),
            randomize=bool(getattr(args, "random", False)),
            seed=getattr(args, "seed", None),
            palaeographer=getattr(args, "palaeographer", None),
            editor=getattr(args, "editor", None),
            encoder=getattr(args, "encoder", None),
            model=getattr(args, "model", None),
            prompt=getattr(args, "prompt", None),
            temperature=getattr(args, "temperature", None),
            max_tokens=getattr(args, "max_tokens", None),
            verbose=True,
        )
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    if res.get("skipped"):
        print(f"test skipped: {res['reason']}", file=sys.stderr)
        sys.exit(1)

    report = Path(res["scratch"]) / "report.md"
    if report.exists():
        print(report.read_text(encoding="utf-8"))
    else:
        print(f"results in {res['scratch']}")


def cmd_encoder_new(cfg: Config, args) -> None:
    from .encoder_helper import run
    raise SystemExit(run(cfg))


def cmd_migrate_config(cfg: Config, args) -> None:
    """`pha migrate-config` — one-shot migration to the models/ registry +
    pha.yaml sidecar layout. Idempotent: already-migrated files are skipped."""
    from .migrate import migrate_config, print_report

    report = migrate_config(cfg, dry_run=args.dry_run, remove_selection_files=args.remove)
    if args.dry_run:
        print("dry-run — no files changed")
    print_report(report)


def cmd_mcp(cfg: Config, args) -> None:
    from . import mcp_server

    mcp_server.main(args.transport, args.host, args.port)


def cmd_serve(cfg: Config, args) -> None:
    """`pha serve` — a read-only HTTP endpoint with stable page-render URLs.

    Loopback by default; `--host 0.0.0.0` is a deliberate opt-in (it exposes the
    archive to the network) and logs a warning. Nothing here writes: mutations
    stay behind the scan/edit lock.
    """
    from .serve import run_server

    run_server(cfg, host=args.host or cfg.serve_host, port=args.port or cfg.serve_port,
               quiet=args.quiet)


def cmd_doctor(cfg: Config, args) -> None:
    """`pha doctor` — are the local OCR/parse engines installed and usable?

    Works even when no archive is configured: the binary checks are
    machine-level. A model file that declares `engine: tesseract` /
    `engine: liteparse` makes that engine REQUIRED; `--engine <name>` also
    requires one (handy before configuring a collection, to ask "is lit even
    installed?"). Exits 1 when a required engine is missing/broken; `--json`
    prints the machine-readable report.
    """
    from . import doctor
    from . import locks

    declared: dict[str, list[str]] = {}
    servers: dict[str, dict] = {}
    for m_id, m in sorted((cfg.models or {}).items()):
        eng = (m.engine or "").strip().lower()
        if eng in doctor.ENGINES:
            declared.setdefault(eng, []).append(m_id)
        key = locks.stage_key(m)
        if key:
            entry = servers.setdefault(key, {"key": key, "models": []})
            entry["models"].append(m_id)
    ek = locks.embed_key(cfg)
    servers.setdefault(ek, {"key": ek, "models": []})["models"].append("embeddings")
    for entry in servers.values():
        entry["slots"] = cfg.server_slots(entry["key"])
    unconfigured = _archive_unconfigured(cfg)
    require = set(getattr(args, "engine", None) or [])
    report = doctor.diagnose(
        declared=declared,
        require=require,
        archive=None if unconfigured else str(cfg.archive_dir),
        servers=[] if unconfigured else [servers[k] for k in sorted(servers)],
        lock_dir=None if unconfigured else str(locks.lock_dir()),
    )
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(doctor.render(report))
    if not report["ok"]:
        sys.exit(1)


def cmd_update(cfg: Config, args) -> None:
    """`pha update` — check GitHub for a newer pha and install it.

    `pha update --check` only compares versions and reports; `pha update` (or
    `--yes`) applies the update. Editable-from-git installs are fast-forwarded
    in place; wheel installs are reinstalled from the repository.
    """
    from .update import UpdateError, check, current_version, install_update

    try:
        info = check(cfg.root, cfg.update_repo, cfg.update_branch, timeout=cfg.update_timeout)
    except Exception as e:  # noqa: BLE001 - report a network/parse failure cleanly
        print(f"could not check for updates: {e}", file=sys.stderr)
        print(f"  current version: {current_version()}", file=sys.stderr)
        sys.exit(2)

    print(f"current version: {info['current']}")
    print(f"latest version : {info['latest']}  ({info['remote_source']})")
    if not info["update_available"]:
        print("pha is up to date.")
        return
    print(f"a newer version of pha is available ({info['current']} -> {info['latest']}).")
    if args.check:
        print("not installing (--check only).")
        return
    if not args.yes:
        try:
            ans = input("install now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans not in ("y", "yes"):
            print("not installing.")
            return
    try:
        msg = install_update(cfg.update_repo, cfg.update_branch)
    except UpdateError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        print("error: update command failed", file=sys.stderr)
        print(e.stderr or e.stdout or e, file=sys.stderr)
        sys.exit(2)
    print(msg)
    print("restart pha to use the new version.")
    print("On the next pha command the agent docs (AGENTS.md / README.md) in your "
          "archive are refreshed if they are outdated.")


def cmd_help(cfg: Config, args) -> None:
    """`pha help [topic]` — orientation and pointers to the instruction files.

    Works for both humans and agents; runs even when no archive is configured.
    """
    root = cfg.root
    docs = {
        "readme": ("README.md", "main manual: pipeline, commands, configuration, quickstart"),
        "mcp": ("MCP_CLIENTS.md", "connecting an AI agent to the archive (MCP `pha_*` tools)"),
        "historians": ("HISTORIANS_README.md", "step-by-step, non-technical guide for historians"),
        "agents": ("AGENTS.md", "conventions for AI agents operating this archive"),
    }
    topic = getattr(args, "topic", None)
    if topic:
        topic = topic.strip().lower().replace("-", "")
        match = next((k for k in docs if k.replace("-", "") == topic), None)
        if not match:
            print(f"unknown help topic: {args.topic}", file=sys.stderr)
            print(f"known topics: {', '.join(sorted(docs))}", file=sys.stderr)
            return
        name, what = docs[match]
        path = root / name
        print(f"pha — {name} ({what})")
        print(f"  path: {path}")
        print("  open this file for the full instructions.")
        return

    print("pha — Personal Historical Archive (local archive of historical documents)")
    print()
    print("USAGE")
    print("  pha <command> [options]       # `pha --help` lists every command")
    print()
    print("COMMON COMMANDS")
    print("  pha status                    per-collection tree of what is ingested, new, pending")
    print("  pha inbox [--move|--dry-run]  list / move documents parked on hold in the inbox")
    print("  pha scan                      extract + index new/changed files in dropbox")
    print('  pha search "query"            search the extracted text')
    print("  pha set archive-dir <path>    point pha at an archive")
    print("  pha init-archive <path>       create a new archive")
    print("  pha mcp                       run the MCP server (stdio)")
    print("  pha bundle <collections...>   export collections for another archive (no re-scan there)")
    print("  pha unbundle <bundle>         import a bundle into THIS archive (no re-scan/edit)")
    print("  pha handoff out|in|back|fetch lend a document to another machine, take the work back")
    print("  pha handoff status            what is out on hand-over; `cancel <id>` releases it")
    print("  pha test [target] [--pages N] [--random]  test a config on a sample; --show/--list/--clean manage reports")
    print("  pha update                    check GitHub for a newer pha and install it")
    print("  pha help <topic>              details on readme|mcp|historians|agents")
    print()
    print("FIRST-TIME SETUP")
    print("  If no archive is configured, pha asks where it is: point at an")
    print("  existing archive or create a new one under ~/pha-home")
    print("  (Windows: %USERPROFILE%\\pha-home).")
    print()
    print("DOCUMENTATION — read these for full instructions")
    for key in ("readme", "mcp", "historians", "agents"):
        name, what = docs[key]
        print(f"  {name:<22} {what}")
    print()
    print("  The files above live in the pha project directory.")
    print("  For agents: an archive created with `pha init-archive` also has its")
    print("  own README.md + AGENTS.md inside it describing that archive.")


# --------------------------------------------------------------------------- fresh-install handling
#
# When `pha` is freshly installed (e.g. a global `uv tool install`) no archive
# is configured yet. Without a guard the CLI silently operates on the empty
# default archive (the project root's ./archive.db) and agents see "documents:
# none" and then guess. Instead, detect that state and ask the user/agent to
# point at an existing archive or create a new one under $HOME/pha-home.

def _archive_explicitly_set(cfg: Config) -> bool:
    """True if PHA_ARCHIVE_DIR was set explicitly (env / .env / config.yaml),
    as opposed to falling back to the default project-root archive."""
    if os.environ.get("PHA_ARCHIVE_DIR"):
        return True
    envp = cfg.root / ".env"
    if envp.exists() and any(
        l.strip().startswith("PHA_ARCHIVE_DIR=")
        for l in envp.read_text(encoding="utf-8").splitlines()
    ):
        return True
    cfg_path = cfg.root / "config.yaml"
    if cfg_path.exists():
        import yaml
        try:
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return False
        val = (raw.get("paths", {}) or {}).get("archive_dir")
        # "." is the backward-compatible DEFAULT (archive == project root),
        # so it does not count as an explicit real archive.
        if val and str(val).strip() not in ("", "."):
            return True
    return False


def _archive_unconfigured(cfg: Config) -> bool:
    """A real archive is absent: no explicit archive_dir and the default
    archive holds no documents (DB missing or empty)."""
    if _archive_explicitly_set(cfg):
        return False
    dbp = cfg.db_path
    if not dbp.exists():
        return True
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
        n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn.close()
        return n == 0
    except Exception:
        return True


def _prospective_archive(cfg: Config) -> Path | None:
    """Another pha archive sitting where the user probably expected this one.

    Heuristic, and deliberately conservative: look only in the CWD's parents
    for an `archive.db`, and ignore the directory we already resolved to. A
    single `stat` per level, and the walk stops at `$HOME`, so this is cheap
    enough to run on every command.
    """
    try:
        cur = Path.cwd().resolve()
    except OSError:
        return None
    home = Path(os.path.expanduser("~"))
    resolved = cfg.archive_dir.resolve()
    for d in [cur, *cur.parents]:
        if d == resolved:
            return None  # we are already looking at it
        if (d / "archive.db").exists():
            return d
        if d == home:
            break
    return None


def _archive_is_empty(cfg: Config) -> bool:
    """Does the resolved archive hold no documents (or have no DB yet)?"""
    dbp = cfg.db_path
    if not dbp.exists():
        return True
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - an unreadable DB is not our problem here
        return False


def _resolve_archive_dir_line(cfg: Config) -> str:
    """One line naming the archive in use, and how it was chosen."""
    if os.environ.get("PHA_ARCHIVE_DIR"):
        how = "from PHA_ARCHIVE_DIR in the environment"
    elif _dotenv_archive_dir(cfg) is not None:
        how = "from the legacy PHA_ARCHIVE_DIR line in .env"
    else:
        cfg_yaml = (cfg.root / "config.yaml")
        val = None
        if cfg_yaml.exists():
            try:
                import yaml
                raw = yaml.safe_load(cfg_yaml.read_text(encoding="utf-8")) or {}
                val = (raw.get("paths", {}) or {}).get("archive_dir")
            except Exception:  # noqa: BLE001
                val = None
        if val and str(val).strip() not in ("", "."):
            how = "from paths.archive_dir in config.yaml"
        else:
            how = ("the default (no archive configured) — "
                   "`pha set archive-dir <path>` to choose one")
    return f"archive: {cfg.archive_dir}   ({how})"


def _dotenv_archive_dir(cfg: Config) -> str | None:
    """The legacy PHA_ARCHIVE_DIR value in .env, as an absolute string."""
    envp = cfg.root / ".env"
    if not envp.exists():
        return None
    try:
        for line in envp.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("PHA_ARCHIVE_DIR="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                return str((cfg.root / raw).resolve()) if not os.path.isabs(raw) else raw
    except OSError:
        return None
    return None


def _warn_resolved_archive(cfg: Config) -> None:
    """Say which archive we resolved, when that is worth saying.

    Two cases, both silent until now:

    - no archive is configured (fallback to the project root / CWD): the user
      is told where it landed, because an empty archive is a VALID archive and
      a wrong one therefore fails silently;
    - an archive IS configured, but it holds no documents while another
      `archive.db` sits in a parent directory — the shape of a stale `.env`
      pointing at the wrong place.

    Printed to stderr (never stdout: `--json` output and piping must stay
    clean) and only for commands that actually work on the archive.
    """
    if not _archive_explicitly_set(cfg):
        print(_resolve_archive_dir_line(cfg), file=sys.stderr)
        return
    if not _archive_is_empty(cfg):
        return
    other = _prospective_archive(cfg)
    if other is not None:
        print(
            f"warning: the configured archive {cfg.archive_dir} has no documents, "
            f"but {other} has an archive.db — is this the archive you meant? "
            f"Check with `pha info`.",
            file=sys.stderr,
        )


def _create_and_set(cfg: Config, path: Path) -> None:
    """Create a new archive at `path` and point pha at it (paths.archive_dir)."""
    from .archive_init import init_archive
    try:
        p = init_archive(str(path), project_root=cfg.root)
    except (FileExistsError, NotADirectoryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return
    _set_archive_dir_in_config(cfg, str(p))
    print(f"created and pointed pha at {p}")


def _prompt_archive_setup(cfg: Config) -> bool:
    """Handle a fresh install. Returns True if the archive was configured and
    the caller must reload Config; returns False if the user declined / we are
    non-interactive and should stop."""
    from pathlib import Path
    home_pha = Path(os.path.expanduser("~")) / "pha-home"
    print("No pha archive is configured or found.", file=sys.stderr)
    print("pha keeps everything (documents, model definitions, generated", file=sys.stderr)
    print("output) in one 'archive directory'. You can:", file=sys.stderr)
    print(f"  1. point pha at an EXISTING archive:  pha set archive-dir <path>", file=sys.stderr)
    print(f"  2. create a NEW archive here:        {home_pha}", file=sys.stderr)
    if sys.stdin.isatty():
        try:
            ans = input("\n[1] existing, [2] create new (default), [q] quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans == "1":
            _set_archive_dir_in_config(cfg, None)
            return True
        if ans in ("", "2"):
            _create_and_set(cfg, home_pha)
            return True
        return False  # quit
    # non-interactive (an agent / cron): don't block on stdin; instruct + stop.
    print(file=sys.stderr)
    print("Set one of these, then re-run your command:", file=sys.stderr)
    print(f"  pha set archive-dir <path>                                       # existing archive", file=sys.stderr)
    print(f"  pha init-archive ~/pha-home && pha set archive-dir ~/pha-home     # new archive", file=sys.stderr)
    return False


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> None:
    cfg = Config.load()

    parser = argparse.ArgumentParser(
        prog="pha",
        description="Personal Historical Archive (pha): drop folder -> VLM extraction -> index -> MCP search.",
    )
    sub = parser.add_subparsers(dest="cmd")
    # `cmd` is optional so a bare `pha` prints help instead of a terse error.

    s = sub.add_parser("scan", help="extract + index new/changed files in the dropbox")
    s.add_argument("--watch", action="store_true", help="keep watching the dropbox")
    s.add_argument("--debounce", type=int, default=8, help="watch debounce seconds")
    s.add_argument("--prompt", default=None, help="prompt file used for all files")
    s.add_argument("--palaeographer", default=None, help="palaeographer id from config (default: vision.palaeographer)")
    s.add_argument("--path", "--collection", default=None,
                   help="only process this subpath under the dropbox (e.g. "
                        "collections/pfister-notices) instead of the whole dropbox")
    s.add_argument("--reprocess", action="store_true", help="re-extract everything")
    s.add_argument("--include-leased", action="store_true",
                        help="also process documents currently out on a hand-over")
    s.set_defaults(fn=cmd_scan)

    q = sub.add_parser("search", help="search the extracted text")
    q.add_argument("query")
    q.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default=None)
    q.add_argument("--limit", type=int, default=None)
    q.add_argument("--collection", default=None,
                   help="restrict to a collection/dir, e.g. 'documents', 'COLX' or 'collections/COLX'")
    q.add_argument("--force", "--allow-embed", dest="force", action="store_true",
                   help="embed the query even while a scan/edit/reindex is using the "
                        "embedding server (loads the embed model there — it may evict "
                        "the running job's model)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_search)

    st = sub.add_parser("status", help="archive summary")
    st.add_argument("--json", action="store_true",
                    help="structured output for agents and the view")
    st.set_defaults(fn=cmd_status)

    inf = sub.add_parser(
        "info",
        help="print the archive's resolved paths (fast, read-only: no DB, no engine probing)")
    inf.add_argument("--json", action="store_true", help="structured output")
    inf.set_defaults(fn=cmd_info)

    ib = sub.add_parser("inbox", help="list documents on hold, or move them into the dropbox")
    ib.add_argument("path", nargs="?", default=None,
                    help="a file or folder inside the inbox (relative); default: the whole inbox")
    ib.add_argument("--move", action="store_true",
                    help="move held documents into the dropbox (then `pha scan`)")
    ib.add_argument("--dry-run", action="store_true",
                    help="show what --move would do without moving anything")
    ib.add_argument("--json", action="store_true", help="structured output for agents and the view")
    ib.set_defaults(fn=cmd_inbox)

    pg = sub.add_parser("page", help="print the full transcription of one page")
    pg.add_argument("doc", help="document id (number) or a filename substring")
    pg.add_argument("page", type=int, help="page number")
    pg.add_argument("--edited", action="store_true",
                    help="show the edited variant (translated/modernized) instead of the raw reading")
    pg.add_argument("--editor", default=None, help="editor id when --edited (default: the document's editor)")
    pg.add_argument("--json", action="store_true", help="structured output for agents")
    pg.set_defaults(fn=cmd_page)

    ct = sub.add_parser(
        "cite",
        help="print a durable citation for one page (stable slug + the exact FILLED variant)")
    ct.add_argument("doc", help="document id (number) or a filename substring")
    ct.add_argument("page", type=int, help="page number")
    ct.add_argument("--edited", action="store_true",
                    help="cite the edited variant (default: the raw transcription)")
    ct.add_argument("--editor", default=None,
                    help="editor id to disambiguate when several edited variants are filled")
    ct.add_argument("--palaeographer", default=None,
                    help="palaeographer id to disambiguate when several transcriptions are filled")
    ct.add_argument("--json", action="store_true", help="structured output for agents/note generators")
    ct.set_defaults(fn=cmd_cite)

    bb = sub.add_parser(
        "bib",
        help="bibliographic references: coverage, one document's reference, or problems")
    bb.add_argument("doc", nargs="?", default=None,
                    help="document id or filename substring (omit for a coverage report)")
    bb.add_argument("--check", action="store_true",
                    help="report only sidecars that are broken, empty or duplicated")
    bb.add_argument("--to-json", action="store_true",
                    help="print the reference as an editable JSON sidecar (a Zotero MODS import "
                         "does not have to be hand-edited as XML)")
    bb.add_argument("--to-bibtex", action="store_true",
                    help="print the reference as a BibTeX entry — the format an agent can draft "
                         "from a scan with no Zotero, and a human can still edit")
    bb.add_argument("--write", action="store_true",
                    help="with --to-json/--to-bibtex: write the sidecar beside the document")
    bb.add_argument("--keep-others", action="store_true",
                    help="with --write: keep sidecars in other formats (JSON would win the lookup)")
    bb.add_argument("--origin", default=None,
                    help="with --to-bibtex: the provenance to record, e.g. "
                         "agent-drafted-unverified for a reference drafted from a scan")
    bb.add_argument("--qualified", action="store_true",
                    help="with --to-json: emit Dublin Core JSON-LD (dcterms:/pha: keys) "
                         "instead of plain readable keys, for handing to another tool")
    bb.add_argument("--json", action="store_true", help="structured output for agents")
    bb.set_defaults(fn=cmd_bib)

    op = sub.add_parser(
        "open",
        help="open an archive page or markdown file in the OS-default app (e.g. your markdown editor)")
    op.add_argument("doc", help="document id or filename substring, or a path to an archive .md file")
    op.add_argument("page", type=int, nargs="?", default=None,
                    help="page number — omit to treat <doc> as a file path instead")
    op.add_argument("--edited", action="store_true",
                    help="open the edited variant instead of the raw reading")
    op.add_argument("--editor", default=None,
                    help="editor id when --edited (default: the document's editor)")
    op.set_defaults(fn=cmd_open)

    pd = sub.add_parser(
        "pending",
        help="list library page files edited but not yet imported into the DB (DB out of sync with the library)")
    pd.add_argument("--doc", type=int, default=None, help="limit to one document id")
    pd.add_argument("--json", action="store_true", help="structured output for agents")
    pd.set_defaults(fn=cmd_pending)

    cf = sub.add_parser(
        "config",
        help="show how a document/collection is processed (pha.yaml), generating it from the legacy selection files when needed")
    cf.add_argument("--doc", default=None, help="document id (number) or a filename substring")
    cf.add_argument("--path", default=None, help="dropbox-relative path to a document or collection dir")
    cf.add_argument("--write", action="store_true",
                    help="generate pha.yaml when it is missing or legacy selection files are still present")
    cf.add_argument("--json", action="store_true", help="structured output for the PHA view / agents")
    cf.set_defaults(fn=cmd_config)

    m = sub.add_parser("mcp", help="run the MCP server (stdio or sse)")
    m.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8000)
    m.set_defaults(fn=cmd_mcp)

    sv = sub.add_parser(
        "serve",
        help="run the read-only render server (stable /doc/<slug>/p<NNN>.jpg URLs)")
    sv.add_argument("--host", default=None,
                    help="bind address (default: serve.host from config, else 127.0.0.1; "
                         "0.0.0.0 exposes the archive to the LAN)")
    sv.add_argument("--port", type=int, default=None,
                    help="port (default: serve.port from config, else 8765)")
    sv.add_argument("--quiet", action="store_true", help="suppress per-request logging")
    sv.set_defaults(fn=cmd_serve)

    up = sub.add_parser("update", help="check GitHub for a newer pha and install it")
    up.add_argument("--check", action="store_true",
                    help="only compare versions and report; do not install")
    up.add_argument("--yes", "-y", action="store_true",
                    help="install without asking for confirmation")
    up.set_defaults(fn=cmd_update)

    doc = sub.add_parser("doctor", help="check that local OCR/parse engines (tesseract, liteparse) are installed")
    doc.add_argument("--engine", action="append", choices=sorted(DOCTOR_ENGINES),
                     help="treat this engine as required (repeatable); default: only engines a model file declares")
    doc.add_argument("--json", action="store_true", help="machine-readable output (agents)")
    doc.set_defaults(fn=cmd_doctor)

    h = sub.add_parser("help", help="orientation and pointers to the instruction files")
    h.add_argument("topic", nargs="?", help="readme | mcp | historians | agents")
    h.set_defaults(fn=cmd_help)

    r = sub.add_parser("reindex", help="re-embed chunks (all documents, or only a subpath)")
    r.add_argument("--path", "--collection", default=None,
                   help="only reindex the document or collection at this dropbox subpath "
                        "(e.g. collections/COLX or collections/COLX/doc.pdf); default: every document")
    r.add_argument("--include-leased", action="store_true",
                        help="also process documents currently out on a hand-over")
    r.set_defaults(fn=cmd_reindex)

    e = sub.add_parser("export", help="regenerate per-page transcription files from the DB")
    e.set_defaults(fn=cmd_export)

    fl = sub.add_parser("filters", help="list the archive's stage filters (filters/<id>/)")
    fl.add_argument("--json", action="store_true", help="machine-readable output")
    fl.set_defaults(fn=cmd_filters)

    f1 = sub.add_parser("filter", help="run ONE stage filter over text (authoring/testing)")
    f1.add_argument("name", help="filter id (filters/<id>/)")
    f1.add_argument("--input", default=None, help="read text from this file (default: stdin)")
    f1.add_argument("--hook", default="editor.pre",
                    help="which hook's contract to use (default: editor.pre)")
    f1.add_argument("--params", action="append", metavar="K=V",
                    help="override a manifest param (repeatable)")
    f1.add_argument("--ctx", action="append", metavar="K=V",
                    help="add/replace a context key, e.g. page=12 (repeatable)")
    f1.add_argument("--doc", type=int, default=None,
                    help="populate the context from this document id")
    f1.add_argument("--json", action="store_true",
                    help="print the result envelope instead of the bare value")
    f1.set_defaults(fn=cmd_filter)

    rv = sub.add_parser("review", help="import corrections from library .md files into the DB")
    rv.add_argument("--doc", type=int, default=None, help="only review this document id")
    rv.add_argument("--page", type=int, default=None,
                    help="only review this page (with --doc; used by --unset)")
    rv.add_argument("--all", action="store_true",
                    help="import and stamp EVERY library page file, changed or not "
                         "(the deliberate blanket review; off by default)")
    rv.add_argument("--unset", action="store_true",
                    help="clear the reviewed stamp instead of importing, so the pages "
                         "can be re-scanned/re-edited (undoes a review; keeps the text)")
    rv.set_defaults(fn=cmd_review)

    rm = sub.add_parser("rm", help="remove document(s) from the index (by id or filename substring)")
    rm.add_argument("target")
    rm.set_defaults(fn=cmd_rm)

    prn = sub.add_parser("prune", help="remove orphaned render image caches (no registered document)")
    prn.add_argument("--dry-run", action="store_true",
                     help="report what would be removed without deleting")
    prn.add_argument("--library-variants", action="store_true",
                     help="instead of renders: delete a bare `edited-<rules>` folder whose pages "
                          "all survive elsewhere (the database, or its `@<model>` sibling) — a "
                          "folder holding a different reading is reported, never deleted")
    prn.add_argument("--doc", type=int, default=None,
                     help="with --library-variants: sweep only this document id")
    prn.set_defaults(fn=cmd_prune)

    pr = sub.add_parser("prompts", help="show prompt resolution")
    pr.add_argument("file", nargs="?")
    pr.set_defaults(fn=cmd_prompts)

    pa = sub.add_parser("palaeographer", help="show palaeographer resolution for a file")
    pa.add_argument("file", nargs="?")
    pa.set_defaults(fn=cmd_palaeographer)

    ed = sub.add_parser("editor", help="show editor resolution for a file")
    ed.add_argument("file", nargs="?")
    ed.set_defaults(fn=cmd_editor)

    e2 = sub.add_parser("edit", help="run the editor pass (all documents, or only a subpath)")
    e2.add_argument("--path", "--collection", default=None,
                    help="only edit documents under this subpath of the dropbox "
                         "(e.g. collections/COLX); default: every document")
    e2.add_argument("--page", type=int, default=None,
                    help="only edit this page number of each matched document "
                         "(combine with --path to target one page of one document)")
    e2.add_argument("--reprocess", action="store_true", help="re-edit everything matched")
    e2.add_argument("--include-leased", action="store_true",
                        help="also process documents currently out on a hand-over")
    e2.set_defaults(fn=cmd_edit)

    en = sub.add_parser("encoder", help="show encoder resolution for a file, or create one")
    en.add_argument("file", nargs="?", help="file to show encoder resolution for")
    en.add_argument("--new", action="store_true",
                    help="interactive wizard: create a new encoder file from samples")
    en.set_defaults(fn=cmd_encoder)

    ec = sub.add_parser("encode", help="run the encoder pass (structured records) over documents with an encoder")
    ec.add_argument("--reprocess", action="store_true", help="re-encode everything")
    ec.add_argument("--include-leased", action="store_true",
                        help="also process documents currently out on a hand-over")
    ec.set_defaults(fn=cmd_encode)

    tt = sub.add_parser("test", help="test a configuration on a sample of pages (transcription + editing + encoding)")
    tt.add_argument("target", nargs="?",
                    help="document or collection path under the dropbox (required unless --show)")
    tt.add_argument("--pages", "-n", type=int, default=3, help="number of pages to sample (default 3)")
    tt.add_argument("--random", action="store_true", help="sample random pages instead of the first N")
    tt.add_argument("--seed", type=int, default=None, help="random seed (with --random, for reproducibility)")
    tt.add_argument("--palaeographer", default=None, help="override palaeographer rules id")
    tt.add_argument("--editor", default=None, help="override editor rules id (or 'none'/'null'/'passthrough')")
    tt.add_argument("--encoder", default=None, help="override encoder id (collection-local or global)")
    tt.add_argument("--model", default=None, help="override the model interface id for all stages")
    tt.add_argument("--prompt", default=None, help="override the transcription prompt file")
    tt.add_argument("--temperature", type=float, default=None, help="override temperature for all stages")
    tt.add_argument("--max-tokens", type=int, default=None, help="override max_tokens for all stages")
    tt.add_argument("--show", action="store_true",
                    help="re-print the most recent test report without re-running the models")
    tt.add_argument("--list", action="store_true",
                    help="list saved test reports (no models are run)")
    tt.add_argument("--clean", action="store_true",
                    help="delete saved test report scratch dirs (all, or those matching target)")
    tt.add_argument("--dry-run", action="store_true",
                    help="with --clean: report what would be removed without deleting")
    tt.set_defaults(fn=cmd_test)

    k = sub.add_parser("key", help="manage API keys (OS secret store or .env)")
    k.add_argument("--set", metavar="NAME", help="store a value for NAME (read from stdin)")
    k.set_defaults(fn=cmd_key)

    sset = sub.add_parser("set", help="set a project setting (archive dir in config.yaml; secrets in .env)")
    ssub = sset.add_subparsers(dest="setting", required=True)
    sad = ssub.add_parser("archive-dir", help="set the archive data root (documents + definitions + generated output)")
    sad.add_argument("path", nargs="?", help="path to the archive directory (or prompted)")
    sad.set_defaults(fn=cmd_set_archive_dir)
    sdb = ssub.add_parser("dropbox", help="DEPRECATED: set only the dropbox documents folder")
    sdb.add_argument("path", nargs="?", help="path to the documents folder (or prompted)")
    sdb.set_defaults(fn=cmd_set_dropbox)
    sub.add_parser("archive-dir", help="alias for `pha set archive-dir`").set_defaults(fn=cmd_set_archive_dir)
    sub.add_parser("dropbox", help="DEPRECATED alias for `pha set dropbox`").set_defaults(fn=cmd_set_dropbox)

    mg = sub.add_parser("migrate-config", help="migrate legacy config to the models/ registry + pha.yaml sidecar layout")
    mg.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    mg.add_argument("--remove", action="store_true", help="remove converted palaeographer/editor selection files")
    mg.set_defaults(fn=cmd_migrate_config)

    ia = sub.add_parser("init-archive", help="create a new self-contained pha archive directory")
    ia.add_argument("path", help="path for the new archive (created if missing; must be empty if it exists)")
    ia.set_defaults(fn=cmd_init_archive)

    up = sub.add_parser("upload", help="copy a document or collection into the dropbox")
    upsub = up.add_subparsers(dest="kind", required=True)
    for k in ("document", "collection"):
        ps = upsub.add_parser(k, help=f"upload a {k} into the dropbox")
        ps.add_argument("path", help=f"path to the {k} (file , image-dir, or collection dir)")
        ps.add_argument("--name", default=None, help="destination name in the dropbox (default: source name)")
        ps.add_argument("--replace", action="store_true", help="replace an existing destination")
        ps.add_argument("--merge", action="store_true", help="copy into an existing destination, updating files")
        ps.set_defaults(fn=cmd_upload)

    bnd = sub.add_parser("bundle", help="export collections/documents into a portable bundle for another archive")
    bnd.add_argument("targets", nargs="+",
                     help="collection or document path(s) under the dropbox (e.g. collections/COLX or COLX)")
    bnd.add_argument("--out", "-o", default=None,
                     help="bundle directory (default: <target>_<date>.pha-bundle in the current directory)")
    bnd.add_argument("--force", action="store_true", help="overwrite an existing bundle directory")
    bnd.add_argument("--move", action="store_true",
                     help="MOVE, not copy: delete the bundled documents from THIS archive "
                          "after the bundle is written (the bundle is the backup)")
    bnd.set_defaults(fn=cmd_bundle)

    ho = sub.add_parser(
        "handoff",
        help="lend a document to a second machine and take the results back")
    hsub = ho.add_subparsers(dest="handoff_cmd")

    ho_out = hsub.add_parser("out", help="lease documents and write a hand-out payload")
    ho_out.add_argument("targets", nargs="+",
                        help="collection or document path(s) under the dropbox, or "
                             "inbox/<rel> to move a held document in first")
    ho_out.add_argument("--out", "-o", default=None, help="payload directory (default: <target>.pha-handoff)")
    ho_out.add_argument("--worker", default="", help="name of the machine taking the work (for `status`)")
    ho_out.add_argument("--force", action="store_true", help="supersede an existing hand-out of the same document")
    ho_out.add_argument("--force-inbox", action="store_true",
                        help="when an inbox target would overwrite a file already in the "
                             "dropbox, replace it instead of refusing")

    ho_in = hsub.add_parser("in", help="import a hand-out here (the worker machine) and leave it resumable")
    ho_in.add_argument("directory", help="the hand-out payload directory")
    ho_in.add_argument("--dry-run", action="store_true", help="print the plan; touch nothing")
    ho_in.add_argument("--keep-defs", action="store_true",
                       help="keep THIS archive's own definition files when they differ "
                            "from the hand-out's (default: adopt the hand-out's, so the "
                            "worker resolves the owner's stages exactly)")

    ho_work = hsub.add_parser("work", help="scan -> edit -> encode the handed-out documents")
    ho_work.add_argument("directory", help="the hand-out payload directory")
    ho_work.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")

    ho_back = hsub.add_parser("back", help="build the return payload from this archive's rows")
    ho_back.add_argument("directory", help="the hand-out payload directory")
    ho_back.add_argument("--out", "-o", default=None, help="result directory (default: <directory>-back)")
    ho_back.add_argument("--dry-run", action="store_true", help="report what would travel")

    ho_fetch = hsub.add_parser("fetch", help="apply a returned payload in place")
    ho_fetch.add_argument("directory", help="the result directory written by `handoff back`")
    ho_fetch.add_argument("--dry-run", action="store_true", help="print the plan; touch nothing")

    ho_status = hsub.add_parser("status", help="what is out on hand-over")
    ho_status.add_argument("--json", action="store_true", help="machine-readable output")

    ho_cancel = hsub.add_parser("cancel", help="release a lease without applying a result")
    ho_cancel.add_argument("handoff_id", help="the hand-off id from `pha handoff status`")

    ho.set_defaults(fn=cmd_handoff)

    ub = sub.add_parser("unbundle", help="import a pha bundle into this archive (no re-scan/re-edit)")
    ub.add_argument("bundle", help="path to the bundle directory created by `pha bundle`")
    ub.add_argument("--force", action="store_true",
                    help="replace documents/files that already exist in this archive")
    ub.set_defaults(fn=cmd_unbundle)

    args = parser.parse_args(argv)

    # Bare `pha` (no subcommand) shows help rather than a terse argparse error.
    if args.cmd is None:
        from types import SimpleNamespace
        cmd_help(cfg, SimpleNamespace(topic=None))
        return

    # Fresh-install guard: if no archive is configured and the default one is
    # empty, ask the user/agent where the archive is before running a command
    # that needs it. Setup commands (`set archive-dir`, `init-archive`,
    # `dropbox`, `key`) and `help` must always run so the guard can be
    # resolved and orientation is always available.
    if args.cmd not in ("set", "archive-dir", "dropbox", "init-archive", "key", "help", "update", "doctor") \
            and _archive_unconfigured(cfg):
        if _prompt_archive_setup(cfg):
            cfg = Config.load()  # reload now that archive_dir may have changed
        else:
            sys.exit(1)

    # Say which archive we resolved when it is worth saying (never to stdout:
    # `--json` output and piping must stay clean). Setup/diagnostic commands
    # are excluded so the notice cannot get in the way of fixing the problem.
    if args.cmd not in ("set", "archive-dir", "dropbox", "init-archive", "key",
                        "help", "update", "doctor", "info"):
        try:
            _warn_resolved_archive(cfg)
        except Exception:  # noqa: BLE001 - a notice must never break a command
            pass

    cfg.ensure_dirs()

    # Daily self-update notice: at most once per day, best-effort, and only
    # outside the `update` command itself (which does its own reporting).
    if args.cmd != "update":
        try:
            from .update import maybe_notify_update

            maybe_notify_update(cfg)
        except Exception:  # noqa: BLE001 - the notice must never break a command
            pass

    try:
        args.fn(cfg, args)
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            # _write already retries through short contention; reaching here
            # means another job held the DB past that window. Fail cleanly
            # instead of dumping a traceback.
            print(
                "the archive database is busy: another pha job (scan / edit / "
                "encode / review / reindex) is writing right now.\n"
                "Wait for it to finish, then run the command again.",
                file=sys.stderr,
            )
            sys.exit(2)
        raise


if __name__ == "__main__":
    main()

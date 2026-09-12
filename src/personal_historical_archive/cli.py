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

from . import db
from .config import Config
from .extract import is_supported, resolve_editor_id, resolve_encoder_id, resolve_palaeographer_id, resolve_prompt, encoder_files_for
from .ingest import (
    edit_all,
    encode_all,
    make_vision_client,
    prune_orphan_renders,
    reindex_all,
    remove_library_artifact,
    remove_render_if_orphaned,
    scan_once,
    watch,
    write_document_pages,
)
from .model_client import ModelClient, ModelError
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
                        path=getattr(args, "path", None))
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
                             collection=args.collection)
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
        }
        if getattr(args, "json", False):
            print(json.dumps({**meta, "text": text}, ensure_ascii=False, indent=2))
            return
        print(f"== {doc['filename']} — page {args.page}"
              + (f" [{editor_id}]" if edited else " [raw]") + " ==")
        if pf:
            print(f"   file: {pf}")
        print("")
        print(text or "(no text)")
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
    info = {
        "archive_dir": str(cfg.archive_dir),
        "db_path": str(cfg.db_path),
        "dropbox": str(cfg.dropbox),
        "library": str(cfg.library),
        "renders": str(cfg.renders),
        "notes": str(cfg.notes),
        "models": str(cfg.models_dir),
        "palaeographers": str(cfg.palaeographers_dir),
        "editors": str(cfg.editors_dir),
        "encoders": str(cfg.encoders_dir),
    }
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
        s = db.summary(conn)
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
        stats = db.chunk_stats(conn)
        stat_w = max((len(str(d["status"])) for d in docs), default=0)
        stat_w = max(stat_w, len("processing"))
        docs_by_key: dict[str, list] = {}
        for d in docs:
            docs_by_key.setdefault(d["dir_path"] or "(root)", []).append(d)

        try:
            pending = pending_review_files(cfg, conn)
        except Exception:
            pending = []

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

        if pending:
            print()
            for line in _pending_summary_lines(pending, lambda d_id: db.get_document(conn, d_id)):
                print(line)
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


def cmd_inbox(cfg: Config, args) -> None:
    """Manage documents parked ON HOLD in the inbox.

    Documents sitting in <archive_dir>/inbox are never scanned; `pha status`
    reports them as 'on hold'. `pha inbox` lists them; `pha inbox --dry-run`
    shows the move plan; `pha inbox --move` relocates them into the dropbox
    (preserving the relative layout) so a following `pha scan` ingests them."""
    held = _inbox_held(cfg)
    move = bool(getattr(args, "move", False))
    dry = bool(getattr(args, "dry_run", False))

    # --dry-run takes precedence: show the move plan without touching anything.
    if dry:
        if not held:
            print("inbox is empty (nothing to move)")
            return
        print(f"would move {len(held)} file(s) from the inbox into the dropbox:")
        for p in held:
            print(f"  {p.relative_to(cfg.inbox)}  →  dropbox/{p.relative_to(cfg.inbox)}")
        print("  (run `pha inbox --move` to move them, then `pha scan`)")
        return

    if move:
        if not held:
            print("inbox is empty (nothing to move)")
            return
        for entry in sorted(cfg.inbox.iterdir()):
            if entry.name.startswith("."):
                continue
            _move_into(entry, cfg.dropbox / entry.name)
        print(f"moved {len(held)} file(s) from the inbox into the dropbox")
        print("  →  run `pha scan` to ingest them")
        return

    if not held:
        print("inbox is empty — park documents for later in " + str(cfg.inbox))
        return

    width = _term_width()
    holds: dict[str, list[str]] = {}
    for p in held:
        rel = p.relative_to(cfg.inbox)
        key = str(rel.parent) if str(rel.parent) != "." else "(inbox root)"
        holds.setdefault(key, []).append(rel.name)
    print("inbox (on hold, never scanned): " + str(cfg.inbox))
    for key in sorted(holds, key=lambda k: (k == "(inbox root)", k)):
        d = key[len("collections/"):] if key.startswith("collections/") else key
        print(_fit(f"  {d}", width))
        names = holds[key]
        prefix = f"    {len(names)} file(s)  ("
        listing = _snip(names, width - len(prefix) - 1)
        print(_fit(prefix + listing + ")", width))
    print("  →  `pha inbox --move` to put them in the dropbox, then `pha scan`")


def cmd_reindex(cfg: Config, args) -> None:
    client = _client(cfg, cfg.embed_base_url, cfg.embed_timeout_s)
    try:
        res = reindex_all(cfg, client, path=args.path)
    finally:
        client.close()
    print(f"reindexed {res['reindexed']} document(s)")


def cmd_review(cfg: Config, args) -> None:
    """Import human corrections from the library markdown files into the DB.

    The historian edits library/.../transcription-<pal>/<stem>.md or
    edited-<editor>/<stem>.md; `pha review` reads those files back and updates
    pages.raw_text / page_edits.text, stamping them reviewed.

    Correcting a transcription-* page fixes the palaeographer's reading: that
    page is never re-read by `pha scan`, and you should then run `pha edit` so
    the editor re-processes just that page from your corrected text. Correcting
    an edited-* page fixes the final output, which neither `pha scan` nor
    `pha edit` will overwrite. Run `pha reindex` afterwards so search uses the
    corrected text.
    """
    from .ingest import review_import
    conn = db.connect(cfg.db_path)
    try:
        res = review_import(cfg, conn, doc_id=args.doc, verbose=True)
    finally:
        conn.close()
    print(f"reviewed: {res['pages']} transcription page(s), {res['edits']} edit(s) "
          f"(skipped {res['skipped']} unparsed files)")


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
    for kind, names in res["defs_installed"].items():
        if names:
            print(f"  installed defs: {kind}: {' '.join(names)}")
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
                write_edited_pages(cfg, conn, d["id"], d["editor"])
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
    is gone or was superseded)."""
    conn = db.connect(cfg.db_path)
    try:
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
    # re-index edited docs so the search covers both raw and edited variants
    if edited:
        client = _client(cfg, cfg.embed_base_url, cfg.embed_timeout_s)
        try:
            reindex_all(cfg, client, verbose=False)
        finally:
            client.close()


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
    for d in (cfg.palaeographers_dir, cfg.editors_dir):
        for f in d.glob("*.md"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("api_key:"):
                    m = re.search(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line)
                    if m:
                        names.add(m.group(1))
    if not names:
        print("no ${...} api_key references found in palaeographers/editors")
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
    gitignored project .env. Shared by `pha set archive-dir` and the
    deprecated `pha set dropbox`."""
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


def cmd_set_archive_dir(cfg: Config, args) -> None:
    """`pha set archive-dir` (or `pha archive-dir`) — set the archive data root.

    Stores PHA_ARCHIVE_DIR in the gitignored project .env. All data —
    documents (dropbox), model definitions (palaeographers/editors/encoders)
    and generated output (library, renders, db) — lives under this directory.
    Read back automatically on the next `pha` run (never committed)."""
    path = getattr(args, "path", None)
    _set_env_in_dotenv(cfg, "PHA_ARCHIVE_DIR", "Archive directory",
                       str(getattr(cfg, "archive_dir", "")), path)


def cmd_init_archive(cfg: Config, args) -> None:
    """`pha init-archive <PATH>` — create a new self-contained pha archive.

    Creates the default structure (dropbox/documents, dropbox/collections,
    library, renders, notes, palaeographers/editors/encoders with zero-config
    defaults) plus a README.md, AGENTS.md and a .gitignore. If PATH does not
    exist it is created; if it exists it must be empty (never touches an
    existing archive)."""
    from .archive_init import init_archive
    try:
        p = init_archive(args.path, project_root=cfg.root)
    except (FileExistsError, NotADirectoryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return
    print(f"created archive at {p}")
    print("  dropbox/documents/  dropbox/collections/   (drop your sources here)")
    print("  library/  renders/  notes/  palaeographers/  editors/  encoders/")
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

    declared: dict[str, list[str]] = {}
    for m_id, m in sorted((cfg.models or {}).items()):
        eng = (m.engine or "").strip().lower()
        if eng in doctor.ENGINES:
            declared.setdefault(eng, []).append(m_id)
    require = set(getattr(args, "engine", None) or [])
    report = doctor.diagnose(
        declared=declared,
        require=require,
        archive=None if _archive_unconfigured(cfg) else str(cfg.archive_dir),
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


def _create_and_set(cfg: Config, path: Path) -> None:
    """Create a new archive at `path` and point pha at it (persist PHA_ARCHIVE_DIR)."""
    from .archive_init import init_archive
    try:
        p = init_archive(str(path), project_root=cfg.root)
    except (FileExistsError, NotADirectoryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return
    _set_env_in_dotenv(cfg, "PHA_ARCHIVE_DIR", "Archive directory",
                       str(cfg.archive_dir), str(p))
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
            _set_env_in_dotenv(cfg, "PHA_ARCHIVE_DIR", "Archive directory",
                               str(cfg.archive_dir), None)
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
    s.set_defaults(fn=cmd_scan)

    q = sub.add_parser("search", help="search the extracted text")
    q.add_argument("query")
    q.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default=None)
    q.add_argument("--limit", type=int, default=None)
    q.add_argument("--collection", default=None,
                   help="restrict to a collection/dir, e.g. 'documents', 'COLX' or 'collections/COLX'")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_search)

    st = sub.add_parser("status", help="archive summary")
    st.set_defaults(fn=cmd_status)

    inf = sub.add_parser(
        "info",
        help="print the archive's resolved paths (fast, read-only: no DB, no engine probing)")
    inf.add_argument("--json", action="store_true", help="structured output")
    inf.set_defaults(fn=cmd_info)

    ib = sub.add_parser("inbox", help="list documents on hold, or move them into the dropbox")
    ib.add_argument("--move", action="store_true",
                    help="move held documents into the dropbox (then `pha scan`)")
    ib.add_argument("--dry-run", action="store_true",
                    help="show what --move would do without moving anything")
    ib.set_defaults(fn=cmd_inbox)

    pg = sub.add_parser("page", help="print the full transcription of one page")
    pg.add_argument("doc", help="document id (number) or a filename substring")
    pg.add_argument("page", type=int, help="page number")
    pg.add_argument("--edited", action="store_true",
                    help="show the edited variant (translated/modernized) instead of the raw reading")
    pg.add_argument("--editor", default=None, help="editor id when --edited (default: the document's editor)")
    pg.add_argument("--json", action="store_true", help="structured output for agents")
    pg.set_defaults(fn=cmd_page)

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
    r.set_defaults(fn=cmd_reindex)

    e = sub.add_parser("export", help="regenerate per-page transcription files from the DB")
    e.set_defaults(fn=cmd_export)

    rv = sub.add_parser("review", help="import corrections from library .md files into the DB")
    rv.add_argument("--doc", type=int, default=None, help="only review this document id")
    rv.set_defaults(fn=cmd_review)

    rm = sub.add_parser("rm", help="remove document(s) from the index (by id or filename substring)")
    rm.add_argument("target")
    rm.set_defaults(fn=cmd_rm)

    prn = sub.add_parser("prune", help="remove orphaned render image caches (no registered document)")
    prn.add_argument("--dry-run", action="store_true",
                     help="report what would be removed without deleting")
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
    e2.set_defaults(fn=cmd_edit)

    en = sub.add_parser("encoder", help="show encoder resolution for a file, or create one")
    en.add_argument("file", nargs="?", help="file to show encoder resolution for")
    en.add_argument("--new", action="store_true",
                    help="interactive wizard: create a new encoder file from samples")
    en.set_defaults(fn=cmd_encoder)

    ec = sub.add_parser("encode", help="run the encoder pass (structured records) over documents with an encoder")
    ec.add_argument("--reprocess", action="store_true", help="re-encode everything")
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

    sset = sub.add_parser("set", help="set a project setting (stored in gitignored .env)")
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

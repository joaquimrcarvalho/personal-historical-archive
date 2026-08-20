from __future__ import annotations

import argparse
import os
import json
import re
import sys
from datetime import datetime

from . import db
from .config import Config
from .extract import is_supported, resolve_editor_id, resolve_encoder_id, resolve_palaeographer_id, resolve_prompt, encoder_files_for
from .ingest import (
    edit_all,
    encode_all,
    make_vision_client,
    reindex_all,
    remove_library_artifact,
    scan_once,
    watch,
    write_document_pages,
)
from .model_client import ModelClient, ModelError


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
    print(f"\n{len(res['results'])} result(s) in mode '{res['mode']}'")


def cmd_status(cfg: Config, args) -> None:
    conn = db.connect(cfg.db_path)
    try:
        s = db.summary(conn)
        print(f"archive: {cfg.db_path}")
        print(f"  documents: {s['documents'] or 'none'}")
        print(f"  pages extracted: {s['pages_done']}")
        print(f"  chunks indexed: {s['chunks']} (embedded: {s['chunks_embedded']})")
        groups = conn.execute(
            "SELECT COALESCE(NULLIF(dir_path, ''), '(root)') AS col, COUNT(*) n FROM documents GROUP BY col ORDER BY col"
        ).fetchall()
        if groups:
            print("  collections:")
            for g in groups:
                print(f"    {g['col']}: {g['n']}")
        docs = db.list_documents(conn, limit=100)
        if docs:
            print("\ndocuments:")
            for d in docs:
                err = f"  ERROR: {(d['error'] or '')[:60]}" if d["status"] == "error" else ""
                col = d["dir_path"] or "(root)"
                pal = d["palaeographer"] or "default"
                print(f"  #{d['id']:3d} {d['status']:10s} [{col}] {d['filename']}  ({d['kind']}, {d['page_count'] or 0} pages, {pal})  updated {_fmt_ts(d['updated_at'])}{err}")
    finally:
        conn.close()


def cmd_reindex(cfg: Config, args) -> None:
    client = _client(cfg, cfg.embed_base_url, cfg.embed_timeout_s)
    try:
        res = reindex_all(cfg, client)
    finally:
        client.close()
    print(f"reindexed {res['reindexed']} document(s)")


def cmd_export(cfg: Config, args) -> None:
    """Regenerate per-page transcription files from the DB (no re-extraction)."""
    conn = db.connect(cfg.db_path)
    try:
        docs = db.list_documents(conn, limit=10000)
        n = 0
        for d in docs:
            out = write_document_pages(cfg, conn, d["id"])
            if out:
                n += 1
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
    finally:
        conn.close()


def cmd_palaeographer(cfg: Config, args) -> None:
    if args.file:
        p = cfg.dropbox / args.file if not (cfg.root / args.file).exists() else cfg.root / args.file
        if not p.exists():
            print(f"not found: {args.file}")
            return
        pal_id, source = resolve_palaeographer_id(
            p.stem, p if p.is_dir() else p.parent, cfg.dropbox
        )
        pal = cfg.get_palaeographer(pal_id) if pal_id else cfg.get_palaeographer()
        print(f"palaeographer: {pal.id} ({pal.description or pal.model})")
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


def cmd_editor(cfg: Config, args) -> None:
    if args.file:
        p = cfg.dropbox / args.file if not (cfg.root / args.file).exists() else cfg.root / args.file
        if not p.exists():
            print(f"not found: {args.file}")
            return
        ed_id, source = resolve_editor_id(
            p.stem, p if p.is_dir() else p.parent, cfg.dropbox
        )
        if ed_id and ed_id in cfg.editors:
            ed = cfg.editors[ed_id]
            print(f"editor: {ed.id} ({ed.description or ed.model})")
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


def cmd_edit(cfg: Config, args) -> None:
    res = edit_all(cfg, reprocess=args.reprocess, verbose=True)
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


def cmd_set_dropbox(cfg: Config, args) -> None:
    """`pha set dropbox` (or `pha dropbox`) — point at the documents folder.

    Asks for a path (absolute, or ~ shorthand), stores it as PHA_DROPBOX in
    the gitignored project .env, and confirms the resolved location. This is
    the friendly way for a historian to set the dropbox without editing
    config.yaml or exporting an env var. The value is read back automatically
    on the next `pha` run (it is not committed)."""
    path = getattr(args, "path", None) or (getattr(args, "dropbox", None) or None)
    if not path:
        try:
            if not sys.stdin.isatty():
                path = sys.stdin.readline().strip()
        except Exception:
            path = None
    if not path:
        cur = getattr(cfg, "dropbox", None)
        print("Dropbox (documents) folder:")
        print(f"  current: {cur}")
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
                 if l.strip() and not l.startswith("PHA_DROPBOX=")] if envp.exists() else []
        lines.append(f"PHA_DROPBOX={expanded}")
        envp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"stored dropbox -> {expanded}  (in {envp}, gitignored)")
    else:
        print(f"dropbox unchanged: {cur}")


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


def cmd_encoder_new(cfg: Config, args) -> None:
    from .encoder_helper import run
    raise SystemExit(run(cfg))


def cmd_mcp(cfg: Config, args) -> None:
    from . import mcp_server

    mcp_server.main(args.transport, args.host, args.port)


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> None:
    cfg = Config.load()
    cfg.ensure_dirs()

    parser = argparse.ArgumentParser(
        prog="pha",
        description="Personal Historical Archive (pha): drop folder -> VLM extraction -> index -> MCP search.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

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

    m = sub.add_parser("mcp", help="run the MCP server (stdio or sse)")
    m.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8000)
    m.set_defaults(fn=cmd_mcp)

    r = sub.add_parser("reindex", help="re-embed all chunks")
    r.set_defaults(fn=cmd_reindex)

    e = sub.add_parser("export", help="regenerate per-page transcription files from the DB")
    e.set_defaults(fn=cmd_export)

    rm = sub.add_parser("rm", help="remove document(s) from the index (by id or filename substring)")
    rm.add_argument("target")
    rm.set_defaults(fn=cmd_rm)

    pr = sub.add_parser("prompts", help="show prompt resolution")
    pr.add_argument("file", nargs="?")
    pr.set_defaults(fn=cmd_prompts)

    pa = sub.add_parser("palaeographer", help="show palaeographer resolution for a file")
    pa.add_argument("file", nargs="?")
    pa.set_defaults(fn=cmd_palaeographer)

    ed = sub.add_parser("editor", help="show editor resolution for a file")
    ed.add_argument("file", nargs="?")
    ed.set_defaults(fn=cmd_editor)

    e2 = sub.add_parser("edit", help="run the editor pass over all documents with an editor")
    e2.add_argument("--reprocess", action="store_true", help="re-edit everything")
    e2.set_defaults(fn=cmd_edit)

    en = sub.add_parser("encoder", help="show encoder resolution for a file, or create one")
    en.add_argument("file", nargs="?", help="file to show encoder resolution for")
    en.add_argument("--new", action="store_true",
                    help="interactive wizard: create a new encoder file from samples")
    en.set_defaults(fn=cmd_encoder)

    ec = sub.add_parser("encode", help="run the encoder pass (structured records) over documents with an encoder")
    ec.add_argument("--reprocess", action="store_true", help="re-encode everything")
    ec.set_defaults(fn=cmd_encode)

    k = sub.add_parser("key", help="manage API keys (OS secret store or .env)")
    k.add_argument("--set", metavar="NAME", help="store a value for NAME (read from stdin)")
    k.set_defaults(fn=cmd_key)

    sset = sub.add_parser("set", help="set a project setting (stored in gitignored .env)")
    ssub = sset.add_subparsers(dest="setting", required=True)
    sdb = ssub.add_parser("dropbox", help="set the dropbox documents folder path")
    sdb.add_argument("path", nargs="?", help="path to the documents folder (or prompted)")
    sdb.set_defaults(fn=cmd_set_dropbox)
    sub.add_parser("dropbox", help="alias for `pha set dropbox`").set_defaults(fn=cmd_set_dropbox)

    up = sub.add_parser("upload", help="copy a document or collection into the dropbox")
    upsub = up.add_subparsers(dest="kind", required=True)
    for k in ("document", "collection"):
        ps = upsub.add_parser(k, help=f"upload a {k} into the dropbox")
        ps.add_argument("path", help=f"path to the {k} (file , image-dir, or collection dir)")
        ps.add_argument("--name", default=None, help="destination name in the dropbox (default: source name)")
        ps.add_argument("--replace", action="store_true", help="replace an existing destination")
        ps.add_argument("--merge", action="store_true", help="copy into an existing destination, updating files")
        ps.set_defaults(fn=cmd_upload)

    args = parser.parse_args(argv)
    args.fn(cfg, args)


if __name__ == "__main__":
    main()

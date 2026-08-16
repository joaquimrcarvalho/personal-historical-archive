from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

from . import db
from .config import Config
from .extract import is_supported, resolve_palaeographer_id, resolve_prompt
from .ingest import (
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
    try:
        if args.watch:
            watch(cfg, client, pal, explicit_prompt=args.prompt, debounce_s=args.debounce)
            return
        res = scan_once(cfg, client, pal, explicit_prompt=args.prompt, reprocess=args.reprocess)
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
        print(f"{i:2d}. [{r['source']:8s}] {r['filename']}  [{r['collection']}]  p.{r['page_no']}  score={r['score']}")
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
    pal_files = []
    for pat in ("palaeographer", "palaeographer.txt", "palaeographer.md",
                "*.palaeographer", "*.palaeographer.txt", "*.palaeographer.md"):
        pal_files.extend(cfg.dropbox.rglob(pat))
    for f in sorted(set(pal_files)):
        pal_id = re.sub(r"^[#\-*\s]+", "", f.read_text().strip().splitlines()[0]).strip() if f.read_text().strip() else ""
        print(f"  {f}: {pal_id or '(empty)'}")


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

    args = parser.parse_args(argv)
    args.fn(cfg, args)


if __name__ == "__main__":
    main()

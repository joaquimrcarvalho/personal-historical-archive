"""pha bundle / pha unbundle — move or share collections between archives
WITHOUT re-running the palaeographer (vision) or editor (text) stages.

A bundle is a portable directory:

    <out>/
      manifest.json          # format/version + per-document metadata
      dropbox/...            # source documents + selection files + collection encoders
      library/...            # transcription-*, edited-*, records-*.json, concatenated-*.md
      renders/<sha>/...      # rendered page images (so images keep working, no re-render)
      defs/                  # palaeographer/editor/encoder .md definition files used
        palaeographers/<id>.md
        editors/<id>.md
        encoders/<id>.md
        prompts/<name>.md

`pha bundle <collection-or-doc...>` writes such a directory; the receiving
archive runs `pha unbundle <dir>` and the documents appear in ITS database
with NEW ids (no collisions with existing documents), their pages, edits,
records and reviewed stamps intact, and their chunks indexed for search.
Nothing is re-extracted or re-edited: the bundle carries the finished text.

Design notes
------------
- The archive DB is the source of truth; `library/` is derived output. The
  bundle therefore carries the library files as the *transport* of the texts
  (they are self-describing: front matter has page/palaeographer/editor/
  reviewed, the body is the text). unbundle rebuilds the DB rows from them,
  then REGENERATES the library files from the DB so front matter carries B's
  new document ids and paths (the copied files' old ids are never trusted).
- `documents.path` stores absolute paths, so every imported document is
  re-registered at B's own dropbox path; sha256 is recomputed from the bytes
  actually present in B (a mismatched file is skipped unless --force).
- Reviewed stamps survive: reviewed pages/edits are marked reviewed_at at
  import, so later `pha scan`/`pha edit` (even --reprocess) never overwrite
  the human corrections.
- Encoder records are imported with created_at >= the pages' timestamps, so
  `pha encode` sees the records as current and does not re-run the encoder.
- Model definitions referenced by the exported documents travel in defs/ and
  are installed in B only when B does not already have that id (B's own
  definitions are never overwritten).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import db
from .extract import (
    encoder_files_for,
    is_supported,
    resolve_editor_id,
    resolve_encoder_id,
    resolve_palaeographer_id,
)
from .ingest import (
    _acquire_scan_lock,
    _doc_slug,
    _is_document_dir,
    _parse_library_file,
    _raw_sha,
    _release_scan_lock,
    discover,
    index_document,
    remove_library_artifact,
    sha256_of,
    sha256_of_dir,
    write_document_pages,
    write_edited_pages,
    write_records_file,
)
from .model_client import ModelClient

BUNDLE_FORMAT = "pha-bundle"
BUNDLE_VERSION = 1


# --------------------------------------------------------------------------- helpers

def _copy2(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists() and dst.is_dir():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _resolve_target(cfg, target: str) -> Path | None:
    """Resolve a CLI target to a path under the dropbox.

    Accepts dropbox-relative paths ('collections/COLX', 'documents/foo.pdf')
    and bare collection names ('COLX' -> collections/COLX). Absolute paths
    must already be inside the dropbox."""
    p = Path(target)
    cands: list[Path] = []
    if p.is_absolute():
        cands = [p]
    else:
        cands = [cfg.dropbox / p]
        if "/" not in target and not target.startswith("."):
            cands.append(cfg.dropbox / "collections" / target)
            cands.append(cfg.dropbox / "documents" / target)
    for c in cands:
        if c.exists():
            return c
    return None


def _document_units(cfg, target: Path) -> list[Path]:
    """The document unit(s) a target covers: the file itself, an image-dir
    document as ONE unit, or every supported file under a collection dir."""
    if target.is_file():
        return [target] if is_supported(target.name) else []
    if _is_document_dir(target):
        return [target]
    return discover(cfg.dropbox, cfg.dir_documents, root=target)


# --------------------------------------------------------------------------- export

def export_bundle(
    cfg,
    targets: list[str],
    out: Path,
    force: bool = False,
    move: bool = False,
    verbose: bool = True,
) -> dict:
    """Export the given collections/documents into a portable bundle directory.

    Only documents that already have an archive record are included (the point
    is to move finished scan+edit results, not raw files); files without a DB
    row are reported and skipped. With `move=True` the bundled documents are
    DELETED from this archive after the bundle is fully written (the bundle is
    the backup) — a true move instead of a copy. Returns a summary dict.
    """
    cfg.ensure_dirs()
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not force:
        raise FileExistsError(
            f"bundle directory {out} already exists and is not empty "
            f"(re-run with --force to overwrite)"
        )
    if out.exists() and force:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    bdrop = out / "dropbox"
    blib = out / "library"
    brend = out / "renders"
    bdefs = {
        "palaeographers": out / "defs" / "palaeographers",
        "editors": out / "defs" / "editors",
        "encoders": out / "defs" / "encoders",
        "prompts": out / "defs" / "prompts",
    }

    conn = db.connect(cfg.db_path)
    try:
        units: list[Path] = []
        coll_dirs: set[Path] = set()
        missing_targets: list[str] = []
        for t in targets:
            p = _resolve_target(cfg, t)
            if p is None:
                missing_targets.append(t)
                continue
            if p.is_dir() and not _is_document_dir(p):
                coll_dirs.add(p)  # a collection dir bundled wholesale
            for u in _document_units(cfg, p):
                if u not in units:
                    units.append(u)
        if missing_targets:
            print(f"  ! target not found in the dropbox: {', '.join(missing_targets)}")

        copied: set[Path] = set()
        defs_used: dict[str, list[str]] = {"palaeographers": [], "editors": [],
                                           "encoders": [], "prompts": []}
        manifest_docs: list[dict] = []
        bundled: list[tuple] = []
        skipped: list[str] = []

        for u in sorted(units, key=str):
            row = db.get_document_by_path(conn, str(u))
            if row is None:
                skipped.append(str(u.relative_to(cfg.dropbox)))
                print(f"  ! {u.relative_to(cfg.dropbox)}: no archive record yet "
                      f"(run `pha scan` first); skipping", flush=True)
                continue
            rel = Path(row["path"]).relative_to(cfg.dropbox)
            bundled.append((row, u))

            # --- dropbox payload: the document itself + resolved selection files
            src = Path(row["path"])
            if src.is_dir():
                _copy_tree(src, bdrop / rel)
            else:
                _copy2(src, bdrop / rel)
            copied.add(src)
            stem = rel.stem
            fdir = src if src.is_dir() else src.parent
            pal_id, pal_src = resolve_palaeographer_id(stem, fdir, cfg.dropbox)
            ed_id, ed_src = resolve_editor_id(stem, fdir, cfg.dropbox)
            for s in (pal_src, ed_src):
                if not s or s.startswith("flag:"):
                    continue
                sp = Path(s)
                if sp.is_relative_to(cfg.dropbox) and sp not in copied:
                    _copy2(sp, bdrop / sp.relative_to(cfg.dropbox))
                    copied.add(sp)
            # encoder definitions live next to the sources: copy the encoders
            # directory that holds each resolved definition (covers the def,
            # its .prompt.md/.langextract.md companions and any others).
            enc_files = encoder_files_for(stem, fdir, cfg.dropbox)
            for enc in enc_files:
                enc_dir = enc.parent
                if enc_dir.is_relative_to(cfg.dropbox) and enc_dir not in copied:
                    rel_enc = enc_dir.relative_to(cfg.dropbox)
                    _copy_tree(enc_dir, bdrop / rel_enc)
                    copied.add(enc_dir)
                    copied.update(enc_dir.rglob("*"))
            # the resolved prompt file, when it lives in the dropbox
            prompt_src = row["prompt_source"]
            if prompt_src and not prompt_src.startswith("builtin"):
                pp = Path(prompt_src)
                if pp.is_relative_to(cfg.dropbox) and pp not in copied:
                    _copy2(pp, bdrop / pp.relative_to(cfg.dropbox))
                    copied.add(pp)

            # --- library folder (the finished transcriptions/edits/records)
            slug = _doc_slug(row)
            rel_dir = Path(row["dir_path"] or "")
            libdir = cfg.library / rel_dir / slug
            if libdir.is_dir():
                _copy_tree(libdir, blib / rel_dir / slug)

            # --- renders (page images, keyed by sha)
            rdir = cfg.renders / row["sha256"]
            if rdir.is_dir():
                _copy_tree(rdir, brend / row["sha256"])

            # --- model definitions used by this document
            pal = row["palaeographer"]
            if pal and pal in cfg.palaeographers:
                pf = cfg.palaeographers[pal].prompt_file
                if pf and pf.exists() and pf.name not in defs_used["palaeographers"]:
                    _copy2(pf, bdefs["palaeographers"] / pf.name)
                    defs_used["palaeographers"].append(pf.name)
            ed = row["editor"]
            if ed and ed in cfg.editors:
                ef = cfg.editors[ed].prompt_file
                if ef and ef.exists() and ef.name not in defs_used["editors"]:
                    _copy2(ef, bdefs["editors"] / ef.name)
                    defs_used["editors"].append(ef.name)
            enc_ids: set[str] = set()
            for enc in enc_files:
                enc_ids.add(enc.stem)
            sel_enc, _esrc = resolve_encoder_id(stem, fdir, cfg.dropbox)
            if sel_enc:
                enc_ids.add(sel_enc)
            for eid in sorted(enc_ids):
                if eid in cfg.encoders:
                    encf = cfg.encoders[eid].prompt_file
                    if (encf and encf.exists() and encf.is_relative_to(cfg.encoders_dir)
                            and encf.name not in defs_used["encoders"]):
                        _copy2(encf, bdefs["encoders"] / encf.name)
                        defs_used["encoders"].append(encf.name)
            if prompt_src and not prompt_src.startswith("builtin"):
                pp = Path(prompt_src)
                if pp.is_relative_to(cfg.prompts) and pp.exists() \
                        and pp.name not in defs_used["prompts"]:
                    _copy2(pp, bdefs["prompts"] / pp.name)
                    defs_used["prompts"].append(pp.name)

            manifest_docs.append({
                "relpath": str(rel),
                "filename": row["filename"],
                "kind": row["kind"],
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "mtime": row["mtime"],
                "page_count": row["page_count"],
                "dir_path": row["dir_path"] or "",
                "palaeographer": pal,
                "editor": ed,
                "prompt_source": prompt_src,
                "status": row["status"],
                "slug": slug,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
            if verbose:
                print(f"  + {rel}  ({row['status']}, {row['page_count'] or 0} pages, "
                      f"pal: {pal or 'default'})", flush=True)

        manifest = {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "source_archive": str(cfg.archive_dir),
            "created_at": time.time(),
            "defs": defs_used,
            "documents": manifest_docs,
        }
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        moved = {}
        if move and manifest_docs:
            # the bundle is fully written — only now is it safe to delete the
            # originals from this archive (the bundle is the backup).
            moved = _remove_bundled(cfg, conn, bundled, copied, coll_dirs, verbose=verbose)
        return {
            "out": str(out),
            "documents": len(manifest_docs),
            "skipped": skipped,
            "defs": defs_used,
            "moved": moved,
        }
    finally:
        conn.close()


def _remove_bundled(
    cfg, conn, bundled: list, copied: set[Path], coll_dirs: set[Path],
    verbose: bool = True,
) -> dict:
    """Delete the bundled documents from the source archive (--move).

    Runs only after the bundle directory (including manifest.json) has been
    fully written, so the bundle is the backup. Removes:

    - the dropbox payload that was actually copied: for documents inside a
      collection that was bundled wholesale, every copied path under that
      collection (document files, selection files, encoders dir); for a
      single-document target, only the document itself (shared sidecars such
      as a collection's `palaeographer`/`editor`/`encoders/` are left for
      sibling documents). Stray files that were NOT bundled (no archive
      record) are never touched.
    - the library artifact folder and the database row for every bundled doc.
    """
    unit_paths = {u for _row, u in bundled}
    covered = {c for c in coll_dirs if any(u != c and c in u.parents for u in unit_paths)}
    to_remove: set[Path] = set()
    if covered:
        for c in covered:
            for p in copied:
                if p == c or c in p.parents:
                    to_remove.add(p)
    else:
        to_remove = set(unit_paths)

    removed_paths = 0
    for p in sorted(to_remove, key=lambda x: -len(x.parts)):
        if p == cfg.dropbox or not p.exists():
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed_paths += 1
            if verbose:
                print(f"  - dropbox/{p.relative_to(cfg.dropbox)}", flush=True)
        except OSError as e:
            print(f"  ! could not remove dropbox/{p.relative_to(cfg.dropbox)}: {e}",
                  flush=True)
    # prune now-empty parent directories (never the dropbox root itself)
    for p in sorted(to_remove, key=lambda x: len(x.parts)):
        d = p if p.is_dir() else p.parent
        while d != cfg.dropbox and d.exists() and cfg.dropbox in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent

    docs = 0
    for row, _u in bundled:
        remove_library_artifact(cfg, row)
        db.delete_document(conn, row["id"])
        docs += 1
    conn.commit()
    return {"documents": docs, "dropbox_paths": removed_paths}


# --------------------------------------------------------------------------- import

def _install_defs(cfg, bundle_dir: Path, verbose: bool = True) -> dict:
    """Install bundled model definitions that B does not already have.
    Never overwrites an existing definition in B."""
    installed: dict[str, list[str]] = {}
    targets = {
        "palaeographers": cfg.palaeographers_dir,
        "editors": cfg.editors_dir,
        "encoders": cfg.encoders_dir,
        "prompts": cfg.prompts,
    }
    for kind, dst_dir in targets.items():
        src_dir = bundle_dir / "defs" / kind
        if not src_dir.is_dir():
            continue
        got = []
        for f in sorted(src_dir.iterdir()):
            if not f.is_file():
                continue
            dst = dst_dir / f.name
            if dst.exists():
                if verbose:
                    print(f"  - def {kind}/{f.name}: already in this archive; keeping it", flush=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            got.append(f.name)
            if verbose:
                print(f"  + def {kind}/{f.name} installed", flush=True)
        if got:
            installed[kind] = got
    return installed


def _copy_dropbox_payload(cfg, bundle_dir: Path, force: bool, verbose: bool = True) -> tuple[list[str], list[str]]:
    """Copy the bundle's dropbox payload into B. Returns (copied, skipped):
    files are skipped (not overwritten) when they already exist and --force
    is off."""
    copied: list[str] = []
    skipped: list[str] = []
    src_root = bundle_dir / "dropbox"
    if not src_root.is_dir():
        return copied, skipped
    for f in sorted(src_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src_root)
        dst = cfg.dropbox / rel
        if dst.exists() and not force:
            skipped.append(str(rel))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        copied.append(str(rel))
    if verbose:
        for r in copied:
            print(f"  + dropbox/{r}", flush=True)
        for r in skipped:
            print(f"  ~ dropbox/{r}: already present (--force to overwrite)", flush=True)
    return copied, skipped


def _pin_selections(cfg, target: Path, rel: Path, md: dict, verbose: bool = True) -> list[str]:
    """Ensure B resolves the recorded palaeographer/editor for an imported doc.

    Returns the list of pin files written. A pin is written ONLY when no
    selection file resolves the id in B (i.e. B would fall back to its active
    default); an existing selection file that resolves differently is a real
    conflict — reported, not overwritten."""
    pins: list[str] = []
    fdir = target if target.is_dir() else target.parent
    stem = target.stem
    dir_path = Path(md.get("dir_path") or "")

    def _pin_path(kind: str) -> Path:
        if dir_path and dir_path.parts:
            return cfg.dropbox / dir_path / kind
        return fdir / f"{stem}.{kind}"

    for kind, recorded in (("palaeographer", md.get("palaeographer")),
                           ("editor", md.get("editor"))):
        if not recorded:
            continue
        if kind == "palaeographer":
            rid, rsrc = resolve_palaeographer_id(stem, fdir, cfg.dropbox)
        else:
            rid, rsrc = resolve_editor_id(stem, fdir, cfg.dropbox)
        if rid == recorded:
            continue
        if rsrc is None:
            pin = _pin_path(kind)
            if not pin.exists():
                pin.write_text(recorded + "\n", encoding="utf-8")
                pins.append(str(pin.relative_to(cfg.dropbox)))
                if verbose:
                    print(f"  ~ {rel}: pinned {kind} {recorded} "
                          f"({pin.relative_to(cfg.dropbox)})", flush=True)
        elif verbose:
            print(f"  ! {rel}: B resolves {kind} {rid!r} but the record says "
                  f"{recorded!r}; a re-scan/edit would re-run this document", flush=True)
    return pins


def _import_document(cfg, conn, bundle_dir: Path, md: dict, embed_client, verbose: bool = True) -> dict | None:
    """Import one manifest document into B's DB and regenerate its outputs.
    Returns a summary dict, or a {'skipped': reason} dict when the document
    cannot be imported."""
    now = time.time()
    rel = Path(md["relpath"])
    target = cfg.dropbox / rel
    if not target.exists():
        print(f"  ! {rel}: file missing in the bundle/archive; skipping", flush=True)
        return {"skipped": "file missing"}

    sha = sha256_of_dir(target) if target.is_dir() else sha256_of(target)
    if sha != md.get("sha256"):
        print(f"  ! {rel}: content differs from the bundle (sha mismatch); "
              f"skipping (--force to overwrite from the bundle)", flush=True)
        return {"skipped": "content differs"}

    stat = target.stat()
    pal = md.get("palaeographer")
    ed = md.get("editor")
    doc_id = db.add_document(
        conn, filename=md["filename"], path=str(target), sha256=sha,
        size_bytes=stat.st_size, mtime=stat.st_mtime, kind=md.get("kind"),
        now=now, dir_path=md.get("dir_path") or "", palaeographer=pal, editor=ed,
    )
    db.set_document_status(conn, doc_id, "processing",
                           prompt_source=md.get("prompt_source"))
    conn.commit()

    # --- pin model selections so B resolves the SAME palaeographer/editor as A
    #     (otherwise `pha scan`/`pha edit` would see a resolution change and
    #     re-extract/re-edit). Done EARLY so the final status stamp leaves the
    #     document's updated_at newer than the pin files (a newer file in the
    #     prompt-selection chain would itself trigger re-extraction). Only
    #     write when no selection file resolves; an existing different
    #     selection is a real conflict, reported below.
    _pin_selections(cfg, target, rel, md, verbose=verbose)

    blib_dir = bundle_dir / "library" / Path(md.get("dir_path") or "") / md["slug"]
    pal = pal or "default"

    # --- pages (raw transcriptions) from transcription-<pal>/ files
    tdir = blib_dir / f"transcription-{pal}"
    if not tdir.is_dir():
        legacy = [d for d in blib_dir.glob("transcription*") if d.is_dir()]
        if legacy:
            tdir = legacy[0]
    pages_ok = 0
    if tdir.is_dir():
        for f in sorted(tdir.glob("*.md")):
            parsed = _parse_library_file(f)
            if not parsed:
                continue
            fm, body = parsed
            pno = fm.get("page")
            if pno is None:
                continue
            source_name = fm.get("source_name")
            if not source_name and f.stem != f"page-{int(pno):03d}":
                source_name = f.stem
            page_id = db.add_page(conn, doc_id, int(pno), source_name=source_name)
            db.set_page_result(conn, page_id, raw_text=body)
            if fm.get("reviewed"):
                conn.execute("UPDATE pages SET reviewed_at = ? WHERE id = ?", (now, page_id))
            pages_ok += 1
    conn.commit()

    # --- edits (editor output) from edited-<editor>/ files
    editors: set[str] = set()
    if blib_dir.is_dir():
        for edir in sorted(blib_dir.glob("edited-*")):
            if not edir.is_dir():
                continue
            editor = edir.name[len("edited-"):]
            editors.add(editor)
            for f in sorted(edir.glob("*.md")):
                parsed = _parse_library_file(f)
                if not parsed:
                    continue
                fm, body = parsed
                pno = fm.get("page")
                if pno is None:
                    continue
                page = conn.execute(
                    "SELECT id, raw_text FROM pages WHERE document_id = ? AND page_no = ?",
                    (doc_id, int(pno)),
                ).fetchone()
                if not page:
                    continue
                db.set_page_edit(conn, page["id"], editor, text=body,
                                 raw_sha=_raw_sha(page["raw_text"] or ""))
                if fm.get("reviewed"):
                    conn.execute(
                        "UPDATE page_edits SET reviewed_at = ?, exported_at = ?, updated_at = ? "
                        "WHERE page_id = ? AND editor = ?",
                        (now, now, now, page["id"], editor),
                    )
    conn.commit()

    # --- structured records from records-<encoder>.json (carries created_at
    #     >= the pages' timestamps so `pha encode` sees them as current)
    encoders: set[str] = set()
    if blib_dir.is_dir():
        for rf in sorted(blib_dir.glob("records-*.json")):
            encoder = rf.name[len("records-"):-len(".json")]
            encoders.add(encoder)
            try:
                payload = json.loads(rf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                print(f"  ! {rel}: unreadable {rf.name}; skipping records", flush=True)
                continue
            for kind, items in (payload.get("records") or {}).items():
                for item in items or []:
                    # the records file groups by the record's own kind; the
                    # DB kind column prefers that, with the group key as fallback.
                    rec_kind = (item or {}).get("kind") or (item or {}).get("class") or kind
                    db.add_record(conn, doc_id, encoder, rec_kind,
                                  json.dumps(item, ensure_ascii=False))
    # an encoder that ran but found nothing still counts as "done": keep a
    # records file with zero rows so `pha encode` does not re-run it.
    if blib_dir.is_dir():
        for cf in blib_dir.glob("concatenated-*.md"):
            encoders.add(cf.name[len("concatenated-"):-len(".md")])
    conn.commit()

    # --- status: done only when every carried page arrived; anything less
    #     stays pending/processing so a later `pha scan` resumes it.
    expected = md.get("page_count") or pages_ok
    if pages_ok == 0:
        status = "pending" if md.get("status") == "done" else (md.get("status") or "pending")
    else:
        status = "done" if pages_ok >= expected else "processing"
    db.set_document_status(conn, doc_id, status, prompt_source=md.get("prompt_source"))
    conn.commit()

    # --- regenerate library files in B: front matter now carries B's ids and
    #     paths, and exported_at is stamped (so `pha status` won't report the
    #     freshly written files as pending corrections).
    write_document_pages(cfg, conn, doc_id)
    for editor in editors:
        write_edited_pages(cfg, conn, doc_id, editor)
    for encoder in encoders:
        write_records_file(cfg, conn, doc_id, encoder)

    # --- index for search: chunks + FTS, embeddings best-effort (a missing
    #     embedding endpoint degrades to text-only, like `pha reindex`).
    n = 0
    try:
        n = index_document(cfg, conn, doc_id, embed_client=embed_client, verbose=False)
    except Exception as e:  # noqa: BLE001 - an index failure must not abort the import
        print(f"  ! {rel}: indexing failed ({e}); run `pha reindex` later", flush=True)
    conn.commit()

    if verbose:
        print(f"  + {rel} -> #{doc_id} ({status}, {pages_ok} pages, {n} chunks)", flush=True)
    return {
        "id": doc_id,
        "relpath": str(rel),
        "status": status,
        "pages": pages_ok,
        "chunks": n,
        "editors": sorted(editors),
        "encoders": sorted(encoders),
    }


def import_bundle(cfg, bundle_dir: Path, force: bool = False, verbose: bool = True) -> dict:
    """Import a pha bundle into the current archive. Existing documents are
    skipped unless --force (which replaces them). No model calls for the
    scan/edit stages; embeddings are best-effort."""
    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"not a pha bundle (no manifest.json): {bundle_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != BUNDLE_FORMAT:
        raise ValueError(f"not a pha bundle (format {manifest.get('format')!r})")
    if manifest.get("version", 0) > BUNDLE_VERSION:
        raise ValueError(
            f"bundle version {manifest.get('version')} is newer than this pha "
            f"supports ({BUNDLE_VERSION}); update pha first"
        )

    if not _acquire_scan_lock(cfg):
        return {"action": "skipped",
                "reason": "another scan/edit job is running (one local model at a time)"}
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    embed_client = ModelClient(cfg.embed_base_url, timeout_s=cfg.embed_timeout_s)
    try:
        defs_installed = _install_defs(cfg, bundle_dir, verbose=verbose)
        copied, skipped_files = _copy_dropbox_payload(cfg, bundle_dir, force, verbose=verbose)

        imported: list[dict] = []
        skipped_docs: list[str] = []
        for md in manifest.get("documents", []):
            rel = md.get("relpath")
            if not rel:
                continue
            target = cfg.dropbox / rel
            existing = db.get_document_by_path(conn, str(target))
            if existing is not None:
                if force:
                    remove_library_artifact(cfg, existing)
                    db.delete_document(conn, existing["id"])
                    conn.commit()
                else:
                    skipped_docs.append(rel)
                    if verbose:
                        print(f"  ~ {rel}: already in this archive (--force to replace)", flush=True)
                    continue
            res = _import_document(cfg, conn, bundle_dir, md, embed_client, verbose=verbose)
            if res is None:
                continue
            if "skipped" in res:
                skipped_docs.append(rel)
            else:
                imported.append(res)
        return {
            "action": "imported",
            "imported": imported,
            "skipped_documents": skipped_docs,
            "files_copied": copied,
            "files_skipped": skipped_files,
            "defs_installed": defs_installed,
        }
    finally:
        embed_client.close()
        conn.close()
        _release_scan_lock(cfg)

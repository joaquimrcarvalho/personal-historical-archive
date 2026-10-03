"""Document lifecycle: remove (to bin or purge), restore, and move.

Why this module exists
----------------------
`pha rm` used to *unregister* a document: it deleted the library folder and the
DB row but left the source file in the dropbox, so the very next `pha scan`
silently re-added it. Removal is now explicit about the source file too, and
reversible by default:

  `pha rm <id|name>`            move the dropbox payload AND the library folder
                                into `<archive>/bin/<stamp>/`, then clear the
                                index (DB row + chunks + FTS + orphan renders)
  `pha rm --purge <id|name>`    the same, but delete the files outright
  `pha rm --keep-files <...>`   clear only the index; leave files in place
  `pha bin list|restore|purge`  inspect / undo / empty the bin

A bin batch is self-describing: `bin/<stamp>/manifest.json` records each
document's id, dropbox-relative paths and library folder, so `pha bin restore`
can put every file back where it came from. The DB row itself is *not* saved:
restoring files does not rebuild the index; the printed `pha scan` command does
(and that re-reads the pages, because a removed row cannot be resurrected from
the library markdown without a model).

`pha mv` moves an already-processed document to another dropbox directory and
rewrites the existing row IN PLACE. The alternative -- a plain `mv` in the
shell -- makes the next scan see an unregistered path, insert a NEW document,
and re-extract every page; here the id, pages, chunks, edits, records and
render cache all survive, and only `path`/`dir_path` (and a moved bibliography
sidecar) change.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import bibliography, db
from .config import Config
from .ingest import _doc_slug, remove_render_if_orphaned


BIN_KIND = "pha-bin"
BIN_VERSION = 1


class ManageError(RuntimeError):
    """A removal/restore/move was refused, or could not be completed."""


# --------------------------------------------------------------------------- paths

def document_rel_path(cfg: Config, doc) -> str:
    """The document's dropbox-relative posix path (its stable identity)."""
    from .addresses import document_rel_path as _rel

    return _rel(cfg, doc)


def library_rel_path(doc) -> str:
    """The document's library folder, relative to `library/`."""
    return (Path(doc["dir_path"] or "") / _doc_slug(doc)).as_posix()


def _rel_to_dropbox(cfg: Config, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(cfg.dropbox.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _sidecar_rel_paths(cfg: Config, doc) -> list[str]:
    """Existing bibliographic sidecars of a document, dropbox-relative."""
    out: list[str] = []
    for _fmt, p in bibliography.candidate_sidecars(doc):
        if not p.is_file():
            continue
        rel = _rel_to_dropbox(cfg, p)
        if rel:
            out.append(rel)
    return out


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _new_stamp(cfg: Config) -> str:
    base = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stamp = base
    n = 1
    while (cfg.bin / stamp).exists():
        n += 1
        stamp = f"{base}-{n}"
    return stamp


def _write_manifest(batch: Path, stamp: str, documents: list[dict]) -> Path:
    batch.mkdir(parents=True, exist_ok=True)
    data = {
        "kind": BIN_KIND,
        "version": BIN_VERSION,
        "batch": stamp,
        "removed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "documents": documents,
    }
    tmp = batch / "manifest.json.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    final = batch / "manifest.json"
    tmp.replace(final)
    return final


def _read_manifest(batch: Path) -> dict:
    try:
        return json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManageError(f"cannot read bin manifest in {batch}: {e}") from e


# --------------------------------------------------------------------------- lookup

def documents_matching(conn, target) -> list:
    """Resolve an id or a filename substring to the documents it names.

    A numeric target is always an id (a document *named* `1576` must be
    addressed by its id, or a substring can never reach it) -- the same rule
    `pha rm`/`pha mv` have always used.
    """
    t = str(target).strip()
    if not t:
        return []
    if t.isdigit():
        d = db.get_document(conn, int(t))
        return [d] if d else []
    return list(db.find_documents_by_filename(conn, t))


def pending_review_count(cfg: Config, conn, doc_id: int) -> int:
    """Library pages a human edited but that are not imported yet.

    Removal must never silently throw those away; callers use this to warn
    (`pha rm`) or to refuse (`pha rm --purge`). Best-effort: a document whose
    library is already gone simply reports zero.
    """
    try:
        from .ingest import pending_review_files

        return len(pending_review_files(cfg, conn, doc_id=doc_id))
    except Exception:
        return 0


def _refuse_processing(docs: list, force: bool) -> None:
    """Refuse to remove/move a document a live scan currently owns.

    `processing` can also be a stale state left by a killed run; `--force` is
    the escape hatch for the operator who knows that.
    """
    if force:
        return
    busy = [d for d in docs if d["status"] == "processing"]
    if busy:
        raise ManageError(
            f"#{busy[0]['id']} is processing (a scan owns it); wait for it to finish "
            f"or pass --force"
        )


def _leased_shas(cfg: Config) -> set[str]:
    """Content hashes currently out on a hand-over (never safe to move)."""
    try:
        from . import handoff

        out: set[str] = set()
        for lease in handoff.active_leases(cfg):
            out |= lease.shas()
        return out
    except Exception:
        return set()


# --------------------------------------------------------------------------- removal

def _move_many(pairs: list[tuple[Path, Path]]) -> None:
    """Move every (src, dst), rolling back already-done moves on failure.

    A removal must not half-happen: if the third of five moves fails, the two
    already moved are put back so the archive stays consistent with its DB rows.
    """
    done: list[tuple[Path, Path]] = []
    for src, dst in pairs:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            done.append((dst, src))
        except OSError as e:
            for back_dst, back_src in reversed(done):
                try:
                    back_src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(back_dst), str(back_src))
                except OSError:
                    pass
            raise ManageError(f"could not move {src} -> {dst}: {e}") from e


def _delete_path(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except OSError as e:
        raise ManageError(f"could not delete {path}: {e}") from e


def _payload_pairs(cfg: Config, doc, batch: Path) -> tuple[str, str, list[tuple[Path, Path]], dict]:
    """Move plan + manifest entry for one document's dropbox + library files."""
    rel = document_rel_path(cfg, doc)
    src = Path(doc["path"])
    lib_rel = library_rel_path(doc)
    lib = cfg.library / lib_rel

    pairs: list[tuple[Path, Path]] = []
    if src.exists():
        pairs.append((src, batch / "dropbox" / rel))
    # Sidecars are separate files for a single-file document, but live INSIDE
    # the source folder for a directory-of-images document (already moved with
    # it), so those must not be moved twice.
    sidecars = _sidecar_rel_paths(cfg, doc)
    for sc_rel in sidecars:
        p = cfg.dropbox / sc_rel
        if src.is_dir() and (p == src or src in p.parents):
            continue
        if p.exists():
            pairs.append((p, batch / "dropbox" / sc_rel))
    if lib.exists():
        pairs.append((lib, batch / "library" / lib_rel))

    entry = {
        "id": doc["id"],
        "filename": doc["filename"],
        "rel_path": rel,
        "sha256": doc["sha256"],
        "kind": doc["kind"],
        "dir_path": doc["dir_path"] or "",
        "library_rel": lib_rel if lib.exists() else None,
        "sidecars": sidecars,
        "source_present": src.exists(),
        "status": doc["status"],
        "page_count": doc["page_count"],
        "palaeographer": doc["palaeographer"],
        "editor": doc["editor"],
    }
    return rel, lib_rel, pairs, entry


def bin_documents(cfg: Config, conn, docs: list, *, dry_run: bool = False,
                  force: bool = False) -> dict:
    """Move documents' payload + library to `bin/<stamp>/` and clear their index.

    The dropbox source is MOVED, not copied: that is what stops the next scan
    from re-adding the document. The library folder travels too, so human
    corrections are preserved for `pha bin restore`. Renders are derived and
    regenerable, so they are dropped (unless another live document shares the
    content hash).
    """
    docs = list(docs)
    _refuse_processing(docs, force)
    stamp = _new_stamp(cfg)
    batch = cfg.bin / stamp
    entries: list[dict] = []
    all_pairs: list[tuple[Path, Path]] = []
    for doc in docs:
        _rel, _lib_rel, pairs, entry = _payload_pairs(cfg, doc, batch)
        entry["pending_reviews"] = pending_review_count(cfg, conn, doc["id"])
        entries.append(entry)
        all_pairs.extend(pairs)

    if not dry_run and all_pairs:
        _move_many(all_pairs)
    if not dry_run:
        _write_manifest(batch, stamp, entries)
        for doc in docs:
            db.delete_document(conn, doc["id"])
        conn.commit()
        for doc in docs:
            remove_render_if_orphaned(cfg, conn, doc["sha256"])

    return {
        "ok": True,
        "mode": "bin",
        "batch": stamp,
        "bin_dir": str(batch),
        "documents": entries,
        "files_moved": len(all_pairs),
        "dry_run": dry_run,
    }


def purge_documents(cfg: Config, conn, docs: list, *, dry_run: bool = False,
                    force: bool = False) -> dict:
    """Permanently delete documents' payload, library and index entries.

    Refuses while a document has unimported human corrections unless `force`,
    because those library files are the only copy of that work.
    """
    docs = list(docs)
    _refuse_processing(docs, force)
    entries: list[dict] = []
    for doc in docs:
        pending = pending_review_count(cfg, conn, doc["id"])
        if pending and not force:
            raise ManageError(
                f"#{doc['id']} has {pending} unimported correction(s) in its library "
                f"files; run `pha review` first, or pass --force to discard them"
            )
        rel = document_rel_path(cfg, doc)
        src = Path(doc["path"])
        lib_rel = library_rel_path(doc)
        lib = cfg.library / lib_rel
        paths: list[Path] = []
        if src.exists():
            paths.append(src)
        for sc_rel in _sidecar_rel_paths(cfg, doc):
            p = cfg.dropbox / sc_rel
            if src.is_dir() and (p == src or src in p.parents):
                continue
            if p.exists():
                paths.append(p)
        if lib.exists():
            paths.append(lib)
        entries.append({
            "id": doc["id"],
            "filename": doc["filename"],
            "rel_path": rel,
            "library_rel": lib_rel if lib.exists() else None,
            "paths": [_rel_to_dropbox(cfg, p) or str(p) for p in paths],
        })
        if not dry_run:
            for p in paths:
                _delete_path(p)
    if not dry_run:
        for doc in docs:
            db.delete_document(conn, doc["id"])
        conn.commit()
        for doc in docs:
            remove_render_if_orphaned(cfg, conn, doc["sha256"])
    return {
        "ok": True,
        "mode": "purge",
        "documents": entries,
        "dry_run": dry_run,
    }


def unregister_documents(cfg: Config, conn, docs: list, *, dry_run: bool = False,
                           force: bool = False) -> dict:
    """Clear the index and leave every file on disk (rescan re-adds it)."""
    docs = list(docs)
    _refuse_processing(docs, force)
    entries = [{
        "id": doc["id"],
        "filename": doc["filename"],
        "rel_path": document_rel_path(cfg, doc),
    } for doc in docs]
    if not dry_run:
        for doc in docs:
            db.delete_document(conn, doc["id"])
        conn.commit()
        for doc in docs:
            remove_render_if_orphaned(cfg, conn, doc["sha256"])
    return {"ok": True, "mode": "keep-files", "documents": entries, "dry_run": dry_run}


# --------------------------------------------------------------------------- the bin

def list_batches(cfg: Config) -> list[dict]:
    """Every bin batch, newest first, with its manifest and on-disk size."""
    out: list[dict] = []
    if not cfg.bin.is_dir():
        return out
    for manifest in sorted(cfg.bin.glob("*/manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["bin_dir"] = str(manifest.parent)
        data["bytes"] = _tree_bytes(manifest.parent)
        out.append(data)
    out.sort(key=lambda b: (b.get("removed_at") or "", b.get("batch") or ""), reverse=True)
    return out


def resolve_batch(cfg: Config, target) -> Path:
    """Find ONE bin batch by its stamp, or by a document id/filename inside it."""
    t = str(target).strip()
    batches = list_batches(cfg)
    if not batches:
        raise ManageError("the bin is empty")
    exact = [b for b in batches if b.get("batch") == t or Path(b["bin_dir"]).name == t]
    if len(exact) == 1:
        return Path(exact[0]["bin_dir"])
    hits = []
    for b in batches:
        names = " ".join(
            f"{d.get('id')} {d.get('filename')} {d.get('rel_path')}"
            for d in b.get("documents", [])
        )
        if t in names or t in (b.get("batch") or ""):
            hits.append(b)
    if not hits:
        raise ManageError(f"no binned batch matches {repr(target)}")
    if len(hits) > 1:
        listing = ", ".join(b.get("batch", "?") for b in hits)
        raise ManageError(f"{repr(target)} matches {len(hits)} batches ({listing}); use the batch id")
    return Path(hits[0]["bin_dir"])


def _rewrite_manifest(batch: Path, data: dict) -> None:
    tmp = batch / "manifest.json.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(batch / "manifest.json")


def restore_batch(cfg: Config, conn, batch: Path, *, doc_ids: list[int] | None = None,
                  dry_run: bool = False, force: bool = False) -> dict:
    """Move one batch's files back to their original dropbox/library locations.

    Restoring files does NOT rebuild the database: the row is gone, so the
    caller prints the `pha scan --path ...` command that re-reads the document.
    Destinations that now exist are refused unless `force` (and never when a
    registered document already owns them).
    """
    data = _read_manifest(batch)
    wanted = set(doc_ids) if doc_ids else None
    restore: list[dict] = []
    keep: list[dict] = []
    for d in data.get("documents", []):
        if wanted is not None and d.get("id") not in wanted:
            keep.append(d)
            continue
        pairs: list[tuple[Path, Path]] = []
        top = d.get("rel_path") or ""
        rels: list[str] = [top] if top else []
        for sc in list(d.get("sidecars") or []):
            # A directory document's sidecar lives INSIDE the folder that is
            # already being restored: adding it separately would plan a move of
            # a path the folder move has just carried away.
            if top and (sc == top or sc.startswith(top + "/")):
                continue
            rels.append(sc)
        for rel in rels:
            if not rel:
                continue
            s = batch / "dropbox" / rel
            if s.exists():
                pairs.append((s, cfg.dropbox / rel))
        if d.get("library_rel"):
            s = batch / "library" / d["library_rel"]
            if s.exists():
                pairs.append((s, cfg.library / d["library_rel"]))
        for _s, t in pairs:
            claimant = db.get_document_by_path(conn, str(t))
            if claimant is not None:
                raise ManageError(
                    f"refusing to restore over {t}: it is registered as document "
                    f"#{claimant['id']}"
                )
            if t.exists() and not force:
                raise ManageError(f"destination already exists: {t} (pass --force to overwrite)")
        if not dry_run:
            for s, t in pairs:
                t.parent.mkdir(parents=True, exist_ok=True)
                if t.exists():
                    _delete_path(t)
                shutil.move(str(s), str(t))
        restore.append(d)
    if not dry_run:
        if keep:
            data["documents"] = keep
            _rewrite_manifest(batch, data)
        else:
            shutil.rmtree(batch, ignore_errors=True)
    rescans = [f"pha scan --path {d.get('rel_path')}" for d in restore if d.get("rel_path")]
    return {
        "ok": True,
        "batch": data.get("batch"),
        "restored": restore,
        "remaining": keep,
        "rescan": rescans,
        "dry_run": dry_run,
    }


def purge_batch(cfg: Config, batch: Path, *, doc_ids: list[int] | None = None,
                dry_run: bool = False) -> dict:
    """Delete a batch (or only some documents in it) for good."""
    data = _read_manifest(batch)
    wanted = set(doc_ids) if doc_ids else None
    if wanted is None:
        if not dry_run:
            shutil.rmtree(batch, ignore_errors=True)
        return {"ok": True, "batch": data.get("batch"), "purged": data.get("documents", []),
                "dry_run": dry_run}
    purged, keep = [], []
    for d in data.get("documents", []):
        (purged if d.get("id") in wanted else keep).append(d)
    if not dry_run:
        for d in purged:
            for rel in [d.get("rel_path")] + list(d.get("sidecars") or []):
                if rel:
                    _delete_path(batch / "dropbox" / rel)
            if d.get("library_rel"):
                _delete_path(batch / "library" / d["library_rel"])
        if keep:
            data["documents"] = keep
            _rewrite_manifest(batch, data)
        else:
            shutil.rmtree(batch, ignore_errors=True)
    return {"ok": True, "batch": data.get("batch"), "purged": purged, "dry_run": dry_run}


# --------------------------------------------------------------------------- move

def resolve_dest_dir(cfg: Config, dest) -> Path:
    """Resolve a move destination to an existing-or-new dir inside the dropbox.

    A bare name resolves against the dropbox first, then `collections/` (so
    `pha mv 93 academic-works` finds `collections/academic-works`), matching how
    `--collection` resolves names elsewhere.
    """
    p = Path(str(dest)).expanduser()
    if not p.is_absolute():
        cand = cfg.dropbox / p
        bare = len(p.parts) == 1
        if bare and not cand.exists() and (cfg.dropbox / "collections" / p).exists():
            cand = cfg.dropbox / "collections" / p
        p = cand
    p = p.resolve()
    try:
        p.relative_to(cfg.dropbox.resolve())
    except ValueError:
        raise ManageError(
            f"destination must be inside the dropbox ({cfg.dropbox}): {dest}"
        ) from None
    if p.exists() and not p.is_dir():
        raise ManageError(f"destination is not a directory: {p}")
    return p


def move_document(cfg: Config, conn, doc, dest, *, dry_run: bool = False,
                  force: bool = False) -> dict:
    """Move one processed document to another dropbox directory, in place.

    Filesystem: the source (file or image-directory), its bibliographic sidecars
    and its library folder move together. Database: the SAME row is rewritten
    (id, pages, chunks, edits, records and the sha-keyed render cache survive),
    so a later scan sees "unchanged" instead of a new document. `updated_at` is
    deliberately preserved (a move is not a re-process).
    """
    old_path = Path(doc["path"])
    old_rel = document_rel_path(cfg, doc)
    if not old_path.exists():
        raise ManageError(f"source file is missing: {old_path}")

    dest_dir = resolve_dest_dir(cfg, dest)
    new_path = dest_dir / old_path.name
    new_rel = _rel_to_dropbox(cfg, new_path)
    if new_rel is None:
        raise ManageError(f"destination escapes the dropbox: {new_path}")
    root = cfg.dropbox.resolve()
    new_dir_path = "" if new_path.parent.resolve() == root else \
        new_path.parent.resolve().relative_to(root).as_posix()

    if new_rel == old_rel:
        return {"ok": True, "moved": False, "reason": "already in that directory",
                "document_id": doc["id"], "rel_path": old_rel, "dry_run": dry_run}

    if doc["status"] == "processing" and not force:
        raise ManageError(
            f"#{doc['id']} is processing (a scan owns it); wait for it to finish "
            f"or pass --force"
        )
    if doc["sha256"] in _leased_shas(cfg):
        raise ManageError(
            f"#{doc['id']} is out on a hand-over; run `pha handoff status` then "
            f"`pha handoff cancel` before moving it"
        )
    if old_path.is_dir() and (old_path.resolve() == new_path.resolve()
                              or old_path.resolve() in new_path.resolve().parents):
        raise ManageError("cannot move a directory document inside itself")
    claimant = db.get_document_by_path(conn, str(new_path))
    if claimant is not None and claimant["id"] != doc["id"]:
        raise ManageError(
            f"another document (#{claimant['id']}) is already registered at {new_path}"
        )
    if new_path.exists() and not force:
        raise ManageError(f"destination already exists: {new_path} (pass --force to overwrite)")

    # Plan the moves. A directory document's sidecar lives inside it, so it is
    # covered by the directory move; a single-file document's sidecars move
    # beside it.
    pairs: list[tuple[Path, Path]] = [(old_path, new_path)]
    for _fmt, sc in bibliography.candidate_sidecars(doc):
        if not sc.is_file():
            continue
        if old_path.is_dir() and old_path.resolve() in sc.resolve().parents:
            continue
        pairs.append((sc, new_path.parent / sc.name))

    old_lib_rel = library_rel_path(doc)
    old_lib = cfg.library / old_lib_rel
    new_lib_rel = (PurePosixPath(new_dir_path) / _doc_slug(doc)).as_posix() if new_dir_path \
        else _doc_slug(doc)
    new_lib = cfg.library / new_lib_rel
    if old_lib.exists():
        pairs.append((old_lib, new_lib))

    for _s, d in pairs:
        if d.exists() and d.resolve() != _s.resolve():
            if not force:
                raise ManageError(
                    f"destination already exists: {d} (pass --force to overwrite)"
                )
            if not dry_run:
                _delete_path(d)

    if not dry_run:
        _move_many(pairs)
        db.move_document_path(conn, doc["id"], path=str(new_path),
                              dir_path=new_dir_path, filename=new_path.name)
        bib = db.get_bibliography(conn, doc["id"])
        if bib is not None and bib["sidecar_path"]:
            stored_name = Path(bib["sidecar_path"]).name
            new_doc = dict(doc)
            new_doc["path"] = str(new_path)
            for _fmt, cand in bibliography.candidate_sidecars(new_doc):
                if cand.name == stored_name:
                    db.set_bibliography_sidecar_path(conn, doc["id"], str(cand))
                    break
        conn.commit()

    from .addresses import doc_slug

    warn = None
    try:
        from .sidecar import resolve_stages

        res = resolve_stages(cfg, new_path.parent, stem=new_path.stem)
        live_pal = res["palaeographer"]["id"]
        live_ed = res["editor"]["id"]
        if (doc["palaeographer"] or None) != (live_pal or None):
            warn = (f"this directory resolves to palaeographer {repr(live_pal)}, but the "
                    f"document was read with {repr(doc['palaeographer'])}; the next scan "
                    f"will re-read every page")
        elif (doc["editor"] or None) != (live_ed or None):
            warn = (f"this directory resolves to editor {repr(live_ed)}, but the document "
                    f"was edited with {repr(doc['editor'])}; the next edit pass will redo it")
    except Exception:
        pass

    return {
        "ok": True,
        "moved": True,
        "document_id": doc["id"],
        "from_rel": old_rel,
        "to_rel": new_rel,
        "dest_dir": str(dest_dir),
        "library_from": old_lib_rel,
        "library_to": new_lib_rel,
        "slug_from": doc_slug(old_rel),
        "slug_to": doc_slug(new_rel),
        "files_moved": len(pairs),
        "warning": warn,
        "dry_run": dry_run,
    }

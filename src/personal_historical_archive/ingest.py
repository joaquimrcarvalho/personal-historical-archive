from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import yaml

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from .config import Config, Editor, Encoder, Palaeographer
from .embed import pack, prefixed
from .extract import (
    build_page_prompt,
    compose_prompts,
    editor_candidates,
    encoder_candidates,
    format_notes,
    is_supported,
    page_count,
    palaeographer_candidates,
    prompt_candidates,
    render_document,
    resolve_editor_id,
    resolve_encoder_id,
    resolve_palaeographer_id,
    resolve_prompt,
)
from .model_client import ModelClient, ModelError


def make_vision_client(
    cfg: Config, pal_id: str | None = None
) -> tuple[ModelClient, Palaeographer]:
    """Create a ModelClient for a palaeographer (or the active one)."""
    pal = cfg.get_palaeographer(pal_id)
    return ModelClient(pal.base_url, timeout_s=pal.timeout_s, api_key=pal.api_key), pal


def make_editor_client(cfg: Config, editor_id: str) -> tuple[ModelClient, Editor]:
    """Create a ModelClient for an editor (a text model, possibly on a
    different endpoint/model than the palaeographer)."""
    editor = cfg.get_editor(editor_id)
    return ModelClient(editor.base_url, timeout_s=editor.timeout_s, api_key=editor.api_key), editor


def _raw_sha(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode()).hexdigest()


# --------------------------------------------------------------------------- scan lock

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) would TERMINATE the process on Windows; probe
        # existence instead (OpenProcess with query access, stdlib only).
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False


def _scan_lock_path(cfg: Config) -> Path:
    return cfg.data / "scan.lock"


def _scan_lock_held_by_other(cfg: Config) -> bool:
    """True if a DIFFERENT, still-running process holds the scan lock."""
    lock = _scan_lock_path(cfg)
    if not lock.exists():
        return False
    try:
        pid = int(lock.read_text().strip() or "0")
    except (ValueError, OSError):
        return False
    return pid != os.getpid() and _pid_alive(pid)


def _acquire_scan_lock(cfg: Config) -> bool:
    """Take the scan lock. Returns False if another live scan is running."""
    lock = _scan_lock_path(cfg)
    try:
        if lock.exists():
            # A lock older than a scan could plausibly last (6 h) is stale
            # even if its PID looks alive — PIDs get reused and a stale lock
            # must never wedge every future scan.
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0
            if age > 6 * 3600:
                lock.unlink()
            else:
                try:
                    pid = int(lock.read_text().strip() or "0")
                except (ValueError, OSError):
                    pid = 0
                if pid != os.getpid() and _pid_alive(pid):
                    return False
                lock.unlink()  # stale lock
        lock.write_text(str(os.getpid()))
        return True
    except OSError:
        return True  # cannot lock; proceed (best effort)


def _release_scan_lock(cfg: Config) -> None:
    lock = _scan_lock_path(cfg)
    try:
        if lock.exists() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink()
    except OSError:
        pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_of_dir(path: Path) -> str:
    """Content hash of an image-directory document (names + file hashes)."""
    h = hashlib.sha256()
    for f in sorted(path.iterdir()):
        if f.is_file() and is_supported(f.name) and not f.name.startswith("."):
            h.update(f.name.encode())
            h.update(sha256_of(f).encode())
    return h.hexdigest()


def _is_document_dir(path: Path) -> bool:
    """A directory of images (and no subdirectories/PDFs) is ONE document:
    each image is a page, scanned with the directory's prompt."""
    entries = list(path.iterdir())
    has_images = any(e.is_file() and is_supported(e.name) and not e.name.startswith(".") for e in entries)
    if not has_images:
        return False
    for e in entries:
        if e.is_dir():
            return False
        if e.is_file() and e.suffix.lower() == ".pdf":
            return False
    return True


def discover(dropbox: Path, dir_documents: bool = True) -> list[Path]:
    """List document units: individual files plus image-directory documents."""
    units: list[Path] = []
    for p in sorted(dropbox.rglob("*")):
        if not p.is_file() or not is_supported(p.name) or p.name.startswith("."):
            continue
        if dir_documents and p.parent != dropbox and _is_document_dir(p.parent):
            continue  # this file is a page of a document-directory
        units.append(p)
    if dir_documents:
        for d in sorted(dropbox.rglob("*")):
            if d.is_dir() and d != dropbox and _is_document_dir(d):
                units.append(d)
    return sorted(units, key=lambda p: str(p))


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end]
        if end < len(text):
            cut = chunk.rfind(" ")
            if cut > size * 0.6:
                chunk = chunk[:cut]
                end = start + cut
        chunks.append(chunk)
        if end >= len(text):
            break  # last chunk reached; stop (overlap must not rewind past the end)
        nxt = end - overlap
        start = nxt if nxt > start else start + 1
    return chunks


def _prompt_newer_than(
    path: Path,
    cfg: Config,
    ts: float,
    palaeographer: Palaeographer | None = None,
) -> bool:
    file_dir = path if path.is_dir() else path.parent
    for cand in prompt_candidates(path.stem, file_dir, cfg.dropbox, cfg.prompts):
        if cand.exists() and cand.stat().st_mtime > ts:
            return True
    default = cfg.prompts / "default_prompt.md"
    if default.exists() and default.stat().st_mtime > ts:
        return True
    if palaeographer and palaeographer.prompt_file:
        try:
            if palaeographer.prompt_file.stat().st_mtime > ts:
                return True
        except OSError:
            pass
    for cand in palaeographer_candidates(path.stem, file_dir, cfg.dropbox):
        if cand.exists() and cand.stat().st_mtime > ts:
            return True
    return False


def remove_library_artifact(cfg: Config, doc) -> None:
    """Delete the document's library folder (all palaeographer transcriptions)."""
    if not doc:
        return
    slug = f"{Path(doc['path']).stem}__{doc['sha256'][:8]}"
    rel = Path(doc["dir_path"] or "")
    d = cfg.library / rel / slug
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def ingest_file(
    cfg: Config,
    conn,
    client: ModelClient,
    path: Path,
    palaeographer: Palaeographer,
    explicit_prompt: str | None = None,
    reprocess: bool = False,
    verbose: bool = True,
) -> dict:
    path = Path(path)
    if path.is_dir():
        sha = sha256_of_dir(path)
        kind = "dir"
    else:
        sha = sha256_of(path)
        kind = "pdf" if path.suffix.lower() == ".pdf" else "image"
    stat = path.stat()
    mtime, size = stat.st_mtime, stat.st_size
    now = time.time()

    existing = db.get_document_by_path(conn, str(path))
    reuse = False
    prompt_changed = False
    if existing and existing["sha256"] == sha:
        # Same file version. Reuse the document row so parallel outputs survive:
        # previous palaeographer transcriptions, editor outputs and records
        # stay in the library folder and DB (staleness marks them for
        # regeneration, never deletion). --reprocess forces re-extraction of
        # every page but must NOT delete the document.
        if reprocess:
            reuse = True
            prompt_changed = True  # re-extract ALL pages
        else:
            prompt_newer = _prompt_newer_than(path, cfg, existing["updated_at"], palaeographer)
            # The document re-extracts if it now resolves to a DIFFERENT palaeographer
            # than the one that produced its current text (NULL = unknown/legacy).
            pal_changed = (
                existing["palaeographer"] is not None
                and existing["palaeographer"] != palaeographer.id
            )
            changed = prompt_newer or pal_changed
            if existing["status"] == "processing":
                # Only skip if ANOTHER live scan owns this document right now;
                # a stale 'processing' (killed by sleep/crash/reboot) is resumed.
                if _scan_lock_held_by_other(cfg) and time.time() - existing["updated_at"] < 600:
                    return {"action": "skipped", "filename": path.name, "reason": "already processing"}
                reuse = True  # resume (keep done pages)
                prompt_changed = changed  # prompt/palaeographer edited mid-run
            elif existing["status"] == "done":
                if not changed:
                    return {"action": "skipped", "filename": path.name, "reason": "unchanged"}
                reuse = True
                prompt_changed = True  # prompt/palaeographer changed -> re-extract ALL pages
            else:  # 'error': previous run failed -> resume (keep done pages, retry the rest)
                reuse = True
                prompt_changed = changed
    elif existing:
        # File content changed (new sha) -> the document is a NEW version.
        # The old library folder is keyed by the old sha, so it is left on
        # disk untouched; only the stale DB row is replaced.
        db.delete_document(conn, existing["id"])
        conn.commit()

    try:
        rel_dir = str(path.parent.relative_to(cfg.dropbox))
    except ValueError:
        rel_dir = ""
    if rel_dir == ".":
        rel_dir = ""
    ed_id, _edsrc = resolve_editor_id(
        path.stem, path if path.is_dir() else path.parent, cfg.dropbox
    )
    doc_id = existing["id"] if reuse else db.add_document(
        conn, filename=path.name, path=str(path), sha256=sha,
        size_bytes=size, mtime=mtime, kind=kind, now=now, dir_path=rel_dir,
        palaeographer=palaeographer.id, editor=ed_id,
    )
    if reuse:
        db.update_document(conn, doc_id, palaeographer=palaeographer.id, editor=ed_id)
    db.set_document_status(conn, doc_id, "processing")
    conn.commit()

    prompt, prompt_source = resolve_prompt(
        path.stem,
        path if path.is_dir() else path.parent,
        cfg.dropbox, cfg.prompts, explicit_prompt,
    )
    prompt = compose_prompts(palaeographer.prompt_text, prompt)
    force = reprocess or prompt_changed  # only these re-extract already-done pages
    db.update_document(conn, doc_id, prompt_source=prompt_source)
    conn.commit()
    write_document_pages(cfg, conn, doc_id)  # visible output even while processing

    try:
        if path.is_dir():
            images = [f for f in sorted(path.iterdir())
                      if f.is_file() and is_supported(f.name) and not f.name.startswith(".")]
            total = len(images)
            renders: list[Path] = []
            for img in images:
                renders += render_document(img, cfg.renders / sha, cfg.render_dpi, cfg.max_image_px, cfg.jpeg_quality)
        else:
            total = page_count(path)
            renders = render_document(path, cfg.renders / sha, cfg.render_dpi, cfg.max_image_px, cfg.jpeg_quality)
    except Exception as e:
        db.set_document_status(conn, doc_id, "error", error=f"render failed: {e}")
        conn.commit()
        return {"action": "error", "filename": path.name, "error": f"render failed: {e}"}
    db.update_document(conn, doc_id, page_count=total)

    page_errors: list[tuple[int, str]] = []
    consecutive_failures = 0
    for i, img in enumerate(renders, start=1):
        page_id = db.add_page(conn, doc_id, i)
        page = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if page["status"] == "done" and not force:
            continue  # resume: keep already-extracted pages
        prompt_txt = build_page_prompt(prompt, path.name, i, total)
        if verbose:
            print(f"  page {i}/{total}: extracting ...", flush=True)
        try:
            text = client.chat_vision(
                palaeographer.model, prompt_txt, img,
                palaeographer.temperature, palaeographer.max_tokens,
                thinking=palaeographer.thinking,
            )
            db.set_page_result(conn, page_id, raw_text=text)
            consecutive_failures = 0
        except ModelError as e:
            db.set_page_result(conn, page_id, error=str(e))
            page_errors.append((i, str(e)))
            consecutive_failures += 1
            if consecutive_failures >= 5:
                # systemic failure (server down, model not loaded, ...): stop
                # instead of burning through the whole document
                db.set_document_status(
                    conn, doc_id, "error",
                    error=f"aborted after {consecutive_failures} consecutive page failures: {e}",
                )
                conn.commit()
                return {"action": "error", "filename": path.name, "error": str(e)}
        db.touch_document(conn, doc_id)
        conn.commit()
        write_document_pages(cfg, conn, doc_id)  # grow the artifact page by page
    if page_errors:
        db.set_document_status(
            conn, doc_id, "error",
            error=f"{len(page_errors)} page(s) failed; first error: {page_errors[0][1]}",
        )
        conn.commit()
        return {"action": "error", "filename": path.name, "error": page_errors[0][1]}

    db.set_document_status(conn, doc_id, "done", prompt_source=prompt_source)
    conn.commit()
    edit_document(cfg, conn, doc_id, verbose=verbose)  # editor pass (skips if none configured)
    index_document(cfg, conn, doc_id, verbose=verbose)  # indexes raw + edited variants
    write_document_pages(cfg, conn, doc_id)
    return {"action": "ingested", "filename": path.name, "pages": total, "prompt": prompt_source}


def index_document(
    cfg: Config, conn, doc_id: int, embed_client: ModelClient | None = None, verbose: bool = True
) -> int:
    """Index BOTH variants when an editor is configured: the raw transcription
    (variant='raw') and the editor's output (variant='edited'), so searches
    hit either the faithful or the modernized/translated text."""
    pages = db.get_pages(conn, doc_id)
    db.clear_chunks(conn, doc_id)
    doc = db.get_document(conn, doc_id)
    edited: dict[int, str] = {}
    if doc and doc["editor"]:
        for e in db.edits_for_document(conn, doc_id, doc["editor"]):
            if e["status"] == "done" and e["text"]:
                edited[e["page_id"]] = e["text"]
    items: list[tuple[int, int, str, str]] = []  # (page_id, chunk_no, text, variant)
    n = 0
    for p in pages:
        for ch in chunk_text(p["raw_text"], cfg.chunk_chars, cfg.chunk_overlap):
            items.append((p["id"], n, ch, "raw"))
            n += 1
        if p["id"] in edited:
            for ch in chunk_text(edited[p["id"]], cfg.chunk_chars, cfg.chunk_overlap):
                items.append((p["id"], n, ch, "edited"))
                n += 1
    if not items:
        conn.commit()
        return 0
    if verbose:
        print(f"  indexing {n} chunks ...", flush=True)
    close_embed = False
    if embed_client is None:
        embed_client = ModelClient(cfg.embed_base_url, timeout_s=cfg.embed_timeout_s)
        close_embed = True
    try:
        vecs = embed_client.embed(
            cfg.embed_model,
            [prefixed(cfg.embed_model, t, "doc") for _, _, t, _v in items],
        )
    except ModelError as e:
        vecs = [None] * len(items)
        if verbose:
            print(f"  warning: embeddings unavailable ({e}); indexing text-only")
    finally:
        if close_embed:
            embed_client.close()
    for (page_id, chunk_no, text, variant), v in zip(items, vecs):
        db.add_chunk(conn, doc_id, page_id, chunk_no, text, pack(v) if v else None, variant)
    conn.commit()
    return n


def write_document_pages(cfg: Config, conn, doc_id: int) -> Path | None:
    """Write per-page transcription files with repeated front matter, grouped
    by palaeographer at the document level:

        library/<rel_dir>/<slug>/transcription-<pal>/page-NNN.md
    """
    doc = db.get_document(conn, doc_id)
    pages = db.get_pages(conn, doc_id)
    if not doc:
        return None
    pal = doc["palaeographer"] or "default"
    slug = f"{Path(doc['path']).stem}__{doc['sha256'][:8]}"
    rel_dir = Path(doc["dir_path"] or "")
    out_dir = cfg.library / rel_dir / slug / f"transcription-{pal}"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "source": doc["path"],
        "filename": doc["filename"],
        "collection": doc["dir_path"] or "(root)",
        "document_id": doc["id"],
        "pages_total": doc["page_count"],
        "palaeographer": pal,
        "prompt": doc["prompt_source"],
    }
    for p in pages:
        fm = dict(base)
        fm["page"] = p["page_no"]
        fm["status"] = "done" if p["status"] == "done" else "waiting"
        body = (p["raw_text"] or "").strip()
        body = format_notes(body) if body else "*waiting*"
        text = (
            "---\n"
            + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=100000).strip()
            + "\n---\n\n"
            + body
            + "\n"
        )
        (out_dir / f"page-{p['page_no']:03d}.md").write_text(text)
    return out_dir


# --------------------------------------------------------------------------- editors

def _edit_needed(page, edit_row, editor: Editor, reprocess: bool) -> bool:
    """Does this page need (re-)editing?"""
    if reprocess:
        return True
    if edit_row is None or edit_row["status"] != "done" or not edit_row["text"]:
        return True
    if edit_row["raw_sha"] != _raw_sha(page["raw_text"]):
        return True  # page was re-transcribed since the edit
    if editor.prompt_file:
        try:
            if editor.prompt_file.stat().st_mtime > (edit_row["updated_at"] or 0):
                return True  # the editor's prompt changed
        except OSError:
            pass
    return False


def write_edited_pages(cfg: Config, conn, doc_id: int, editor_id: str) -> Path | None:
    """Write the editor's per-page output to library/.../edited-<editor>/."""
    doc = db.get_document(conn, doc_id)
    if not doc:
        return None
    slug = f"{Path(doc['path']).stem}__{doc['sha256'][:8]}"
    rel_dir = Path(doc["dir_path"] or "")
    out_dir = cfg.library / rel_dir / slug / f"edited-{editor_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "source": doc["path"],
        "filename": doc["filename"],
        "collection": doc["dir_path"] or "(root)",
        "document_id": doc["id"],
        "pages_total": doc["page_count"],
        "editor": editor_id,
    }
    for e in db.edits_for_document(conn, doc_id, editor_id):
        p = conn.execute("SELECT page_no FROM pages WHERE id = ?", (e["page_id"],)).fetchone()
        if not p:
            continue
        fm = dict(base)
        fm["page"] = p["page_no"]
        fm["status"] = "done" if e["status"] == "done" else "waiting"
        body = (e["text"] or "*waiting*").strip()
        text = (
            "---\n"
            + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=100000).strip()
            + "\n---\n\n"
            + body
            + "\n"
        )
        (out_dir / f"page-{p['page_no']:03d}.md").write_text(text)
    return out_dir


def _edit_null(cfg: Config, conn, doc_id: int, resolved: str,
               reprocess: bool, verbose: bool) -> dict:
    """Null/passthrough editor: copy each page's transcription verbatim as
    the 'edited' text (no model call). Produces edited-<resolved>/ pages and
    records the editor on the document for provenance."""
    doc = db.get_document(conn, doc_id)
    pages = db.get_pages(conn, doc_id)
    edited = 0
    for p in pages:
        raw = (p["raw_text"] or "").strip()
        if not raw:
            continue
        row = db.get_page_edit(conn, p["id"], resolved)
        if not reprocess and row is not None and row["status"] == "done" and row["text"] == raw:
            continue
        db.set_page_edit(conn, p["id"], resolved, text=raw, raw_sha=_raw_sha(raw))
        edited += 1
        conn.commit()
        write_edited_pages(cfg, conn, doc_id, resolved)
    if doc["editor"] != resolved:
        db.update_document(conn, doc_id, editor=resolved)
        conn.commit()
    write_edited_pages(cfg, conn, doc_id, resolved)
    return {"action": "edited", "filename": doc["filename"], "editor": resolved, "pages": edited}


def edit_document(
    cfg: Config,
    conn,
    doc_id: int,
    editor_id: str | None = None,
    reprocess: bool = False,
    verbose: bool = True,
) -> dict:
    """Run the editor pass over a document's transcription pages. The editor is
    a DIFFERENT (text) model than the palaeographer; it transforms each page's
    transcription with its editing prompt (modernize, translate, ...).

    The special editor id 'null' (or 'passthrough') keeps the transcription
    verbatim: it copies each page's raw text as the 'edited' text without a
    model call, so documents without a real editor still flow through the
    same pipeline (edited-<editor>/ folder, both-variant indexing, encoder
    input) with explicit provenance."""
    doc = db.get_document(conn, doc_id)
    if not doc:
        return {"action": "skipped", "filename": "?", "reason": "no document"}
    if editor_id:
        resolved = editor_id
    else:
        path = Path(doc["path"])
        ed_id, _src = resolve_editor_id(
            path.stem, path if path.is_dir() else path.parent, cfg.dropbox
        )
        resolved = ed_id or (doc["editor"] if doc["editor"] in cfg.editors else None)
    if not resolved:
        return {"action": "skipped", "filename": doc["filename"], "reason": "no editor configured"}
    if resolved in ("null", "passthrough"):
        return _edit_null(cfg, conn, doc_id, resolved, reprocess, verbose)
    editor = cfg.get_editor(resolved)
    pages = db.get_pages(conn, doc_id)
    client, _ = make_editor_client(cfg, resolved)
    edited = 0
    try:
        for p in pages:
            raw = (p["raw_text"] or "").strip()
            if not raw:
                continue
            edit_row = db.get_page_edit(conn, p["id"], resolved)
            if not _edit_needed(p, edit_row, editor, reprocess):
                continue
            if verbose:
                print(f"  editing page {p['page_no']}/{doc['page_count']} ...", flush=True)
            prompt = (
                f"{editor.prompt_text}\n\n"
                f"Document: {doc['filename']}\nPage: {p['page_no']} of {doc['page_count']}\n\n"
                f"Transcription to edit:\n{raw}"
            )
            try:
                out = client.chat_text(editor.model, prompt, editor.temperature, editor.max_tokens,
                                       thinking=editor.thinking)
                db.set_page_edit(conn, p["id"], resolved, text=out, raw_sha=_raw_sha(raw))
                edited += 1
            except ModelError as e:
                db.set_page_edit(conn, p["id"], resolved, error=str(e), raw_sha=_raw_sha(raw))
            conn.commit()
            write_edited_pages(cfg, conn, doc_id, resolved)  # grow output page by page
    finally:
        client.close()
    if doc["editor"] != resolved:
        db.update_document(conn, doc_id, editor=resolved)
        conn.commit()
    write_edited_pages(cfg, conn, doc_id, resolved)
    return {"action": "edited", "filename": doc["filename"], "editor": resolved, "pages": edited}


def edit_all(cfg: Config, reprocess: bool = False, verbose: bool = True) -> dict:
    """Run the editor pass for every document that has an editor configured."""
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    try:
        results = []
        for d in db.list_documents(conn, limit=10000):
            results.append(edit_document(cfg, conn, d["id"], reprocess=reprocess, verbose=verbose))
        return {"results": results}
    finally:
        conn.close()


# --------------------------------------------------------------------------- encoders (structured records)

def make_encoder_client(cfg: Config, encoder_id: str) -> tuple[ModelClient, Encoder]:
    encoder = cfg.get_encoder(encoder_id)
    return ModelClient(encoder.base_url, timeout_s=encoder.timeout_s, api_key=encoder.api_key), encoder


def _parse_json_array(text: str) -> list:
    """Extract the first balanced JSON array from a model response. Also
    accepts a JSON object wrapping an array under a list-valued key
    (e.g. {"records": [...]})."""
    if not text:
        return []
    stripped = text.strip()
    if stripped.startswith("["):
        start = 0
    else:
        start = text.find("[")
        if start == -1:
            # object wrapper: {"records": [...]} or {"letters": [...]}
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    for v in obj.values():
                        if isinstance(v, list):
                            return v
            except json.JSONDecodeError:
                pass
            return []
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return []
    return []


def _encode_needed(cfg: Config, conn, doc_id: int, encoder: Encoder, resolved: str,
                   doc_path: Path, reprocess: bool) -> bool:
    """Re-encode when no records yet, or the encoder file / the document's
    encoder.prompt.md / encoder-prompt-langextract.md / the source
    transcription changed since the records were created."""
    if reprocess:
        return True
    row = conn.execute(
        "SELECT MAX(created_at) AS m FROM records WHERE document_id = ? AND encoder = ?",
        (doc_id, resolved),
    ).fetchone()
    if not row or not row["m"]:
        return True
    latest = row["m"]
    candidates = []
    if encoder.prompt_file:
        candidates.append(encoder.prompt_file)
    for kind in ("encoder.prompt", "encoder.prompt.langextract"):
        prompt, src = resolve_prompt(
            doc_path.stem, doc_path if doc_path.is_dir() else doc_path.parent,
            cfg.dropbox, cfg.prompts, kind=kind,
        )
        if src and src != "none" and not src.startswith("builtin"):
            candidates.append(Path(src))
    for cand in encoder_candidates(doc_path.stem, doc_path if doc_path.is_dir() else doc_path.parent, cfg.dropbox):
        if cand.exists():
            candidates.append(cand)
    for cand in candidates:
        try:
            if cand.stat().st_mtime > latest:
                return True
        except OSError:
            pass
    # the input text changed (new/updated page edits or raw transcriptions)
    t = conn.execute(
        "SELECT MAX(e.updated_at) AS m FROM page_edits e JOIN pages p ON p.id = e.page_id "
        "WHERE p.document_id = ? AND e.status = 'done'",
        (doc_id,),
    ).fetchone()["m"]
    if t and t > latest:
        return True
    t2 = conn.execute(
        "SELECT MAX(updated_at) AS m FROM pages WHERE document_id = ? AND status = 'done'",
        (doc_id,),
    ).fetchone()["m"]
    if t2 and t2 > latest:
        return True
    return False


def _expand_records(parsed: list) -> list[dict]:
    """Expand LangExtract-flat items into one record per class.

    An item like
      {"person": "Padre Mestre S. Francisco Xavier",
       "person_attributes": {"title": "Padre Mestre S.", "name": "Francisco Xavier"},
       "letter": "0 Padre Mestre S. Francisco Xavier ao ...",
       "letter_attributes": {"date": "1545-01-27", "place": "Cochim", ...}}
    becomes two records:
      {"kind": "person", "class": "person", "text": "...", "title": ..., "name": ...}
      {"kind": "letter", "class": "letter", "text": "...", "date": ..., ...}
    Plain records (no '<class>_attributes' keys) pass through unchanged."""
    out: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        attr_keys = [k for k in item if isinstance(k, str) and k.endswith("_attributes")]
        if not attr_keys:
            out.append(item)
            continue
        for k, v in item.items():
            if not isinstance(k, str) or k.endswith("_attributes") or k.endswith("_index"):
                continue
            if not isinstance(v, (str, int, float)):
                continue
            rec = {"kind": k, "class": k, "text": str(v)}
            attrs = item.get(k + "_attributes")
            if isinstance(attrs, dict):
                rec.update(attrs)
            out.append(rec)
    return out


def _record_key(rec: dict) -> tuple:
    """Dedupe key: kind + normalized text/from/to/date/place so the same
    extraction found in two overlapping chunks or passes collapses."""
    parts = [str(rec.get("kind") or "record")]
    for k in ("text", "from", "to", "date", "place"):
        v = rec.get(k)
        if isinstance(v, str):
            v = re.sub(r"\s+", " ", v.strip().lower())
        parts.append(str(v or ""))
    return tuple(parts)


def _record_similar(a: dict, b: dict) -> float:
    """Fuzzy similarity of two records of the SAME kind (LangExtract-style
    fuzzy alignment): ratio over all string/scalar fields. Different kinds
    are never similar (a letter and a person never collapse)."""
    if (a.get("kind") or "record") != (b.get("kind") or "record"):
        return 0.0
    import difflib

    def flat(rec: dict) -> str:
        vals = []
        for k, v in rec.items():
            if k in ("kind", "class", "page"):
                continue
            if isinstance(v, (str, int, float)):
                vals.append(re.sub(r"\s+", " ", str(v)).strip().lower())
        return " | ".join(vals)

    return difflib.SequenceMatcher(None, flat(a), flat(b)).ratio()


def write_concatenated_file(cfg: Config, conn, doc_id: int, texts: list,
                            encoder_id: str) -> Path | None:
    """Write the concatenated document text the encoder consumed
    (edited-preferred, '--- page N ---' markers) next to the records file."""
    doc = db.get_document(conn, doc_id)
    if not doc:
        return None
    slug = f"{Path(doc['path']).stem}__{doc['sha256'][:8]}"
    rel_dir = Path(doc["dir_path"] or "")
    out_dir = cfg.library / rel_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    block = "\n\n".join(f"--- page {pno} ---\n{t}" for pno, t in texts)
    out = out_dir / f"concatenated-{encoder_id}.md"
    out.write_text(block, encoding="utf-8")
    return out


def write_records_file(cfg: Config, conn, doc_id: int, encoder_id: str) -> Path | None:
    doc = db.get_document(conn, doc_id)
    if not doc:
        return None
    slug = f"{Path(doc['path']).stem}__{doc['sha256'][:8]}"
    rel_dir = Path(doc["dir_path"] or "")
    out_dir = cfg.library / rel_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r for r in db.records_for_document(conn, doc_id)
            if r["encoder"] == encoder_id]
    records = [json.loads(r["data"]) for r in rows]
    by_kind: dict[str, list] = {}
    for rec in records:
        by_kind.setdefault(rec.get("kind") or "record", []).append(rec)
    payload = {
        "document": doc["path"],
        "encoder": encoder_id,
        "records": by_kind,
    }
    out = out_dir / f"records-{encoder_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return out


def encode_document(
    cfg: Config,
    conn,
    doc_id: int,
    encoder_id: str | None = None,
    reprocess: bool = False,
    verbose: bool = True,
) -> dict:
    """Run the encoder over a document's transcription (edited text when the
    document has an editor, else raw), producing structured records in
    batches of pages. The encoder prompt is composed as:
    [encoder base prompt] + [document/collection encoder.prompt.md]."""
    doc = db.get_document(conn, doc_id)
    if not doc:
        return {"action": "skipped", "filename": "?", "reason": "no document"}
    doc_path = Path(doc["path"])
    if encoder_id:
        resolved = encoder_id
    else:
        enc_id, _src = resolve_encoder_id(
            doc_path.stem, doc_path if doc_path.is_dir() else doc_path.parent, cfg.dropbox
        )
        resolved = enc_id or (doc["encoder"] if doc["encoder"] in cfg.encoders else None)
    if not resolved:
        return {"action": "skipped", "filename": doc["filename"], "reason": "no encoder configured"}
    encoder = cfg.get_encoder(resolved)

    pages = db.get_pages(conn, doc_id)
    edits: dict[int, str] = {}
    if doc["editor"]:
        for e in db.edits_for_document(conn, doc_id, doc["editor"]):
            if e["status"] == "done" and e["text"]:
                edits[e["page_id"]] = e["text"]
    texts = [(p["page_no"], (edits.get(p["id"]) or p["raw_text"] or "").strip())
             for p in pages if (edits.get(p["id"]) or p["raw_text"] or "").strip()]
    if not texts:
        return {"action": "skipped", "filename": doc["filename"], "reason": "no text"}

    if not _encode_needed(cfg, conn, doc_id, encoder, resolved, doc_path, reprocess):
        return {"action": "skipped", "filename": doc["filename"], "reason": "records up to date"}

    doc_prompt, _src = resolve_prompt(
        doc_path.stem, doc_path if doc_path.is_dir() else doc_path.parent,
        cfg.dropbox, cfg.prompts, kind="encoder.prompt",
    )
    # LangExtract-formatted encoder content (fields + examples) lives next to
    # the source as 'encoder-prompt-langextract.md' — composed last, so it
    # carries the collection-specific schema and few-shot examples.
    lx_prompt, _lx_src = resolve_prompt(
        doc_path.stem, doc_path if doc_path.is_dir() else doc_path.parent,
        cfg.dropbox, cfg.prompts, kind="encoder.prompt.langextract",
    )
    base_prompt = compose_prompts(encoder.prompt_text, doc_prompt)
    if lx_prompt:
        base_prompt = compose_prompts(base_prompt, lx_prompt)

    client, _ = make_encoder_client(cfg, resolved)
    records: list[dict] = []
    try:
        # The encoder reads the document as ONE concatenated text so records
        # that span several pages (e.g. a letter whose header is on one page
        # and body on the next) are seen whole. Single call when it fits the
        # model window; otherwise overlapping chunks + dedupe.
        concat = "\n\n".join(f"--- page {pno} ---\n{t}" for pno, t in texts)
        if len(concat) <= encoder.max_input_chars:
            calls = [(texts, concat)]
        else:
            batch = max(1, encoder.batch_pages)
            ov = max(0, min(encoder.overlap_pages, batch - 1))
            calls = []
            start = 0
            while start < len(texts):
                chunk = texts[start : start + batch]
                calls.append((chunk, "\n\n".join(f"--- page {pno} ---\n{t}" for pno, t in chunk)))
                if start + batch >= len(texts):
                    break
                start += batch - ov
        seen: set = set()
        passes = max(1, encoder.extraction_passes)
        for pass_num in range(passes):
            if verbose and passes > 1:
                print(f"  pass {pass_num + 1}/{passes}", flush=True)
            for chunk, block in calls:
                prompt = (
                    f"{base_prompt}\n\n"
                    f"Document: {doc['filename']}\nPages: {chunk[0][0]}-{chunk[-1][0]}\n\n"
                    f"{block}"
                )
                if verbose:
                    mode = "single-pass" if len(calls) == 1 else "chunked"
                    print(f"  encoding pages {chunk[0][0]}-{chunk[-1][0]} "
                          f"({mode}, {len(block)} chars)", flush=True)
                out = client.chat_text(encoder.model, prompt, encoder.temperature,
                                       encoder.max_tokens, thinking=encoder.thinking)
                parsed = _parse_json_array(out)
                if verbose and not parsed and out.strip():
                    print(f"    (model returned no parseable JSON array; "
                          f"response {len(out)} chars, head: {out[:160]!r})", flush=True)
                for rec in _expand_records(parsed):
                    key = _record_key(rec)
                    if key in seen:
                        continue
                    # first-pass wins on the same page: a later-pass record
                    # starting on the same page with near-identical metadata
                    # (e.g. "Santo" vs "São") is the same letter (LangExtract
                    # drops overlapping extractions from later passes).
                    page = rec.get("page")
                    dup = False
                    if page is not None:
                        for prev in records:
                            if prev.get("page") == page and _record_similar(prev, rec) >= 0.75:
                                dup = True
                                break
                    if dup:
                        continue
                    seen.add(key)
                    records.append(rec)
                conn.commit()
    finally:
        client.close()

    db.clear_records(conn, doc_id, resolved)
    for rec in records:
        db.add_record(conn, doc_id, resolved, str(rec.get("kind") or rec.get("type") or "record"),
                      json.dumps(rec, ensure_ascii=False), str(rec.get("page") or ""))
    if doc["encoder"] != resolved:
        db.update_document(conn, doc_id, encoder=resolved)
    conn.commit()
    write_records_file(cfg, conn, doc_id, resolved)
    write_concatenated_file(cfg, conn, doc_id, texts, resolved)
    return {"action": "encoded", "filename": doc["filename"], "encoder": resolved, "records": len(records)}


def encode_all(cfg: Config, reprocess: bool = False, verbose: bool = True) -> dict:
    """Run the encoder pass for every document that has an encoder configured."""
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    try:
        results = []
        for d in db.list_documents(conn, limit=10000):
            results.append(encode_document(cfg, conn, d["id"], reprocess=reprocess, verbose=verbose))
        return {"results": results}
    finally:
        conn.close()


def scan_once(
    cfg: Config,
    client: ModelClient,
    palaeographer: Palaeographer,
    explicit_prompt: str | None = None,
    reprocess: bool = False,
    verbose: bool = True,
) -> dict:
    if not _acquire_scan_lock(cfg):
        return {"scanned": 0, "results": [{"action": "skipped", "filename": "(scan)",
                                           "reason": "another scan is running"}]}
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    # vision clients per palaeographer: the default is the passed client;
    # documents that resolve to another palaeographer get their own.
    clients: dict[str, tuple[ModelClient, Palaeographer]] = {
        palaeographer.id: (client, palaeographer)
    }
    try:
        db.backfill_dir_path(conn, cfg.dropbox)
        files = discover(cfg.dropbox, cfg.dir_documents)
        results = []
        for i, f in enumerate(files, 1):
            if verbose:
                print(f"[{i}/{len(files)}] {f.name}", flush=True)
            pal_id, pal_src = resolve_palaeographer_id(
                f.stem, f if f.is_dir() else f.parent, cfg.dropbox
            )
            if pal_id:
                try:
                    pal = cfg.get_palaeographer(pal_id)
                except KeyError:
                    print(f"  warning: unknown palaeographer {pal_id!r} (from {pal_src}); using default",
                          flush=True)
                    pal = palaeographer
            else:
                pal = palaeographer
            if pal.id not in clients:
                clients[pal.id] = make_vision_client(cfg, pal.id)
                if verbose:
                    print(f"  palaeographer: {pal.id} ({pal.description or pal.model})", flush=True)
            results.append(
                ingest_file(cfg, conn, clients[pal.id][0], f, clients[pal.id][1],
                            explicit_prompt, reprocess, verbose)
            )
        return {"scanned": len(files), "results": results}
    finally:
        conn.close()
        _release_scan_lock(cfg)
        for pid, (c, _p) in clients.items():
            if pid != palaeographer.id:
                c.close()


def reindex_all(cfg: Config, client: ModelClient, verbose: bool = True) -> dict:
    conn = db.connect(cfg.db_path)
    try:
        db.backfill_dir_path(conn, cfg.dropbox)
        docs = [d for d in db.list_documents(conn, limit=10000) if d["status"] == "done"]
        counts = {}
        for d in docs:
            counts[d["id"]] = index_document(cfg, conn, d["id"], embed_client=client, verbose=verbose)
        return {"reindexed": len(docs), "chunks": counts}
    finally:
        conn.close()


class _WatchHandler(FileSystemEventHandler):
    def __init__(
        self,
        cfg: Config,
        client: ModelClient,
        palaeographer: Palaeographer,
        explicit_prompt: str | None,
        debounce_s: float,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.palaeographer = palaeographer
        self.explicit_prompt = explicit_prompt
        self.debounce_s = debounce_s
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        name = Path(event.src_path).name
        if name.startswith(".") or name.endswith((".tmp", "~")):
            return
        is_pal = (
            name == "palaeographer"
            or name.startswith("palaeographer.")
            or name.endswith(".palaeographer")
            or name.endswith(".palaeographer.txt")
            or name.endswith(".palaeographer.md")
        )
        is_ed = (
            name == "editor"
            or name.startswith("editor.")
            or name.endswith(".editor")
            or name.endswith(".editor.txt")
            or name.endswith(".editor.md")
        )
        if not (is_supported(name) or name.endswith(".prompt.md") or is_pal or is_ed):
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_s, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self) -> None:
        try:
            scan_once(self.cfg, self.client, self.palaeographer, self.explicit_prompt)
        except Exception as e:  # keep the watcher alive
            print(f"scan failed: {e}")


def watch(
    cfg: Config,
    client: ModelClient,
    palaeographer: Palaeographer,
    explicit_prompt: str | None = None,
    debounce_s: float = 8.0,
) -> None:
    cfg.ensure_dirs()
    print(f"Initial scan of {cfg.dropbox} ...")
    scan_once(cfg, client, palaeographer, explicit_prompt)
    handler = _WatchHandler(cfg, client, palaeographer, explicit_prompt, debounce_s)
    observer = Observer()
    observer.schedule(handler, str(cfg.dropbox), recursive=True)
    observer.start()
    print(f"Watching {cfg.dropbox} (debounce {debounce_s:.0f}s). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

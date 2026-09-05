from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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
from .model_client import ModelClient, ModelError, PAGE_ENGINES
from .sidecar import Sidecar, effective_render, resolve_sidecar


def transcribe_page(
    client: ModelClient,
    palaeographer: Palaeographer,
    prompt_txt: str,
    img: Path,
    *,
    source: Path | None = None,
    page_no: int | None = None,
    total: int | None = None,
) -> str:
    """Produce ONE page's transcript.

    - An `engine`-bearing palaeographer is NOT an LLM: it has no
      base_url/model/tokens. Look the engine up in `PAGE_ENGINES` (a local
      OCR/parse tool such as tesseract or liteparse) and run it on the page
      render; its plain-text output becomes the transcript (no prompt — the
      editor stage, if configured, later normalizes it and adds the Notes).
    - Everything else ("" / "llm") is a vision LLM call via `chat_vision`.
    """
    engine = (palaeographer.engine or "").strip().lower()
    if engine and engine != "llm":
        fn = PAGE_ENGINES.get(engine)
        if fn is None:
            raise ModelError(
                f"unknown palaeographer engine {engine!r}; "
                f"supported: {sorted(PAGE_ENGINES)}"
            )
        ctx = SimpleNamespace(source=source, page_no=page_no, total=total)
        return fn(palaeographer, img, ctx)
    return client.chat_vision(
        palaeographer.model, prompt_txt, img,
        palaeographer.temperature, palaeographer.max_tokens,
        thinking=palaeographer.thinking,
        max_vision_px=palaeographer.max_vision_px,
        jpeg_quality=palaeographer.vision_jpeg_quality,
    )


def make_vision_client(
    cfg: Config, pal_id: str | None = None
) -> tuple[ModelClient, Palaeographer]:
    """Create a ModelClient for a palaeographer (or the active one)."""
    pal = cfg.get_palaeographer(pal_id)
    pal = cfg.resolve_model(pal)  # bind the default model when rules-only
    return ModelClient(pal.base_url, timeout_s=pal.timeout_s, api_key=pal.api_key,
                       api_style=pal.api_style), pal


def make_editor_client(cfg: Config, editor_id: str) -> tuple[ModelClient, Editor]:
    """Create a ModelClient for an editor (a text model, possibly on a
    different endpoint/model than the palaeographer)."""
    editor = cfg.get_editor(editor_id)
    editor = cfg.resolve_model(editor)  # bind the default model when rules-only
    return ModelClient(editor.base_url, timeout_s=editor.timeout_s, api_key=editor.api_key,
                       api_style=editor.api_style), editor


def _vision_client(pal: Palaeographer) -> ModelClient:
    """Build a ModelClient for an (already resolved/overridden) palaeographer."""
    return ModelClient(pal.base_url, timeout_s=pal.timeout_s, api_key=pal.api_key,
                       api_style=pal.api_style)


def _client_key(pal: Palaeographer) -> tuple:
    return (pal.id, pal.model_ref or "")


def _doc_sidecar(cfg: Config, path: Path) -> Sidecar:
    """Resolve the merged pha.yaml sidecar for a document path, including a
    document-specific `<stem>.pha.yaml` when present."""
    if path.is_dir():
        return resolve_sidecar(cfg.dropbox, path)
    return resolve_sidecar(cfg.dropbox, path.parent, stem=path.stem)


def _raw_sha(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode()).hexdigest()


_HEADER_WINDOW = 6  # lines after a candidate start line to look for a header


def _line_matches(re_header, line: str) -> bool:
    return bool(re_header.pattern) and bool(re_header.search(line))


def _regex_candidates(texts: list, encoder: Encoder) -> list[int]:
    """Regex fast-path: pages where a line matches the encoder's
    candidate_pattern (e.g. a lone Roman numeral or a bare notice number) and
    a following NON-BLANK line (or a 1-2 line wrapped name) matches
    candidate_header (e.g. a 'Name aos Name' letter header or an ALL-CAPS
    biography name that may wrap across two lines)."""
    if not encoder.candidate_pattern:
        return []
    try:
        re_start = re.compile(encoder.candidate_pattern, re.MULTILINE)
        re_header = re.compile(encoder.candidate_header or "", re.MULTILINE)
    except re.error:
        return []
    hits: list[int] = []
    for pno, text in texts:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not re_start.search(line):
                continue
            window = [w for w in lines[i + 1 : i + 1 + _HEADER_WINDOW] if w.strip()]
            # header is the first caps line; if it has no trailing '.', it may
            # wrap: try "first + second caps line" as the header too.
            ok = bool(window) and _line_matches(re_header, window[0])
            if not ok and len(window) >= 2:
                ok = _line_matches(re_header, window[0] + " " + window[1])
            if ok:
                hits.append(pno)
                break
    return hits


def detect_entry_pages(texts: list, encoder: Encoder) -> list[int]:
    """Determine where entries start, two ways:
    - regex fast-path from the encoder config (candidate_pattern/header), or
    - (future) a cheap model scan per chunk driven by the collection's
      detection rules in encoder.prompt.md. Today the regex path is the
      general configurable mechanism; a collection without patterns falls
      back to no detection (single-pass)."""
    return _regex_candidates(texts, encoder)


def _build_entry_spans(texts: list, starts: list[int],
                       max_pages: int = 30) -> list[tuple[list, str, int | None]]:
    """Slice the document into per-entry spans: each span runs from one
    detected start page through the pages before the NEXT start (so the entry
    and its body are seen whole). A span is capped at `max_pages` so a long
    entry never swallows the whole document; the pages beyond the cap (up to
    the next start, or the end) become a no-hint continuation span so no page
    is dropped."""
    idx = [i for i, (pno, _) in enumerate(texts) if pno in set(starts)]
    calls: list[tuple[list, str, int | None]] = []
    if idx and idx[0] > 0:  # pages before the first start (front matter etc.)
        pre = texts[: idx[0]]
        block = "\n\n".join(f"--- page {p_} ---\n{t}" for p_, t in pre)
        calls.append((pre, block, None))
    for k, j in enumerate(idx):
        end = idx[k + 1] if k + 1 < len(idx) else len(texts)
        limit = min(end, j + max_pages)
        span = texts[j:limit]
        block = "\n\n".join(f"--- page {p_} ---\n{t}" for p_, t in span)
        calls.append((span, block, texts[j][0]))
        if limit < end:  # continuation beyond the cap (no new entry starts here)
            rest = texts[limit:end]
            block2 = "\n\n".join(f"--- page {p_} ---\n{t}" for p_, t in rest)
            calls.append((rest, block2, None))
    if not calls:
        block = "\n\n".join(f"--- page {p_} ---\n{t}" for p_, t in texts)
        calls = [(texts, block, None)]
    return calls


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


def _lock_owner_pid(lock: Path) -> int:
    """Read the pid recorded in a lock file (0 = none/unknown)."""
    try:
        return int(lock.read_text(encoding="utf-8").strip() or "0")
    except (ValueError, OSError):
        return 0


def _lock_stale(lock: Path, pid: int) -> bool:
    """Is this lock file reclaimable?

    - A recorded pid that is no longer alive -> stale (its holder died).
    - A pid-less file (created but not yet written) -> stale only after the
      6 h age threshold: it may belong to a scan that is still starting, and
      stealing it would let two scans run together.
    - A lock OLDER than 6 h is always stale even when its pid looks alive
      (pids get reused; a stale lock must never wedge every future scan).
    """
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        age = 0.0
    if pid > 0:
        return pid != os.getpid() and not _pid_alive(pid)
    return age > 6 * 3600


def _scan_lock_held_by_other(cfg: Config) -> bool:
    """True if a DIFFERENT, still-running process holds the scan lock."""
    lock = _scan_lock_path(cfg)
    if not lock.exists():
        return False
    pid = _lock_owner_pid(lock)
    if pid == os.getpid():
        return False
    return not _lock_stale(lock, pid)


def _acquire_scan_lock(cfg: Config) -> bool:
    """Take the scan lock ATOMICALLY (O_CREAT|O_EXCL).

    When two scans start at the same instant, exactly ONE creates the lock
    file and the other refuses — no window in which both think they hold it
    (unlike check-then-write on a plain file). A lock whose holder is dead is
    reclaimed; a 6 h old lock is stale even if its pid looks alive.

    Returns False if a live scan owns the lock, True once this process owns it
    (or when locking is impossible and pha proceeds best-effort)."""
    lock = _scan_lock_path(cfg)
    for _attempt in range(20):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            pid = _lock_owner_pid(lock)
            if pid == os.getpid():
                return True  # this process already owns it
            if _lock_stale(lock, pid):
                # Reclaim. A racer may replace the file between our staleness
                # check and the unlink, so loop back to the atomic create.
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            return False  # a live scan owns the lock
        except OSError:
            return True  # cannot lock; proceed (best effort, as before)
        else:
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
    return False  # repeated stale-reclaim races: refuse rather than collide


def _release_scan_lock(cfg: Config) -> None:
    lock = _scan_lock_path(cfg)
    try:
        if lock.exists() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
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


def discover(dropbox: Path, dir_documents: bool = True, root: Path | None = None) -> list[Path]:
    """List document units: individual files plus image-directory documents.

    By default walks the whole `dropbox` tree. Pass `root` to restrict
    discovery to a single subpath (a collection, e.g. the directory of
    dropbox/collections/pfister-notices) so a scan can target one collection
    instead of the whole dropbox. `root` must be inside `dropbox`.

    If `root` itself is a directory-of-images, it is treated as ONE document
    (the folder is the document, its images are pages) — the same rule that
    applies to image-directories below a collection root. This makes
    `--path` to a leaf image-folder behave consistently with `--path` to the
    collection root."""
    base = dropbox if root is None else root
    if not base.exists():
        return []
    units: list[Path] = []
    # When scanning a specific root that is itself a document-directory, the
    # whole folder is the document — do not enumerate its images separately.
    if root is not None and dir_documents and base.is_dir() and _is_document_dir(base):
        return [base]
    for p in sorted(base.rglob("*")):
        if not p.is_file() or not is_supported(p.name) or p.name.startswith("."):
            continue
        if dir_documents and p.parent != base and _is_document_dir(p.parent):
            continue  # this file is a page of a document-directory
        units.append(p)
    if dir_documents:
        for d in sorted(base.rglob("*")):
            if d.is_dir() and d != base and _is_document_dir(d):
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
    # The model file (models/<id>.md) now holds resolution limits
    # (max_vision_px/vision_jpeg_quality); editing it re-extracts too.
    if palaeographer and getattr(palaeographer, "model_ref", "") and palaeographer.model_ref in cfg.models:
        mf = cfg.models[palaeographer.model_ref].prompt_file
        if mf:
            try:
                if mf.stat().st_mtime > ts:
                    return True
            except OSError:
                pass
    for cand in palaeographer_candidates(path.stem, file_dir, cfg.dropbox):
        if cand.exists() and cand.stat().st_mtime > ts:
            return True
    return False


def _doc_slug(doc) -> str:
    """Readable, version-safe library folder name for a document:
    `<stem>_<YYYY-MM-DD>` (its creation date).

    A content-changed document is a NEW row with a NEW created_at (see
    ingest_file: the old row is deleted), so each version gets its own dated
    folder and the old one is left untouched on disk. Same-day re-scans of a
    changed file can share a date, but that is harmless: the previous row is
    already gone, so its folder just holds the latest output."""
    import datetime
    stem = Path(doc["path"]).stem
    try:
        date = datetime.datetime.fromtimestamp(doc["created_at"]).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        date = "unknown"
    return f"{stem}_{date}"


def remove_library_artifact(cfg: Config, doc) -> None:
    """Delete the document's library folder (all palaeographer transcriptions)."""
    if not doc:
        return
    slug = _doc_slug(doc)
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
    sidecar: Sidecar | None = None,
) -> dict:
    path = Path(path)
    if sidecar is None:
        sidecar = _doc_sidecar(cfg, path)
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
    if sidecar.editor_set:
        ed_id = sidecar.editor.rules if sidecar.editor else None
    else:
        ed_id, _edsrc = resolve_editor_id(
            path.stem, path if path.is_dir() else path.parent, cfg.dropbox
        )
    doc_id = existing["id"] if reuse else db.add_document(
        conn, filename=path.name, path=str(path), sha256=sha,
        size_bytes=size, mtime=mtime, kind=kind, now=now, dir_path=rel_dir,
        palaeographer=palaeographer.id, editor=ed_id,
        palaeographer_model=palaeographer.model_ref or None,
    )
    if reuse:
        db.update_document(conn, doc_id, palaeographer=palaeographer.id, editor=ed_id,
                           palaeographer_model=palaeographer.model_ref or None)
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

    render_dpi, max_image_px, jpeg_quality = effective_render(cfg, sidecar)
    try:
        source_names: list[str | None] = []
        if path.is_dir():
            images = [f for f in sorted(path.iterdir())
                      if f.is_file() and is_supported(f.name) and not f.name.startswith(".")]
            total = len(images)
            renders: list[Path] = []
            for img in images:
                n = len(renders)
                renders += render_document(img, cfg.renders / sha, render_dpi,
                                           max_image_px, jpeg_quality,
                                           prefix=img.stem)
                # a single image renders to one page: map each new render to
                # the source image stem (e.g. 505V) for file naming.
                new = len(renders) - n
                source_names += [img.stem] * new
        else:
            total = page_count(path)
            renders = render_document(path, cfg.renders / sha, render_dpi, max_image_px, jpeg_quality)
            source_names = [None] * len(renders)
    except Exception as e:
        db.set_document_status(conn, doc_id, "error", error=f"render failed: {e}")
        conn.commit()
        return {"action": "error", "filename": path.name, "error": f"render failed: {e}"}
    db.update_document(conn, doc_id, page_count=total)

    page_errors: list[tuple[int, str]] = []
    consecutive_failures = 0
    for i, img in enumerate(renders, start=1):
        page_id = db.add_page(conn, doc_id, i, source_name=source_names[i - 1] if i - 1 < len(source_names) else None)
        page = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if page["reviewed_at"]:
            continue  # a human corrected this page; never re-extract over it
        if page["status"] == "done" and not force:
            continue  # resume: keep already-extracted pages
        prompt_txt = build_page_prompt(prompt, path.name, i, total)
        if verbose:
            print(f"  page {i}/{total}: extracting ...", flush=True)
        try:
            text = transcribe_page(client, palaeographer, prompt_txt, img,
                                   source=path, page_no=i, total=total)
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
            batch_size=cfg.embed_batch_size,
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
    pal_model = doc["palaeographer_model"] or None
    slug = _doc_slug(doc)
    rel_dir = Path(doc["dir_path"] or "")
    out_dir = cfg.library / rel_dir / slug / (f"transcription-{pal}" + (f"@{pal_model}" if pal_model else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "source": doc["path"],
        "filename": doc["filename"],
        "collection": doc["dir_path"] or "(root)",
        "document_id": doc["id"],
        "pages_total": doc["page_count"],
        "palaeographer": pal,
        "model": doc["palaeographer_model"] or None,
        "editor": doc["editor"] or None,
        "prompt": doc["prompt_source"],
    }
    for p in pages:
        fm = dict(base)
        fm["page"] = p["page_no"]
        fm["status"] = "done" if p["status"] == "done" else "waiting"
        if p["reviewed_at"]:
            fm["reviewed"] = True
        body = (p["raw_text"] or "").strip()
        body = format_notes(body) if body else "*waiting*"
        text = (
            "---\n"
            + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=100000).strip()
            + "\n---\n\n"
            + body
            + "\n"
        )
        # name the file after the source image stem when it is a directory-of-
        # images document (e.g. 505V.md); otherwise page-NNN.md.
        name = f"{p['source_name']}.md" if p["source_name"] else f"page-{p['page_no']:03d}.md"
        f = out_dir / name
        f.write_text(text, encoding="utf-8")
        # record when we wrote this file so `pha status` can detect later
        # human edits via the file's mtime (timestamp, not content, compare).
        conn.execute("UPDATE pages SET exported_at = ? WHERE id = ?",
                     (f.stat().st_mtime, p["id"]))
    # drop stale page-NNN.md files left by the pre-source-stem naming scheme,
    # but only when this document actually uses source stems.
    if any(p["source_name"] for p in pages):
        for stale in out_dir.glob("page-*.md"):
            stale.unlink(missing_ok=True)
    conn.commit()
    return out_dir


# --------------------------------------------------------------------------- editors

def _edit_needed(page, edit_row, editor: Editor, reprocess: bool) -> bool:
    """Does this page need (re-)editing?"""
    if edit_row is not None and edit_row["reviewed_at"]:
        return False  # a human corrected this edit; never re-edit over it
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
    ed_model = doc["editor_model"] or None
    slug = _doc_slug(doc)
    rel_dir = Path(doc["dir_path"] or "")
    out_dir = cfg.library / rel_dir / slug / (f"edited-{editor_id}" + (f"@{ed_model}" if ed_model else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "source": doc["path"],
        "filename": doc["filename"],
        "collection": doc["dir_path"] or "(root)",
        "document_id": doc["id"],
        "pages_total": doc["page_count"],
        "palaeographer": doc["palaeographer"] or None,
        "editor": editor_id,
        "model": doc["editor_model"] or None,
    }
    for e in db.edits_for_document(conn, doc_id, editor_id):
        p = conn.execute("SELECT page_no, source_name, reviewed_at FROM pages WHERE id = ?", (e["page_id"],)).fetchone()
        if not p:
            continue
        fm = dict(base)
        fm["page"] = p["page_no"]
        fm["status"] = "done" if e["status"] == "done" else "waiting"
        if e["reviewed_at"]:
            fm["reviewed"] = True
        body = (e["text"] or "*waiting*").strip()
        text = (
            "---\n"
            + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=100000).strip()
            + "\n---\n\n"
            + body
            + "\n"
        )
        name = f"{p['source_name']}.md" if p["source_name"] else f"page-{p['page_no']:03d}.md"
        f = out_dir / name
        f.write_text(text, encoding="utf-8")
        # record the write time so later human edits are detected by mtime
        conn.execute("UPDATE page_edits SET exported_at = ? WHERE id = ?",
                     (f.stat().st_mtime, e["id"]))
    # drop stale page-NNN.md left by the pre-source-stem scheme when the doc
    # uses source stems.
    if conn.execute(
        "SELECT COUNT(*) n FROM pages WHERE document_id=? AND source_name IS NOT NULL",
        (doc_id,)).fetchone()["n"]:
        for stale in out_dir.glob("page-*.md"):
            stale.unlink(missing_ok=True)
    conn.commit()
    return out_dir


# --------------------------------------------------------------------------- review round-trip

def pending_review_files(cfg: Config, conn) -> list[dict]:
    """Find library page files a human edited since pha last wrote/imported them.

    Timestamp-based: a file is pending if its filesystem mtime is NEWER than
    the page/edit's `exported_at` (when pha last wrote that file). For legacy
    rows with exported_at NULL we fall back to comparing the file body to the
    DB text. Returns {path, document_id, page_no, variant, editor}.
    """
    pending: list[dict] = []
    for p in sorted(cfg.library.rglob("*.md")):
        rel_parts = p.relative_to(cfg.library).parts
        if len(rel_parts) < 3:
            continue
        variant = rel_parts[-2]
        if not (variant.startswith("transcription-") or variant.startswith("edited-")):
            continue
        parsed = _parse_library_file(p)
        if not parsed:
            continue
        fm, body = parsed
        d_id, page_no = fm.get("document_id"), fm.get("page")
        if d_id is None or page_no is None:
            continue
        doc = db.get_document(conn, int(d_id))
        if not doc:
            continue
        page = conn.execute(
            "SELECT id, raw_text, exported_at FROM pages WHERE document_id = ? AND page_no = ?",
            (int(d_id), int(page_no))).fetchone()
        if not page:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if variant.startswith("transcription-"):
            if page["exported_at"] is not None:
                edited = mtime > page["exported_at"]
            else:
                # legacy row: compare content, mirroring write_document_pages
                raw = (page["raw_text"] or "").strip()
                current = format_notes(raw) if raw else "*waiting*"
                edited = body.strip() != current.strip()
            if edited:
                pending.append({"path": str(p), "document_id": int(d_id),
                                "page_no": int(page_no), "variant": variant})
        else:
            editor = variant[len("edited-"):].split("@", 1)[0]
            edit = db.get_page_edit(conn, page["id"], editor)
            if edit is not None and edit["exported_at"] is not None:
                edited = mtime > edit["exported_at"]
            else:
                current = ((edit["text"] if edit else "") or "*waiting*").strip()
                edited = body.strip() != current
            if edited:
                pending.append({"path": str(p), "document_id": int(d_id),
                                "page_no": int(page_no), "variant": variant,
                                "editor": editor})
    return pending


def _parse_library_file(path: Path) -> tuple[dict, str] | None:
    """Parse a library page file: returns (front_matter, body) or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    fm_text = text[3:end].strip()
    body = text[end + 4:].strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return None
    return (fm, body)


def page_row_raw(conn, page_id: int) -> str:
    row = conn.execute("SELECT raw_text FROM pages WHERE id = ?", (page_id,)).fetchone()
    return row["raw_text"] or ""


def review_import(cfg: Config, conn, doc_id: int | None = None, verbose: bool = True) -> dict:
    """Import human corrections from the library markdown files back into the DB.

    The historian edits `library/.../transcription-<pal>/<stem>.md` or
    `library/.../edited-<editor>/<stem>.md`; this reads them back, updates
    pages.raw_text / page_edits.text, and stamps them reviewed so later
    scan/edit passes skip them. Returns counts.
    """
    updated_pages = 0
    updated_edits = 0
    skipped = 0
    for p in sorted(cfg.library.rglob("*.md")):
        rel_parts = p.relative_to(cfg.library).parts
        # library/<rel_dir>/<slug>/transcription-<pal>/<file>.md
        # library/<rel_dir>/<slug>/edited-<editor>/<file>.md
        if len(rel_parts) < 3:
            continue
        variant = rel_parts[-2]  # transcription-xxx or edited-xxx
        parsed = _parse_library_file(p)
        if not parsed:
            skipped += 1
            continue
        fm, body = parsed
        d_id = fm.get("document_id")
        page_no = fm.get("page")
        if d_id is None or page_no is None:
            skipped += 1
            continue
        if doc_id is not None and int(d_id) != doc_id:
            continue
        doc = db.get_document(conn, int(d_id))
        if not doc:
            skipped += 1
            continue
        page = conn.execute(
            "SELECT id FROM pages WHERE document_id = ? AND page_no = ?",
            (int(d_id), int(page_no))).fetchone()
        if not page:
            skipped += 1
            continue
        if variant.startswith("transcription-"):
            db.mark_page_reviewed(conn, page["id"], body)
            updated_pages += 1
            if verbose:
                print(f"  reviewed transcription: doc {d_id} page {page_no} ({p.name})")
        elif variant.startswith("edited-"):
            editor = variant[len("edited-"):].split("@", 1)[0]
            db.set_page_edit(conn, page["id"], editor, text=body,
                             raw_sha=_raw_sha(page_row_raw(conn, page["id"])))
            db.mark_edit_reviewed(conn, page["id"], editor, body)
            updated_edits += 1
            if verbose:
                print(f"  reviewed edit: doc {d_id} page {page_no} ({p.name}, editor {editor})")
    conn.commit()
    return {"pages": updated_pages, "edits": updated_edits, "skipped": skipped}


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
        db.update_document(conn, doc_id, editor=resolved, editor_model=None)
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
    editor_model = None
    if editor_id:
        resolved = editor_id
    else:
        path = Path(doc["path"])
        sc = _doc_sidecar(cfg, path)
        if sc.editor_set:
            if sc.editor is None:
                resolved = None
            else:
                resolved = sc.editor.rules
                editor_model = sc.editor.model
        else:
            ed_id, _src = resolve_editor_id(
                path.stem, path if path.is_dir() else path.parent, cfg.dropbox
            )
            resolved = ed_id or (doc["editor"] if doc["editor"] in cfg.editors else None)
    if not resolved:
        return {"action": "skipped", "filename": doc["filename"], "reason": "no editor configured"}
    if resolved in ("null", "passthrough"):
        return _edit_null(cfg, conn, doc_id, resolved, reprocess, verbose)
    editor = cfg.get_editor(resolved)
    editor = cfg.resolve_model(editor, editor_model)
    pages = db.get_pages(conn, doc_id)
    client = ModelClient(editor.base_url, timeout_s=editor.timeout_s, api_key=editor.api_key,
                         api_style=editor.api_style)
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
    if doc["editor"] != resolved or (doc["editor_model"] or None) != (editor.model_ref or None):
        db.update_document(conn, doc_id, editor=resolved,
                           editor_model=editor.model_ref or None)
        conn.commit()
    write_edited_pages(cfg, conn, doc_id, resolved)
    return {"action": "edited", "filename": doc["filename"], "editor": resolved, "pages": edited}


def edit_all(cfg: Config, reprocess: bool = False, verbose: bool = True) -> dict:
    """Run the editor pass for every document that has an editor configured.

    Uses the SAME lock as scan_once: a scan and an edit must not run
    concurrently, because they both load a local model and LM Studio only
    holds ONE model in memory at a time (two local jobs -> swap -> disk fill).
    If another job holds the lock, this pass reports it and exits."""
    if not _acquire_scan_lock(cfg):
        return {"results": [{"action": "skipped", "filename": "(edit)",
                             "reason": "another scan/edit job is running (one local model at a time)"}]}
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    try:
        results = []
        for d in db.list_documents(conn, limit=10000):
            results.append(edit_document(cfg, conn, d["id"], reprocess=reprocess, verbose=verbose))
        return {"results": results}
    finally:
        conn.close()
        _release_scan_lock(cfg)


# --------------------------------------------------------------------------- encoders (structured records)

def make_encoder_client(cfg: Config, encoder_id: str | None = None,
                        encoder: Encoder | None = None) -> tuple[ModelClient, Encoder]:
    """Create a ModelClient for an encoder. Pass either a global encoder_id
    (legacy) or the loaded encoder object (collection-local)."""
    if encoder is None:
        encoder = cfg.get_encoder(encoder_id)
    encoder = cfg.resolve_model(encoder)  # bind the default model when rules-only
    return ModelClient(encoder.base_url, timeout_s=encoder.timeout_s, api_key=encoder.api_key,
                       api_style=encoder.api_style), encoder


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
                   doc_path: Path, reprocess: bool, enc_file: Path | None = None) -> bool:
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
    if enc_file is not None:
        from .extract import resolve_encoder_prompt
        for kind in ("encoder.prompt", "encoder.prompt.langextract"):
            _txt, src = resolve_encoder_prompt(enc_file, kind)
            if src and src != "none":
                candidates.append(Path(src))
    else:
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
    t2 = None
    try:
        t2 = conn.execute(
            "SELECT MAX(updated_at) AS m FROM pages WHERE document_id = ? AND status = 'done'",
            (doc_id,),
        ).fetchone()["m"]
    except sqlite3.OperationalError:
        # legacy schema: pages has no updated_at column — skip this input-
        # changed check rather than failing `pha encode` on every record-bearing doc.
        pass
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
    slug = _doc_slug(doc)
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
    slug = _doc_slug(doc)
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
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _page_filter(encoder: Encoder) -> set[int] | None:
    """Return the set of page numbers this encoder handles, or None for all.
    `pages` in the encoder front matter: "1-15", "1-15,40", "all"."""
    if not encoder.pages or encoder.pages.strip().lower() in ("all", "*", ""):
        return None
    wanted: set[int] = set()
    for part in encoder.pages.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                wanted.update(range(int(a), int(b) + 1))
            except ValueError:
                continue
        else:
            try:
                wanted.add(int(part))
            except ValueError:
                continue
    return wanted or None


def encode_document(
    cfg: Config,
    conn,
    doc_id: int,
    encoder_id: str | None = None,
    enc_file: Path | None = None,
    reprocess: bool = False,
    verbose: bool = True,
    model_override: str | None = None,
) -> dict:
    """Run the encoder over a document's transcription (edited text when the
    document has an editor, else raw), producing structured records.

    `enc_file` is a collection-local encoder definition
    (dropbox/collections/COLX/encoders/<name>.md); when given, the stage
    prompts (encoders/<name>.prompt.md, encoders/<name>.langextract.md) are
    resolved NEXT TO it and the encoder's `pages` range filters which pages
    it processes. `encoder_id` (a global id) is the legacy path. The encoder
    prompt is composed as:
    [encoder base prompt] + [encoders/<name>.prompt.md] + [encoders/<name>.langextract.md].
    """
    doc = db.get_document(conn, doc_id)
    if not doc:
        return {"action": "skipped", "filename": "?", "reason": "no document"}
    doc_path = Path(doc["path"])

    if enc_file is not None:
        encoder = cfg.encoder_from_file(enc_file)
        if encoder is None:
            return {"action": "skipped", "filename": doc["filename"], "reason": "invalid encoder file"}
        resolved = enc_file.stem
    else:
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

    encoder = cfg.resolve_model(encoder, model_override)
    pages = db.get_pages(conn, doc_id)
    edits: dict[int, str] = {}
    if doc["editor"]:
        for e in db.edits_for_document(conn, doc_id, doc["editor"]):
            if e["status"] == "done" and e["text"]:
                edits[e["page_id"]] = e["text"]
    texts = [(p["page_no"], (edits.get(p["id"]) or p["raw_text"] or "").strip())
             for p in pages if (edits.get(p["id"]) or p["raw_text"] or "").strip()]
    page_filter = _page_filter(encoder)
    if page_filter is not None:
        texts = [x for x in texts if x[0] in page_filter]
    if not texts:
        return {"action": "skipped", "filename": doc["filename"], "reason": "no text"}

    if not _encode_needed(cfg, conn, doc_id, encoder, resolved, doc_path, reprocess, enc_file):
        return {"action": "skipped", "filename": doc["filename"], "reason": "records up to date"}

    if enc_file is not None:
        from .extract import resolve_encoder_prompt
        doc_prompt, _src = resolve_encoder_prompt(enc_file, "encoder.prompt")
        lx_prompt, _lx_src = resolve_encoder_prompt(enc_file, "encoder.prompt.langextract")
    else:
        doc_prompt, _src = resolve_prompt(
            doc_path.stem, doc_path if doc_path.is_dir() else doc_path.parent,
            cfg.dropbox, cfg.prompts, kind="encoder.prompt",
        )
        lx_prompt, _lx_src = resolve_prompt(
            doc_path.stem, doc_path if doc_path.is_dir() else doc_path.parent,
            cfg.dropbox, cfg.prompts, kind="encoder.prompt.langextract",
        )
    base_prompt = compose_prompts(encoder.prompt_text, doc_prompt)
    if lx_prompt:
        base_prompt = compose_prompts(base_prompt, lx_prompt)

    client, _ = make_encoder_client(cfg, encoder=encoder)
    records: list[dict] = []
    try:
        # Two-stage encoding:
        #   1. DETECT where entries start (regex fast-path from the encoder
        #      config, else a cheap model scan per chunk driven by the
        #      collection's detection rules).
        #   2. EXTRACT each entry in its own small call with the page TOLD to
        #      the model ("an entry starts at page N — extract only it"), a
        #      confirmation task instead of hunting a huge document. This is
        #      deterministic on recall (detection) and reliable on precision
        #      (small per-entry context) — no multi-pass / merging needed.
        # Fallback: no detection -> single whole-document pass or overlapping
        # chunks (the pre-two-stage behaviour).
        starts = detect_entry_pages(texts, encoder)
        if starts:
            calls = _build_entry_spans(texts, starts)  # (span_texts, block, start_page|None)
        else:
            concat = "\n\n".join(f"--- page {pno} ---\n{t}" for pno, t in texts)
            if len(concat) <= encoder.effective_max_input_chars:
                calls = [(texts, concat, None)]
            else:
                batch = max(1, encoder.batch_pages)
                ov = max(0, min(encoder.overlap_pages, batch - 1))
                calls = []
                start = 0
                while start < len(texts):
                    chunk = texts[start : start + batch]
                    calls.append((chunk, "\n\n".join(f"--- page {pno} ---\n{t}" for pno, t in chunk), None))
                    if start + batch >= len(texts):
                        break
                    start += batch - ov
        seen: set = set()
        passes = max(1, encoder.extraction_passes)
        for pass_num in range(passes):
            if verbose and passes > 1:
                print(f"  pass {pass_num + 1}/{passes}", flush=True)
            for chunk, block, start_page in calls:
                entry_hint = (
                    f"\n\nAn entry STARTS at page {start_page} in the text below.\n"
                    f"Extract ONLY that entry (and its sub-records). The page\n"
                    f"attribute of its main record MUST be {start_page}.\n"
                    f"If page {start_page} is not really an entry after all, return []."
                ) if start_page else ""
                prompt = (
                    f"{base_prompt}{entry_hint}\n\n"
                    f"Document: {doc['filename']}\nPages: {chunk[0][0]}-{chunk[-1][0]}\n\n"
                    f"{block}"
                )
                if verbose:
                    mode = "entry-spans" if starts else ("single-pass" if len(calls) == 1 else "chunked")
                    print(f"  encoding pages {chunk[0][0]}-{chunk[-1][0]} "
                          f"({mode}, {len(block)} chars)", flush=True)
                # Retry empty/unparseable responses: models sometimes return
                # '' or prose instead of the JSON array (flaky); a couple of
                # retries fix most of it. A generous max_tokens matters:
                # reasoning models emit a <think> block even with thinking
                # disabled, and a tight cap makes them return [] rather than
                # risk truncating their answer.
                parsed: list = []
                for attempt in range(3):
                    out = client.chat_text(encoder.model, prompt, encoder.temperature,
                                           max(8192, encoder.max_tokens),
                                           thinking=encoder.thinking)
                    parsed = _parse_json_array(out)
                    if parsed or not out.strip():
                        break
                    if verbose:
                        print(f"    (retry {attempt + 1}: response {len(out)} chars not parseable, "
                              f"head: {out[:120]!r})", flush=True)
                    if start_page and attempt >= 1:
                        # The detector flagged this page; drop the escape
                        # hatch and insist, in case the model is being overly
                        # conservative (returning [] instead of extracting).
                        prompt = prompt.replace(
                            "If page {0} is not really an entry after all, return [].".format(start_page),
                            "A detector flagged page {0} as an entry start. Extract it.".format(start_page),
                        )
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
    """Run the encoder pass for every document that has encoders configured.

    Encoders live NEXT TO THE SOURCE (dropbox/collections/COLX/encoders/*.md),
    one file per structure type in the document. All of a document's encoders
    run in succession, ordered by their `pages` front matter (e.g. the
    chronological table on pages 1-15 first, then the person notices on the
    rest)."""
    from .extract import encoder_file_named, encoder_files_for

    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    try:
        results = []
        for d in db.list_documents(conn, limit=10000):
            doc_path = Path(d["path"])
            fdir = doc_path if doc_path.is_dir() else doc_path.parent
            sc = _doc_sidecar(cfg, doc_path)
            if sc.encoders is not None:
                # pha.yaml lists encoders explicitly (by name), in page order
                specs: list[tuple[Path, str | None]] = []
                for spec in sc.encoders:
                    f = encoder_file_named(spec.rules, fdir, cfg.dropbox)
                    if f is None:
                        print(f"  warning: unknown encoder {spec.rules!r} for {doc_path.name}", flush=True)
                        continue
                    specs.append((f, spec.model))
            else:
                enc_files = encoder_files_for(doc_path.stem, fdir, cfg.dropbox)
                specs = [(f, None) for f in enc_files]
            if not specs:
                # legacy: single global encoder via the 'encoder' selection file
                results.append(encode_document(cfg, conn, d["id"], reprocess=reprocess, verbose=verbose))
                continue
            # order by page range start (empty pages => whole document => last)
            def _page_start(item: tuple[Path, str | None]) -> int:
                f, _m = item
                e = cfg.encoder_from_file(f)
                if e and e.pages and "-" in e.pages:
                    try:
                        return int(e.pages.split("-")[0])
                    except ValueError:
                        return 10**9
                return 10**9  # whole-document encoders run after section ones
            for f, model_override in sorted(specs, key=_page_start):
                results.append(encode_document(cfg, conn, d["id"], enc_file=f,
                                               reprocess=reprocess, verbose=verbose,
                                               model_override=model_override))
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
    path: str | None = None,
) -> dict:
    if not _acquire_scan_lock(cfg):
        return {"scanned": 0, "results": [{"action": "skipped", "filename": "(scan)",
                                           "reason": "another scan is running"}]}
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    # resolve the --path/--collection target to a discovery root under dropbox
    scan_root = cfg.dropbox
    if path:
        root = Path(path)
        if not root.is_absolute():
            root = cfg.dropbox / root
        root = root.resolve()
        if cfg.dropbox.resolve() in root.parents or root == cfg.dropbox.resolve() or root.exists():
            scan_root = root
        else:
            # path outside the dropbox: allow only if it exists (absolute target)
            if root.exists():
                scan_root = root
            else:
                print(f"  target path does not exist: {path}", flush=True)
                scan_root = root  # discover() returns [] if missing
    # vision clients per effective palaeographer (rules + model override): the
    # default is the passed client; other palaeographers get their own.
    clients: dict[tuple, tuple[ModelClient, Palaeographer]] = {
        _client_key(palaeographer): (client, palaeographer)
    }
    try:
        db.backfill_dir_path(conn, cfg.dropbox)
        files = discover(cfg.dropbox, cfg.dir_documents, root=scan_root)
        results = []
        for i, f in enumerate(files, 1):
            if verbose:
                print(f"[{i}/{len(files)}] {f.name}", flush=True)
            sc = _doc_sidecar(cfg, f)
            pal_id, pal_src = resolve_palaeographer_id(
                f.stem, f if f.is_dir() else f.parent, cfg.dropbox
            )
            model_id = None
            if sc.palaeographer:
                pal_id = sc.palaeographer.rules
                pal_src = str(sc.source)
                model_id = sc.palaeographer.model
            if pal_id:
                try:
                    pal = cfg.get_palaeographer(pal_id)
                except KeyError:
                    print(f"  warning: unknown palaeographer {pal_id!r} (from {pal_src}); using default",
                          flush=True)
                    pal = palaeographer
            else:
                pal = palaeographer
            try:
                pal = cfg.resolve_model(pal, model_id)
            except KeyError:
                print(f"  warning: unknown model {model_id or cfg.default_model!r}; leaving {pal.id} unbound",
                      flush=True)
            key = _client_key(pal)
            if key not in clients:
                clients[key] = (_vision_client(pal), pal)
                if verbose:
                    print(f"  palaeographer: {pal.id} ({pal.description or pal.model})", flush=True)
            results.append(
                ingest_file(cfg, conn, clients[key][0], f, clients[key][1],
                            explicit_prompt, reprocess, verbose, sidecar=sc)
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
        path: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.palaeographer = palaeographer
        self.explicit_prompt = explicit_prompt
        self.debounce_s = debounce_s
        self.path = path
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
            scan_once(self.cfg, self.client, self.palaeographer, self.explicit_prompt,
                      path=self.path)
        except Exception as e:  # keep the watcher alive
            print(f"scan failed: {e}")


def watch(
    cfg: Config,
    client: ModelClient,
    palaeographer: Palaeographer,
    explicit_prompt: str | None = None,
    debounce_s: float = 8.0,
    path: str | None = None,
) -> None:
    cfg.ensure_dirs()
    print(f"Initial scan of {cfg.dropbox} ...")
    scan_once(cfg, client, palaeographer, explicit_prompt, path=path)
    handler = _WatchHandler(cfg, client, palaeographer, explicit_prompt, debounce_s, path=path)
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

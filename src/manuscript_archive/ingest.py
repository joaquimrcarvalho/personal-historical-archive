from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from .config import Config, Palaeographer
from .embed import pack, prefixed
from .extract import (
    build_page_prompt,
    compose_prompts,
    is_supported,
    page_count,
    prompt_candidates,
    render_document,
    resolve_prompt,
)
from .model_client import ModelClient, ModelError


def make_vision_client(
    cfg: Config, pal_id: str | None = None
) -> tuple[ModelClient, Palaeographer]:
    """Create a ModelClient for a palaeographer (or the active one)."""
    pal = cfg.get_palaeographer(pal_id)
    return ModelClient(pal.base_url, timeout_s=pal.timeout_s, api_key=pal.api_key), pal


# --------------------------------------------------------------------------- scan lock

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
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


def _prompt_newer_than(path: Path, cfg: Config, ts: float) -> bool:
    file_dir = path if path.is_dir() else path.parent
    for cand in prompt_candidates(path.stem, file_dir, cfg.dropbox, cfg.prompts):
        if cand.exists() and cand.stat().st_mtime > ts:
            return True
    default = cfg.prompts / "default_prompt.md"
    return default.exists() and default.stat().st_mtime > ts


def remove_library_artifact(cfg: Config, doc) -> None:
    """Delete the extracted-markdown directory for a document, if present."""
    if not doc:
        return
    slug = f"{Path(doc['path']).stem}__{doc['sha256'][:8]}"
    d = cfg.library / (doc["dir_path"] or "") / slug
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
    if existing and not reprocess and existing["sha256"] == sha:
        if existing["status"] == "processing":
            # Only skip if ANOTHER live scan owns this document right now;
            # a stale 'processing' (killed by sleep/crash/reboot) is resumed.
            if _scan_lock_held_by_other(cfg) and time.time() - existing["updated_at"] < 600:
                return {"action": "skipped", "filename": path.name, "reason": "already processing"}
            reuse = True  # resume (keep done pages)
        elif existing["status"] == "done":
            if not _prompt_newer_than(path, cfg, existing["updated_at"]):
                return {"action": "skipped", "filename": path.name, "reason": "unchanged"}
            reuse = True
            prompt_changed = True  # prompt edited -> re-extract ALL pages
        else:  # 'error': previous run failed -> resume (keep done pages, retry the rest)
            reuse = True
    if existing and not reuse:
        remove_library_artifact(cfg, existing)
        db.delete_document(conn, existing["id"])
        conn.commit()

    try:
        rel_dir = str(path.parent.relative_to(cfg.dropbox))
    except ValueError:
        rel_dir = ""
    if rel_dir == ".":
        rel_dir = ""
    doc_id = existing["id"] if reuse else db.add_document(
        conn, filename=path.name, path=str(path), sha256=sha,
        size_bytes=size, mtime=mtime, kind=kind, now=now, dir_path=rel_dir,
        palaeographer=palaeographer.id,
    )
    if reuse:
        db.update_document(conn, doc_id, palaeographer=palaeographer.id)
    db.set_document_status(conn, doc_id, "processing")
    conn.commit()

    prompt, prompt_source = resolve_prompt(
        path.stem,
        path if path.is_dir() else path.parent,
        cfg.dropbox, cfg.prompts, explicit_prompt,
    )
    prompt = compose_prompts(palaeographer.prompt_text, prompt)
    force = reprocess or prompt_changed  # only these re-extract already-done pages

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
    if page_errors:
        db.set_document_status(
            conn, doc_id, "error",
            error=f"{len(page_errors)} page(s) failed; first error: {page_errors[0][1]}",
        )
        conn.commit()
        return {"action": "error", "filename": path.name, "error": page_errors[0][1]}

    db.set_document_status(conn, doc_id, "done", prompt_source=prompt_source)
    conn.commit()
    index_document(cfg, conn, client, doc_id, verbose=verbose)
    write_library_artifact(cfg, conn, doc_id)
    return {"action": "ingested", "filename": path.name, "pages": total, "prompt": prompt_source}


def index_document(cfg: Config, conn, client: ModelClient, doc_id: int, verbose: bool = True) -> int:
    pages = db.get_pages(conn, doc_id)
    db.clear_chunks(conn, doc_id)
    items: list[tuple[int, int, str]] = []
    n = 0
    for p in pages:
        for ch in chunk_text(p["raw_text"], cfg.chunk_chars, cfg.chunk_overlap):
            items.append((p["id"], n, ch))
            n += 1
    if not items:
        conn.commit()
        return 0
    if verbose:
        print(f"  indexing {n} chunks ...", flush=True)
    try:
        vecs = client.embed(
            cfg.embed_model,
            [prefixed(cfg.embed_model, t, "doc") for _, _, t in items],
        )
    except ModelError as e:
        vecs = [None] * len(items)
        if verbose:
            print(f"  warning: embeddings unavailable ({e}); indexing text-only")
    for (page_id, chunk_no, text), v in zip(items, vecs):
        db.add_chunk(conn, doc_id, page_id, chunk_no, text, pack(v) if v else None)
    conn.commit()
    return n


def write_library_artifact(cfg: Config, conn, doc_id: int) -> Path | None:
    doc = db.get_document(conn, doc_id)
    pages = db.get_pages(conn, doc_id)
    if not doc:
        return None
    slug = f"{Path(doc['path']).stem}__{doc['sha256'][:8]}"
    rel_dir = Path(doc["dir_path"] or "")
    out_dir = cfg.library / rel_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {doc['filename']}",
        "",
        f"- source: `{doc['path']}`",
        f"- collection: `{doc['dir_path'] or '(root)'}`",
        f"- kind: {doc['kind']}",
        f"- pages: {doc['page_count']}",
        f"- status: {doc['status']}",
        f"- palaeographer: {doc['palaeographer'] or 'default'}",
        f"- prompt: `{doc['prompt_source']}`",
        f"- sha256: {doc['sha256']}",
        "",
    ]
    for p in pages:
        lines.append(f"## Page {p['page_no']}\n")
        lines.append((p["raw_text"] or f"*{p['status']}*").strip())
        lines.append("")
    out = out_dir / f"{doc['filename']}.md"
    out.write_text("\n".join(lines))
    return out


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
    try:
        db.backfill_dir_path(conn, cfg.dropbox)
        files = discover(cfg.dropbox, cfg.dir_documents)
        results = []
        for i, f in enumerate(files, 1):
            if verbose:
                print(f"[{i}/{len(files)}] {f.name}", flush=True)
            results.append(
                ingest_file(cfg, conn, client, f, palaeographer, explicit_prompt, reprocess, verbose)
            )
        return {"scanned": len(files), "results": results}
    finally:
        conn.close()
        _release_scan_lock(cfg)


def reindex_all(cfg: Config, client: ModelClient, verbose: bool = True) -> dict:
    conn = db.connect(cfg.db_path)
    try:
        db.backfill_dir_path(conn, cfg.dropbox)
        docs = [d for d in db.list_documents(conn, limit=10000) if d["status"] == "done"]
        counts = {}
        for d in docs:
            counts[d["id"]] = index_document(cfg, conn, client, d["id"], verbose=verbose)
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
        if not (is_supported(name) or name.endswith(".prompt.md")):
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

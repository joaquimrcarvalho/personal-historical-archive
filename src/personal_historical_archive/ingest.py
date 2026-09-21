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
from . import locks
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
from .model_client import ModelClient, ModelError, ModelStall, PAGE_ENGINES
from .filters import FilterError, filters_changed, filters_signature, write_stamp
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
                       api_style=pal.api_style, deadline_s=pal.deadline_s), pal


def make_editor_client(cfg: Config, editor_id: str) -> tuple[ModelClient, Editor]:
    """Create a ModelClient for an editor (a text model, possibly on a
    different endpoint/model than the palaeographer)."""
    editor = cfg.get_editor(editor_id)
    editor = cfg.resolve_model(editor)  # bind the default model when rules-only
    return ModelClient(editor.base_url, timeout_s=editor.timeout_s, api_key=editor.api_key,
                       api_style=editor.api_style, deadline_s=editor.deadline_s), editor


def _vision_client(pal: Palaeographer) -> ModelClient:
    """Build a ModelClient for an (already resolved/overridden) palaeographer."""
    return ModelClient(pal.base_url, timeout_s=pal.timeout_s, api_key=pal.api_key,
                       api_style=pal.api_style, deadline_s=pal.deadline_s)


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


# ----------------------------------------------------------------- model-server jobs

def _effective_palaeographer(
    cfg: Config, path: Path, default_pal: Palaeographer,
    pal_override: str | None = None, model_override: str | None = None,
    warn: bool = True,
):
    """The palaeographer a scan of `path` will actually use, plus its sidecar.

    The per-run override is AUTHORITATIVE, and its two halves resolve
    independently: `--model X` alone keeps the document's rules, and
    `--palaeographer R` alone keeps the document's model (so a run that swaps in
    an OCR engine for a VLM prompt, or the reverse, is warned about rather than
    silently producing nonsense).
    """
    sidecar = _doc_sidecar(cfg, path)
    pal_id, pal_src = resolve_palaeographer_id(
        path.stem, path if path.is_dir() else path.parent, cfg.dropbox,
        explicit=pal_override,
    )
    model_id = None
    if sidecar.palaeographer:
        if pal_override is None:
            pal_id = sidecar.palaeographer.rules
            pal_src = str(sidecar.source)
        model_id = sidecar.palaeographer.model
    if model_override:
        model_id = model_override
    if pal_id:
        try:
            pal = cfg.get_palaeographer(pal_id)
        except KeyError:
            if warn:
                print(f"  warning: unknown palaeographer {pal_id!r} (from {pal_src}); "
                      f"using default", flush=True)
            pal = default_pal
    else:
        pal = default_pal
    try:
        pal = cfg.resolve_model(pal, model_id)
    except KeyError:
        if warn:
            print(f"  warning: unknown model {model_id or cfg.default_model!r}; "
                  f"leaving {pal.id} unbound", flush=True)
    if warn and pal_override and model_override is None and (pal.engine or "").strip():
        print(f"  warning: {pal.id} resolves to the local '{pal.engine}' engine, which "
              f"ignores the transcription prompt — pass --model to choose a chat/vision "
              f"model for this run", flush=True)
    return pal, sidecar


def _servers_for_document(
    cfg: Config, path: Path, default_pal: Palaeographer | None = None,
    pal_override: str | None = None, model_override: str | None = None,
    warn: bool = True, include_pal: bool = True,
) -> set[str]:
    """The model-server keys a job over `path` may talk to.

    Resolved BEFORE a lock is taken, because the lock must cover every server
    the job may touch — including the server of a per-run override, which is not
    the document's configured one. `include_pal=False` for the EDITOR pass, which
    reads no page and so never talks to the palaeographer's server at all.
    Encoders are not part of scan/edit.
    """
    keys: set[str] = set()
    sidecar = _doc_sidecar(cfg, path)
    if include_pal:
        if default_pal is None:
            try:
                default_pal = cfg.get_palaeographer()
            except KeyError:
                default_pal = None
        if default_pal is not None:
            pal, sidecar = _effective_palaeographer(
                cfg, path, default_pal, pal_override, model_override, warn=warn)
            keys.add(locks.stage_key(pal))
    ed_id: str | None = None
    if sidecar.editor_set:
        ed_id = sidecar.editor.rules if sidecar.editor else None
    else:
        ed_id, _esrc = resolve_editor_id(
            path.stem, path if path.is_dir() else path.parent, cfg.dropbox
        )
    if ed_id and ed_id not in ("null", "passthrough"):
        try:
            keys.add(locks.stage_key(cfg.resolve_model(cfg.get_editor(ed_id))))
        except KeyError:
            pass
    keys.discard("")
    return keys


def _job_keys(
    cfg: Config, paths, embed: bool = True, default_pal: Palaeographer | None = None,
    pal_override: str | None = None, model_override: str | None = None,
    warn: bool = True, include_pal: bool = True,
) -> list[str]:
    """Union of the server keys of a job covering `paths`, plus the embed model.

    An empty key set means the job touches no model server (e.g. an OCR-only
    preview) and needs no lock at all."""
    keys: set[str] = set()
    for p in paths:
        keys |= _servers_for_document(cfg, p, default_pal, pal_override, model_override,
                                      warn=warn, include_pal=include_pal)
    if embed:
        keys.add(locks.embed_key(cfg))
    keys.discard("")
    return sorted(keys)


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


def discover(
    dropbox: Path,
    dir_documents: bool = True,
    root: Path | None = None,
    exclude: list[Path] | None = None,
) -> list[Path]:
    """List document units: individual files plus image-directory documents.

    By default walks the whole `dropbox` tree. Pass `root` to restrict
    discovery to a single subpath (a collection, e.g. the directory of
    dropbox/collections/pfister-notices, or ONE document file such as
    documents/myfile.pdf) so a scan can target that unit instead of the whole
    dropbox. `root` must be inside `dropbox`.

    If `root` itself is a directory-of-images, it is treated as ONE document
    (the folder is the document, its images are pages) — the same rule that
    applies to image-directories below a collection root. This makes
    `--path` to a leaf image-folder behave consistently with `--path` to the
    collection root. A `root` that is a FILE is that file (previously it fell
    through `rglob` and discovered nothing, so `pha scan --path <doc.pdf>`
    silently scanned zero files).

    `exclude` skips any unit at or under one of the given paths (e.g. the
    archive's `inbox` when it is nested inside the dropbox) so parked/on-hold
    documents are never picked up by a scan."""
    exclusions = [p.resolve() for p in (exclude or [])]

    def _excluded(p: Path) -> bool:
        rp = p.resolve()
        return any(rp == ex or ex in rp.parents for ex in exclusions)

    base = dropbox if root is None else root
    if not base.exists():
        return []
    if base.is_file():
        # A single document named directly: it is the unit (mirrors
        # `_documents_under`, which the `pha edit --path` route already uses).
        if not is_supported(base.name) or base.name.startswith("."):
            return []
        return [] if _excluded(base) else [base]
    units: list[Path] = []
    # When scanning a specific root that is itself a document-directory, the
    # whole folder is the document — do not enumerate its images separately.
    if root is not None and dir_documents and base.is_dir() and _is_document_dir(base):
        return [] if _excluded(base) else [base]
    for p in sorted(base.rglob("*")):
        if not p.is_file() or not is_supported(p.name) or p.name.startswith("."):
            continue
        if dir_documents and p.parent != base and _is_document_dir(p.parent):
            continue  # this file is a page of a document-directory
        if _excluded(p):
            continue
        units.append(p)
    if dir_documents:
        for d in sorted(base.rglob("*")):
            if d.is_dir() and d != base and _is_document_dir(d) and not _excluded(d):
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


def _sha_referenced(conn, sha: str) -> bool:
    """True if any registered document still carries this content sha.

    Several documents can share a sha when two files have identical bytes, so a
    render folder must not be dropped while any of them still references it."""
    if not sha:
        return False
    return conn.execute(
        "SELECT 1 FROM documents WHERE sha256 = ? LIMIT 1", (sha,)
    ).fetchone() is not None


def remove_render_dir(cfg: Config, sha: str) -> bool:
    """Delete ``renders/<sha>`` unconditionally (callers must confirm the sha
    is no longer needed). Returns whether a directory was removed."""
    if not sha:
        return False
    d = cfg.renders / sha
    if not d.is_dir():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def remove_render_if_orphaned(cfg: Config, conn, sha: str) -> bool:
    """After dropping a document's DB row, remove its ``renders/<sha>`` folder
    when no other live document still shares that content hash.

    The render cache is keyed by content sha, so a superseded or removed
    document leaves its folder (and its JPEGs) behind. Callers should already
    have committed the document deletion; this helper then verifies the sha is
    unreferenced and deletes only that folder."""
    if _sha_referenced(conn, sha):
        return False
    return remove_render_dir(cfg, sha)


def prune_orphan_renders(cfg: Config, conn, dry_run: bool = False, verbose: bool = True) -> int:
    """Remove ``renders/<sha>`` folders that no registered document references.

    A render folder is keyed by the document's content sha; superseded or
    deleted documents leave their folder behind. This scans the renders dir and
    deletes every subdirectory whose name is not a live document sha. With
    ``dry_run`` it only reports. Returns the number of folders removed (or that
    would be removed in a dry run)."""
    live = {row["sha256"] for row in conn.execute("SELECT sha256 FROM documents") if row["sha256"]}
    removed = 0
    if not cfg.renders.is_dir():
        return 0
    for d in sorted(cfg.renders.iterdir()):
        if not d.is_dir():
            continue
        if d.name in live:
            continue
        if not dry_run:
            shutil.rmtree(d, ignore_errors=True)
        removed += 1
        if verbose:
            print(f"  {'[dry-run] would remove' if dry_run else 'removed'} {d}")
    return removed


def _render_image_count(rdir: Path) -> int:
    """JPEGs present in one `renders/<sha>` cache folder."""
    if not rdir.is_dir():
        return 0
    return sum(1 for f in rdir.iterdir() if f.is_file() and not f.name.startswith("."))


def render_document_pages(cfg: Config, conn, doc_id: int, verbose: bool = True) -> dict:
    """Render the page images MISSING for one document, from its source.

    `renders/<sha>/` is a derived cache keyed by the source content hash. A
    hand-over ships no renders (the worker re-renders to extract) and the return
    leg carries none either, so a document this archive never scanned — handed
    out before its first scan — has no images when its text comes back.
    Re-rendering here, with the same sidecar settings, is cheaper than
    transferring the worker's images and keeps the cache local.

    Never raises: a missing or unreadable source is reported, so a `fetch` that
    has already applied the text is not failed by a render.
    """
    doc = db.get_document(conn, doc_id)
    if doc is None:
        return {"action": "skipped", "reason": "no such document", "doc_id": doc_id}
    path = Path(doc["path"] or "")
    sha = doc["sha256"] or ""
    if not sha or not path.exists():
        return {"action": "skipped", "reason": "source missing", "doc_id": doc_id}
    rdir = cfg.renders / sha
    expected = int(doc["page_count"] or 0)
    have = _render_image_count(rdir)
    if expected and have >= expected:
        return {"action": "skipped", "reason": "already rendered",
                "doc_id": doc_id, "images": have}
    dpi, max_px, jq = effective_render(cfg, _doc_sidecar(cfg, path))
    try:
        if path.is_dir():
            for img in sorted(path.iterdir()):
                if img.is_file() and not img.name.startswith(".") and is_supported(img.name):
                    render_document(img, rdir, dpi, max_px, jq, prefix=img.stem)
        else:
            render_document(path, rdir, dpi, max_px, jq)
    except Exception as e:  # noqa: BLE001 - a render must never fail the caller
        return {"action": "error", "doc_id": doc_id, "error": str(e)}
    n = _render_image_count(rdir)
    if verbose:
        print(f"  rendered {n} page image(s) for #{doc_id} ({doc['filename']})", flush=True)
    return {"action": "rendered", "doc_id": doc_id, "images": n}


# What `write_edited_pages` writes for a page whose edit row has no text yet: the
# absence of a page, not content, so a folder holding only these holds nothing.
_WAITING_STUB = "*waiting*"

# A page that takes longer than this is called out in the scan/edit output, so
# a provider slowing down is visible BEFORE it becomes a stall (report F3).
_SLOW_PAGE_WARN_S = float(os.environ.get("PHA_SLOW_PAGE_S", "600"))


def _page_timing_line(page_no: int, total: int, elapsed: float) -> str:
    """`page N/M: extracted in Xs` — with a warning marker when it was slow."""
    mark = "  ! slow page: " if elapsed >= _SLOW_PAGE_WARN_S else "  "
    return f"{mark}page {page_no}/{total}: done in {elapsed:.1f}s"


def _pages_list(pages) -> str:
    """Compact `1, 4, 7-9` rendering of page numbers for a report line."""
    nums = sorted({int(p) for p in pages})
    if not nums:
        return ""
    spans: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        spans.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    spans.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(spans)


def _db_edited_bodies(conn, doc_id: int, editor: str) -> dict[str, str]:
    """``{page file name: the text pha wrote for it}`` for one document + editor.

    Mirrors ``write_edited_pages`` exactly — including the ``*waiting*`` stub for
    an empty or failed row — so a library file can be compared to what the
    database holds.
    """
    out: dict[str, str] = {}
    for r in conn.execute(
        "SELECT pe.text, p.page_no, p.source_name FROM page_edits pe "
        "JOIN pages p ON p.id = pe.page_id "
        "WHERE p.document_id = ? AND pe.editor = ?",
        (doc_id, editor),
    ):
        name = f"{r['source_name']}.md" if r["source_name"] else f"page-{r['page_no']:03d}.md"
        out[name] = ((r["text"] or "") or _WAITING_STUB).strip()
    return out


def _library_body(path: Path) -> str:
    """A library page file's text without its front matter ("" if unreadable)."""
    return (((_parse_library_file(path) or (None, ""))[1]) or "").strip()


def prune_redundant_edited_dirs(cfg: Config, conn, dry_run: bool = False,
                                verbose: bool = True, doc_id: int | None = None) -> dict:
    """Delete a bare ``edited-<rules>`` folder that is provably a duplicate.

    The duplicate-edited-variants bug wrote one variant into two folders:
    ``edited-<rules>`` (while ``documents.editor_model`` was transiently NULL)
    and ``edited-<rules>@<model>``. Resolution now always prefers the qualified
    folder, so the bare one is inert — but it still occupies disk and misleads
    anyone reading ``library/`` by hand.

    A bare folder is deleted **only when nothing lives in it alone**. That is
    proved two ways, and either is enough:

    * every page file with text is exactly what the DB holds for that document +
      editor (so ``pha export`` regenerates it), or
    * every page file with text is body-identical to the same file in a
      model-qualified sibling (so that folder keeps it byte for byte).

    A ``*waiting*`` stub counts as *no page at all*: a folder that is nothing but
    placeholders holds nothing to preserve, and stubs never block a delete (docs
    47/50 on jesuit-archive are 610/627 stubs around a few real pages, all of
    which the qualified folder also has). Anything else is a different reading —
    a whole alternate OCR pass, one page of another translation generation, or a
    page the DB has since moved on from — and is reported, never deleted.

    Only a bare folder with a model-qualified sibling is considered — a
    bare-only variant is the document's only edited output — and never one that
    *is* the document's current model-less output. ``doc_id`` narrows the sweep
    to one document, like ``pha review --doc N``. Returns
    ``{"removed": [...], "refused": [...], "failed": [...], "kept_bare_only": n,
    "bytes": n}``; a folder that could not be deleted is reported in ``failed``
    rather than counted as removed.
    """
    from .addresses import parse_variant

    removed: list[dict] = []
    refused: list[dict] = []
    failed: list[dict] = []
    kept_bare_only = 0
    total_bytes = 0
    if doc_id is None:
        docs = db.list_documents(conn, limit=100000)
    else:
        one = db.get_document(conn, int(doc_id))
        docs = [one] if one else []
    for doc in docs:
        doc_dir = _library_doc_dir(cfg, doc)
        if doc_dir is None or not doc_dir.is_dir():
            continue
        groups: dict[str, list[str]] = {}
        for entry in sorted(doc_dir.iterdir()):
            parsed = parse_variant(entry.name)
            if entry.is_dir() and parsed and parsed[0] == "edited":
                groups.setdefault(parsed[1], []).append(entry.name)
        for editor, names in sorted(groups.items()):
            bare = sorted(n for n in names if parse_variant(n)[2] is None)
            qualified = sorted(n for n in names if parse_variant(n)[2])
            if not bare:
                continue
            if not qualified:
                kept_bare_only += len(bare)  # the only edited output — keep
                continue
            # A model-less current editor writes the bare name: its qualified
            # sibling is the OLDER reading, so this folder must stay.
            if (_doc_field(doc, "editor") or None) == editor \
                    and not (_doc_field(doc, "editor_model") or None):
                refused.append({"path": str(doc_dir / bare[0]), "document_id": doc["id"],
                                "reason": "current model-less variant"})
                if verbose:
                    print(f"  keep     {doc_dir / bare[0]}  (the current model-less variant)")
                continue
            bodies = _db_edited_bodies(conn, doc["id"], editor)
            twins = [doc_dir / n for n in qualified]
            for name in bare:
                folder = doc_dir / name
                files = sorted(folder.glob("*.md"))
                if not files:
                    refused.append({"path": str(folder), "document_id": doc["id"],
                                    "reason": "no page files to compare",
                                    "differing": 0, "pages": 0})
                    continue
                local = {f.name: _library_body(f) for f in files}
                # A `*waiting*` stub is the ABSENCE of a page, not content: there
                # is nothing in it to preserve, so it never blocks a delete. Docs
                # 47/50 on jesuit-archive are 610/627 stubs wrapped around a
                # handful of real pages.
                real = {n: b for n, b in local.items() if b and b != _WAITING_STUB}
                from_db = all(bodies.get(n) == b for n, b in real.items())
                twin = next((t.name for t in twins if all(
                    (t / n).is_file() and _library_body(t / n) == b
                    for n, b in real.items())), None)
                if real and not from_db and twin is None:
                    differing = sum(1 for n, b in real.items() if bodies.get(n) != b)
                    refused.append({"path": str(folder), "document_id": doc["id"],
                                    "reason": "content lives nowhere else",
                                    "differing": differing, "pages": len(files)})
                    if verbose:
                        print(f"  keep     {folder}  ({differing}/{len(real)} real page(s) differ "
                              f"from the database and no sibling holds them — inspect by hand)")
                    continue
                nbytes = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
                stubs = len(local) - len(real)
                if not real:
                    why = f"only {stubs} placeholder(s)"
                elif from_db:
                    why = ("identical to the database" if not stubs
                           else f"{len(real)} real page(s) identical to the database, {stubs} stub(s)")
                else:
                    why = f"identical to {twin}"
                if not dry_run:
                    # Never swallow the error: a sweep that reports "removed" while
                    # the folder is still there is worse than no sweep at all.
                    try:
                        shutil.rmtree(folder)
                    except OSError as e:
                        failed.append({"path": str(folder), "document_id": doc["id"],
                                       "reason": str(e)})
                        if verbose:
                            print(f"  FAILED   {folder}  could not delete: {e}")
                        continue
                    if folder.exists():
                        failed.append({"path": str(folder), "document_id": doc["id"],
                                       "reason": "still present after rmtree"})
                        if verbose:
                            print(f"  FAILED   {folder}  still present after delete")
                        continue
                removed.append({"path": str(folder), "document_id": doc["id"], "editor": editor,
                                "pages": len(files), "bytes": nbytes, "why": why})
                total_bytes += nbytes
                if verbose:
                    print(f"  {'[dry-run] would remove' if dry_run else 'removed '} {folder}"
                          f"  ({len(files)} pages, {nbytes / 1e6:.1f} MB — {why})")
    return {"removed": removed, "refused": refused, "failed": failed,
            "kept_bare_only": kept_bare_only, "bytes": total_bytes}


def _doc_field(doc, key):
    """``doc[key]`` or None — a row/dict without that column is not an error."""
    try:
        return doc[key]
    except (IndexError, KeyError, TypeError):
        return None


def _variant_dirs(base: Path, variant: str) -> list[str]:
    """The directory names under `base` that are ONE logical variant.

    ``edited-french-ocr`` and ``edited-french-ocr@deepseek-v4-flash`` are the
    same variant — the bare name only records that the model was unknown when
    it was written (see ``enhancements/pha-duplicate-edited-variants-bug-report.md``).
    A name outside the grammar falls back to the literal directory.
    """
    from .addresses import parse_variant

    parsed = parse_variant(variant)
    if parsed is None:
        return [variant] if (base / variant).is_dir() else []
    stage, ident = parsed[0], parsed[1]
    out: list[str] = []
    for d in sorted(base.iterdir()):
        p = parse_variant(d.name)
        if d.is_dir() and p and p[0] == stage and p[1] == ident:
            out.append(d.name)
    return out


def _pick_pages_dir(base: Path, variant: str, doc) -> Path | None:
    """The library directory for one variant, never the older bare alias."""
    from .addresses import pick_variant

    stage = variant.split("-", 1)[0] if variant else ""
    ident = variant.split("-", 1)[1] if "-" in variant else variant
    cur_id = _doc_field(doc, "editor" if stage == "edited" else "palaeographer")
    cur_model = _doc_field(
        doc, "editor_model" if stage == "edited" else "palaeographer_model")
    picked = pick_variant(_variant_dirs(base, variant), want_model=cur_model,
                          known=(cur_id == ident and bool(ident)))
    return (base / picked) if picked else None


def library_page_path(
    cfg: Config,
    doc,
    page_no: int,
    variant: str = "raw",
    source_name: str | None = None,
    editor_id: str | None = None,
) -> Path | None:
    """Locate the library page file for (doc, page) on disk.

    Returns the exact path when the file exists (None otherwise). ``variant``
    is 'raw' (``transcription-<pal>[@model]/``) or 'edited'
    (``edited-<editor>[@model]/``). The dated document folder is found via the
    document's slug, falling back to the newest matching ``<stem>_*`` folder."""
    if not doc:
        return None
    if not isinstance(doc, dict):  # accept sqlite3.Row too
        try:
            doc = dict(doc)
        except (TypeError, ValueError):
            return None
    stem = Path(doc["path"]).stem
    rel = Path(doc["dir_path"] or "")
    base = cfg.library / rel
    doc_dir = base / _doc_slug(doc)
    if not doc_dir.exists():
        folders = sorted(base.glob(f"{stem}_*"), key=lambda p: p.stat().st_mtime
                         if p.is_dir() else 0.0)
        if not folders:
            return None
        doc_dir = folders[-1]
    if variant == "edited":
        ed = editor_id or doc.get("editor") or None
        if not ed:
            return None
        variant_name = f"edited-{ed}"
    else:
        pal = doc.get("palaeographer") or "default"
        variant_name = f"transcription-{pal}"
    # The bare name and its `@<model>` sibling are ONE variant (the bare one only
    # means the model was unknown when it was written): the directory matching
    # the document's recorded model wins, and the older bare alias never does.
    variant_dir = _pick_pages_dir(doc_dir, variant_name, doc)
    if variant_dir is None:
        return None
    name = f"{source_name}.md" if source_name else f"page-{page_no:03d}.md"
    f = variant_dir / name
    return f if f.exists() else None


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
    pages: set[int] | None = None,
    pin: bool = True,
) -> dict:
    """Extract one document (or, with `pages`, re-read exactly those pages).

    `pages` is the targeted re-read (`pha scan --page N`): only those pages are
    rendered and transcribed, with `palaeographer` as the AUTHORITATIVE pair for
    them; the document-level config and every other page are left alone. Each
    re-read page records its own provenance and (unless `pin=False`) is pinned,
    so a later bulk pass cannot discard it. A page whose transcription is
    human-`reviewed` is refused, never overwritten."""
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
    # The document's CONFIGURED pair, for the before → after report of a page
    # re-read (it is what the untouched pages still use).
    doc_pal_id = existing["palaeographer"] if existing else None
    doc_pal_model = existing["palaeographer_model"] if existing else None
    prior_status = existing["status"] if existing else None
    prior_error = existing["error"] if existing else None
    # ---- page-scoped re-read: cheap pre-checks BEFORE the row is touched ----
    # An out-of-range page must not flip a finished document to 'error'.
    if pages is not None:
        if not pages:
            return {"action": "error", "filename": path.name,
                    "error": "--page needs at least one page number"}
        if existing is None:
            return {"action": "error", "filename": path.name,
                    "error": "not scanned yet — run `pha scan --path <doc>` first, "
                             "then re-read the page"}
        if path.is_dir():
            hint = len([f for f in path.iterdir()
                        if f.is_file() and is_supported(f.name) and not f.name.startswith(".")])
        else:
            hint = page_count(path)
        bad = sorted(n for n in pages if n < 1 or n > hint)
        if bad:
            return {"action": "error", "filename": path.name,
                    "error": f"page(s) {_pages_list(bad)} out of range (1-{hint})"}
    reuse = False
    prompt_changed = False
    if existing and existing["sha256"] == sha:
        # Same file version. Reuse the document row so parallel outputs survive:
        # previous palaeographer transcriptions, editor outputs and records
        # stay in the library folder and DB (staleness marks them for
        # regeneration, never deletion). --reprocess forces re-extraction of
        # every page but must NOT delete the document.
        if pages is not None:
            # A targeted page re-read works on the existing row whatever the
            # document-level staleness says: the user named the page, so the
            # selected pages are always re-read (and the pin, not the staleness
            # rule, is what protects the pages nobody named).
            reuse = True
            prompt_changed = True
        elif reprocess:
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
            # A changed palaeographer FILTER chain re-runs pages whose stored
            # signature differs (checked per page below). It must also clear
            # this document-level early return, which happens before the loop.
            filters_stale = (
                _stored_page_filters(conn, existing["id"], sample=1)
                not in _acceptable_filters_signature(
                    cfg, sidecar.palaeographer.post if sidecar.palaeographer is not None else [])
            )
            changed = prompt_newer or pal_changed or filters_stale
            if existing["status"] == "processing":
                # Only skip if ANOTHER live scan owns this document right now;
                # a stale 'processing' (killed by sleep/crash/reboot) is resumed.
                if locks.job_running(cfg, locks.embed_key(cfg)) and time.time() - existing["updated_at"] < 600:
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
        # disk untouched; only the stale DB row is replaced. The old render
        # folder (also keyed by the old sha) is orphaned, so drop it now
        # unless another live document still shares that content hash.
        old_sha = existing["sha256"]
        db.delete_document(conn, existing["id"])
        conn.commit()
        remove_render_if_orphaned(cfg, conn, old_sha)

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
        if pages is None:
            db.update_document(conn, doc_id, palaeographer=palaeographer.id, editor=ed_id,
                               palaeographer_model=palaeographer.model_ref or None)
        elif ed_id is not None:
            # A page re-read must NOT re-configure the document: the other pages
            # keep the configured pair, and so does the document row.
            db.update_document(conn, doc_id, editor=ed_id)
    db.set_document_status(conn, doc_id, "processing")
    conn.commit()

    prompt, prompt_source = resolve_prompt(
        path.stem,
        path if path.is_dir() else path.parent,
        cfg.dropbox, cfg.prompts, explicit_prompt,
    )
    prompt = compose_prompts(palaeographer.prompt_text, prompt)
    force = reprocess or prompt_changed or pages is not None  # only these re-extract already-done pages
    db.update_document(conn, doc_id, prompt_source=prompt_source)
    conn.commit()
    write_document_pages(cfg, conn, doc_id)  # visible output even while processing

    render_dpi, max_image_px, jpeg_quality = effective_render(cfg, sidecar)
    selection = sorted(pages) if pages is not None else None
    try:
        source_names: list[str | None] = []
        if path.is_dir():
            images = [f for f in sorted(path.iterdir())
                      if f.is_file() and is_supported(f.name) and not f.name.startswith(".")]
            total = len(images)
            chosen = selection if selection is not None else list(range(1, total + 1))
            renders = []
            page_numbers: list[int] = []
            for n in chosen:
                img = images[n - 1]
                out = render_document(img, cfg.renders / sha, render_dpi,
                                      max_image_px, jpeg_quality, prefix=img.stem)
                # a single image renders to one page: map each render to the
                # source image stem (e.g. 505V) for file naming.
                renders += out
                source_names += [img.stem] * len(out)
                page_numbers += [n] * len(out)
        else:
            total = page_count(path)
            rendered = render_document(path, cfg.renders / sha, render_dpi, max_image_px,
                                       jpeg_quality,
                                       pages=set(selection) if selection is not None else None)
            # render_document keeps the ABSOLUTE page index in the file name
            # (p001.jpg), so a partial render still maps back to its page.
            rendered = sorted(rendered, key=lambda p: int(p.stem[1:]))
            renders = rendered
            page_numbers = [int(p.stem[1:]) for p in rendered]
            source_names = [None] * len(rendered)
    except Exception as e:
        db.set_document_status(conn, doc_id, "error", error=f"render failed: {e}")
        conn.commit()
        return {"action": "error", "filename": path.name, "error": f"render failed: {e}"}
    db.update_document(conn, doc_id, page_count=total)

    page_errors: list[tuple[int, str]] = []
    consecutive_failures = 0
    # The filter chain this scan would apply, as a signature. A page whose
    # stored signature differs was produced by a different chain (an edited
    # filter, changed params, or a filter added/removed) and is re-extracted
    # even without --reprocess — the same "editing rules re-runs the stage"
    # rule the prompt/model files follow.
    acceptable_filters = _acceptable_filters_signature(
        cfg, sidecar.palaeographer.post if sidecar.palaeographer is not None else [])
    page_errors: list[tuple[int, str]] = []
    stalled: list[int] = []  # pages abandoned for THIS pass by a model stall
    kept_pinned: list[int] = []      # bulk pass: pages a targeted re-read pinned
    refused_reviewed: list[int] = []  # explicit --page naming a human-corrected page
    details: list[dict] = []          # per-page before -> after, for the report
    for idx, (img, i) in enumerate(zip(renders, page_numbers)):
        src_name = source_names[idx] if idx < len(source_names) else None
        page_id = db.add_page(conn, doc_id, i, source_name=src_name)
        page = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if page["reviewed_at"]:
            # A human corrected this page. An explicit --page is REFUSED (and
            # named) rather than silently ignored; a bulk pass just leaves it.
            if pages is not None:
                refused_reviewed.append(i)
                if verbose:
                    print(f"  page {i}/{total}: human-reviewed — not re-read; run "
                          f"`pha review --unset --doc {doc_id} --page {i}` to release it",
                          flush=True)
            continue  # a human corrected this page; never re-extract over it
        if pages is None and page["pinned_at"]:
            # A deliberate re-read of this page with another model: a bulk pass
            # (or --reprocess, or a changed collection config) must not discard
            # it. `--page N` re-reads it on purpose; `--unpin` releases it.
            kept_pinned.append(i)
            continue
        if page["status"] == "done" and not force:
            if not filters_changed(page["filters"], acceptable_filters):
                continue  # resume: keep already-extracted pages
            if verbose:
                print(f"  page {i}/{total}: filters changed, re-extracting ...", flush=True)
        prompt_txt = build_page_prompt(prompt, path.name, i, total)
        if verbose:
            print(f"  page {i}/{total}: extracting ...", flush=True)
        started = time.monotonic()
        before_chars = len(page["raw_text"] or "")
        try:
            text = transcribe_page(client, palaeographer, prompt_txt, img,
                                   source=path, page_no=i, total=total)
            # palaeographer.post filters shape the raw text BEFORE it is stored;
            # a filter failure must not store a partially filtered page. The
            # applied chain is recorded so editing a filter re-runs this page.
            ran: list = []
            if sidecar.palaeographer is not None and sidecar.palaeographer.post:
                text, ran = _run_stage_filters(
                    cfg, sidecar.palaeographer.post, hook="palaeographer.post",
                    value=text, conn=conn, path=path, doc_id=doc_id, stage="palaeographer",
                    page=i, source_name=src_name,
                    verbose=verbose, return_ran=True,
                )
            db.set_page_result(conn, page_id, raw_text=text,
                               filters=filters_signature(ran))
            if pages is not None:
                # Per-page provenance, and the pin that protects it from a later
                # bulk pass: this page was read by the override pair, not by the
                # document's configured one.
                db.set_page_provenance(conn, page_id,
                                       palaeographer=palaeographer.id,
                                       model=palaeographer.model_ref or None,
                                       pinned=pin)
                details.append({
                    "page": i,
                    "from": {"palaeographer": page["palaeographer"] or doc_pal_id,
                             "model": page["palaeographer_model"] or doc_pal_model},
                    "to": {"palaeographer": palaeographer.id,
                           "model": palaeographer.model_ref or None},
                    "chars": {"before": before_chars, "after": len(text or "")},
                    "pinned": bool(pin),
                })
            consecutive_failures = 0
            if verbose:
                print(_page_timing_line(i, total, time.monotonic() - started), flush=True)
        except ModelStall as e:
            # The provider stopped answering (pha's wall-clock deadline). This
            # is NOT a page failure: abandon the page for THIS pass — leave its
            # row pending, record no error, and do not advance the
            # consecutive-failure abort — so a later pass resumes exactly the
            # abandoned pages (report F1/F3 + the batch behaviour G4).
            stalled.append(i)
            if verbose:
                print(f"  page {i}/{total}: STALLED after "
                      f"{time.monotonic() - started:.1f}s — abandoned for this pass "
                      f"({e})", flush=True)
        except (ModelError, FilterError) as e:
            db.set_page_result(conn, page_id, error=str(e))
            page_errors.append((i, str(e)))
            consecutive_failures += 1
            if verbose:
                print(f"  page {i}/{total}: FAILED after "
                      f"{time.monotonic() - started:.1f}s: {e}", flush=True)
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
    if pages is not None:
        # ---- page-scoped finish: editor + incremental index, THESE pages ----
        edited_pages = 0
        for pno in sorted(pages):
            res = edit_document(cfg, conn, doc_id, verbose=verbose, page_no=pno)
            edited_pages += int(res.get("pages") or 0)
        index_error = None
        try:
            index_document(cfg, conn, doc_id, verbose=verbose, pages=set(pages))
        except ModelError as e:
            index_error = str(e)
            if verbose:
                print(f"  ! page(s) re-read but not re-indexed: {e} — run "
                      f"`pha reindex --doc {doc_id}`", flush=True)
        write_document_pages(cfg, conn, doc_id)
        # A page fix does not make an INCOMPLETE document complete: restore the
        # status it had. A failed page keeps its old text (the error branch of
        # set_page_result does not touch raw_text) and is marked 'waiting' so the
        # next scan retries it; a stalled page is left pending the same way.
        if prior_status:
            db.set_document_status(conn, doc_id, prior_status, error=prior_error)
        conn.commit()
        return {"action": "rescanned", "filename": path.name,
                "pages": sorted(pages), "prompt": prompt_source,
                "edited_pages": edited_pages, "refused_reviewed": refused_reviewed,
                "index_error": index_error, "stalled": sorted(stalled),
                "failed": [{"page": p, "error": e} for p, e in page_errors],
                "details": details}

    if page_errors:
        db.set_document_status(
            conn, doc_id, "error",
            error=f"{len(page_errors)} page(s) failed; first error: {page_errors[0][1]}",
        )
        conn.commit()
        return {"action": "error", "filename": path.name, "error": page_errors[0][1]}

    edit_document(cfg, conn, doc_id, verbose=verbose)  # editor pass (skips if none configured)
    index_document(cfg, conn, doc_id, verbose=verbose)  # indexes raw + edited variants
    write_document_pages(cfg, conn, doc_id)
    if kept_pinned and verbose:
        # Not silent: a bulk pass deliberately leaves pages a targeted re-read
        # pinned. `--unpin` (or naming the page with `--page`) releases them.
        print(f"  kept {len(kept_pinned)} pinned page(s) (re-read earlier with a chosen "
              f"model): {_pages_list(kept_pinned)}", flush=True)
    if stalled:
        # Some pages were abandoned by a stall. What WAS read is edited and
        # indexed (so the progress is searchable), but the document is left
        # `processing` -- never `done` -- because it is incomplete: the next
        # `pha scan` resumes exactly the abandoned pages and nothing else.
        if verbose:
            print(f"  {len(stalled)} page(s) abandoned for this pass (model stalled): "
                  f"{_pages_list(stalled)} of {total} — re-run `pha scan` to retry "
                  f"them; nothing was recorded as failed", flush=True)
        return {"action": "stalled", "filename": path.name, "pages": total,
                "stalled": sorted(stalled), "prompt": prompt_source,
                "status": "processing", "kept_pinned": kept_pinned}
    # `done` is written LAST, after the editor and the indexer have run. A crash
    # in either used to leave a healthy-looking row -- `done`, no edited variant,
    # 0 chunks (doc 57, documenta-indica, 2026-09-15) -- which every status-only
    # check reported as success. Now such a document stays 'processing' and the
    # next scan resumes it.
    db.set_document_status(conn, doc_id, "done", prompt_source=prompt_source)
    conn.commit()
    return {"action": "ingested", "filename": path.name, "pages": total,
            "prompt": prompt_source, "kept_pinned": kept_pinned}


def index_document(
    cfg: Config, conn, doc_id: int, embed_client: ModelClient | None = None,
    verbose: bool = True, incremental: bool = True, pages=None,
) -> int:
    """Index BOTH variants when an editor is configured: the raw transcription
    (variant='raw') and the editor's output (variant='edited'), so searches
    hit either the faithful or the modernized/translated text.

    **Incremental by default.** A chunk whose text is byte-identical to the one
    already stored, and whose stored vector was produced by the embed model now
    configured (`chunks.embed_model`), is REUSED instead of re-embedded. So
    re-indexing after a one-page correction embeds that page's chunks and
    nothing else, instead of the whole volume (the reported limitation:
    rewriting one page used to re-embed every page of the document). Pass
    `incremental=False` to force a full re-embed — what `pha reindex --force`
    does. A chunk whose `embed_model` is unknown (a row written before that
    column existed) or different from the current model is never reused, so
    switching embed model still re-embeds everything.

    `pages` (a set of 1-based page numbers) scopes the pass to those pages:
    only their chunks are replaced, and every other page keeps its chunk rows.
    `pha reindex --doc N --page P` uses this.

    **Embeddings are computed BEFORE the existing chunks are cleared.** A
    re-index replaces a document's chunks wholesale (`clear_chunks` then
    insert), so a failed embed used to leave the document with its vectors
    silently deleted and only text-only chunks in their place — `status=done`
    with no error, invisible except in the embedded-chunk count.

    The rule now:

    - a document that ALREADY has vectors, whose embed fails, is left
      completely untouched: its chunks are not cleared and the `ModelError`
      propagates, so the caller can report it and a retry can succeed. One
      transient embed failure can no longer destroy a finished index.
    - a document with no vectors yet (a fresh ingest indexed while the embed
      endpoint is down) still degrades to text-only with a warning, so
      `pha scan` keeps its zero-config behaviour.
    """
    all_pages = db.get_pages(conn, doc_id)
    if pages is None:
        selected = list(all_pages)
    else:
        wanted = {int(p) for p in pages}
        selected = [p for p in all_pages if p["page_no"] in wanted]
    doc = db.get_document(conn, doc_id)
    edited: dict[int, str] = {}
    if doc and doc["editor"]:
        edited = _edited_texts(conn, doc_id, doc["editor"])
    items: list[tuple[int, int, str, str]] = []  # (page_id, chunk_no, text, variant)
    n = 0
    for p in selected:
        for ch in chunk_text(p["raw_text"], cfg.chunk_chars, cfg.chunk_overlap):
            items.append((p["id"], n, ch, "raw"))
            n += 1
        if p["id"] in edited:
            for ch in chunk_text(edited[p["id"]], cfg.chunk_chars, cfg.chunk_overlap):
                items.append((p["id"], n, ch, "edited"))
                n += 1
    selected_ids = [p["id"] for p in selected]
    scope_ids = None if pages is None else selected_ids
    # Loss is only possible when the rows we are about to replace carry vectors.
    # A forced (non-incremental) pass re-embeds everything, so it only needs the
    # cheap existence test — not every stored blob.
    if incremental:
        old = db.existing_chunks(conn, doc_id, scope_ids)
        strict = any(r["embedding"] is not None for r in old)
    else:
        old = []
        strict = db.any_embedded_chunk(conn, doc_id, scope_ids)
    # Reuse pool: (page_id, variant) -> {text: [stored vectors]}, limited to
    # vectors the CURRENT embed model produced (an unknown/different model is
    # never reused — see the docstring).
    pool: dict[tuple[int, str], dict[str, list[bytes]]] = {}
    if incremental and cfg.embed_model:
        for r in old:
            if r["embedding"] is None or r["embed_model"] != cfg.embed_model:
                continue
            pool.setdefault((r["page_id"], r["variant"]), {}) \
                .setdefault(r["text"], []).append(r["embedding"])
    blobs: list[bytes | None] = [None] * len(items)
    to_embed: list[tuple[int, str]] = []  # (index into items, prefixed text)
    for i, (page_id, _no, text, variant) in enumerate(items):
        reused = pool.get((page_id, variant), {}).get(text)
        if reused:
            blobs[i] = reused.pop()
        else:
            to_embed.append((i, prefixed(cfg.embed_model, text, "doc")))
    if not items:
        if pages is None:
            db.clear_chunks(conn, doc_id)
        else:
            db.clear_chunks_for_pages(conn, doc_id, selected_ids)
        conn.commit()
        return 0
    if not to_embed:
        # Nothing to embed: if the stored rows already match, this is a true
        # no-op (keep the rows, their ids and their vectors untouched).
        same = len(old) == len(items) and sorted(
            (r["page_id"], r["chunk_no"], r["text"], r["variant"]) for r in old
        ) == sorted(items)
        if same:
            if verbose:
                print(f"  indexing {len(items)} chunks (all reused; index already current)",
                      flush=True)
            return len(items)
    if verbose:
        print(f"  indexing {len(items)} chunks ({len(to_embed)} to embed, "
              f"{len(items) - len(to_embed)} reused) ...", flush=True)
    if to_embed:
        close_embed = False
        if embed_client is None:
            embed_client = ModelClient(cfg.embed_base_url, timeout_s=cfg.embed_timeout_s)
            close_embed = True
        try:
            new_vecs = embed_client.embed(
                cfg.embed_model,
                [t for _i, t in to_embed],
                batch_size=cfg.embed_batch_size,
            )
        except ModelError as e:
            if strict:
                # There are vectors to lose: never trade a working index for a
                # text-only one. Propagate so the caller reports it and the whole
                # document is retried later (the chunks above are still intact).
                raise ModelError(
                    f"embeddings unavailable ({e}); document #{doc_id} left unchanged "
                    "rather than dropping its stored vectors — re-run once the embed "
                    "model is available"
                ) from e
            # Nothing to lose yet (a fresh ingest with the endpoint down): keep
            # `pha scan`'s zero-config behaviour and index text-only.
            new_vecs = [None] * len(to_embed)
            if verbose:
                print(f"  warning: embeddings unavailable ({e}); indexing text-only")
        finally:
            if close_embed:
                embed_client.close()
        for (i, _t), v in zip(to_embed, new_vecs):
            blobs[i] = pack(v) if v else None
    # Only now is the old index replaced, with the new vectors already in hand.
    if pages is None:
        db.clear_chunks(conn, doc_id)
    else:
        db.clear_chunks_for_pages(conn, doc_id, selected_ids)
    for (page_id, chunk_no, text, variant), blob in zip(items, blobs):
        db.add_chunk(conn, doc_id, page_id, chunk_no, text, blob, variant,
                     embed_model=cfg.embed_model if blob else None)
    conn.commit()
    return len(items)


def _library_dir_for(cfg: Config, conn, doc_id: int) -> Path | None:
    """The document's current library version folder (`library/<dir>/<slug>`)."""
    doc = db.get_document(conn, doc_id)
    if not doc:
        return None
    return cfg.library / Path(doc["dir_path"] or "") / _doc_slug(doc)


def _stage_filter_dirs(cfg: Config, conn, doc_id: int) -> tuple[Path | None, Path | None]:
    """(raw pages dir, edited pages dir) for a document, for the filter context.

    Both are best-effort: a filter that does not need them sees null.
    """
    raw = edited = None
    doc = db.get_document(conn, doc_id)
    if not doc:
        return None, None
    pal = doc["palaeographer"] or "default"
    raw = _pages_dir_for(cfg, doc, f"transcription-{pal}")
    ed = doc["editor"]
    if ed:
        edited = _pages_dir_for(cfg, doc, f"edited-{ed}")
    return raw, edited


def _run_stage_filters(cfg: Config, specs, *, hook: str, value, conn, path: Path,
                       doc_id: int, stage: str, page: int | None = None,
                       source_name: str | None = None, encoder: str | None = None,
                       library_dir: Path | None = None,
                       pages_dir_edited: Path | None = None,
                       records_file: Path | None = None,
                       concatenated_file: Path | None = None,
                       verbose: bool = True, return_ran: bool = False):
    """Apply a stage's filter chain; returns the new value (or `(value, ran)`).

    Builds the documented context object (FILTERS_PLAN.md §2.3) and delegates
    to `filters.apply_filters`. Raises `FilterError` on failure — the caller
    must then discard the unit, so nothing partially filtered is ever stored.

    With `return_ran=True` returns ``(value, ran)`` where `ran` is the list of
    applied filters ({name, params, sha}) for provenance/staleness; otherwise
    just the value.
    """
    from .filters import apply_filters, build_context

    raw_dir, edited_dir = _stage_filter_dirs(cfg, conn, doc_id)
    if pages_dir_edited is not None:
        edited_dir = pages_dir_edited
    doc = db.get_document(conn, doc_id)
    doc_dict = dict(doc) if doc else {}
    if library_dir is None:
        library_dir = _library_dir_for(cfg, conn, doc_id)
    sidecar_file = None
    try:
        sc = resolve_sidecar(cfg.dropbox, path if path.is_dir() else path.parent,
                             stem=None if path.is_dir() else path.stem)
        sidecar_file = sc.source
    except Exception:  # noqa: BLE001 - provenance only; never break a run for it
        sidecar_file = None
    ctx = build_context(
        cfg=cfg, document=doc_dict, stage=stage, hook=hook,
        kind="records" if hook == "encoder.post" else "text",
        params={}, inputs={}, page=page, source_name=source_name, encoder=encoder,
        library_dir=library_dir, pages_dir_raw=raw_dir, pages_dir_edited=edited_dir,
        records_file=records_file, concatenated_file=concatenated_file,
        sidecar=sidecar_file,
    )
    value, ran = apply_filters(value, specs, hook=hook, ctx=ctx,
                               filters_dir=cfg.filters_dir, verbose=verbose)
    return (value, ran) if return_ran else value


def _filter_chain_signatures(cfg: Config, specs) -> tuple[str, str]:
    """(resolved, declared) signatures for a filter chain, without running it.

    `resolved` merges each filter manifest's declared `params:` with the
    sidecar's per-use overrides — EXACTLY what `apply_filters()` records on a
    page. `declared` uses the sidecar params alone, which is what pha computed
    before this was fixed: a filter whose manifest declares `params:` and whose
    sidecar omits them (the normal spelling, `post: [my-filter]`) therefore read
    as "changed" on every pass and re-ran the stage forever. `declared` is kept
    only so a stored value still in the old spelling counts as unchanged (via
    `filters_changed`), which avoids one mass re-run of the whole archive.
    """
    from .filters import filter_sha, filters_signature, load_filter, resolve_params
    resolved: list = []
    declared: list = []
    for spec in specs or []:
        try:
            f = load_filter(cfg.filters_dir, spec.name)
        except FilterError:
            # a broken/missing filter: fold its name in so the stage re-runs
            # once it is fixed, instead of silently looking unchanged
            resolved.append({"name": spec.name, "sha": "missing", "params": spec.params})
            declared.append({"name": spec.name, "sha": "missing", "params": spec.params})
            continue
        sha = filter_sha(f)
        resolved.append({"name": f.name, "sha": sha, "params": resolve_params(f, spec)})
        declared.append({"name": f.name, "sha": sha, "params": dict(spec.params)})
    return filters_signature(resolved), filters_signature(declared)


def _configured_filters_signature(cfg: Config, specs) -> str:
    """The signature of a filter chain as configured NOW, without running it.

    This is the RESOLVED form (manifest defaults merged with the sidecar params)
    — the same signature `apply_filters()` stores, so a value it produced
    compares equal. Used wherever the signature is written down.
    """
    return _filter_chain_signatures(cfg, specs)[0]


def _acceptable_filters_signature(cfg: Config, specs) -> tuple[str, ...]:
    """Every signature a stored value may carry for this chain to count as
    unchanged: the resolved form, plus the legacy declared-params form.

    Comparing against BOTH means the fix above does not re-run every page once,
    and is safe: a real change (edited filter, changed manifest or sidecar
    params, added/removed filter) changes the sha or the params in both forms,
    so the stored value matches neither and the stage does re-run.
    """
    return _filter_chain_signatures(cfg, specs)


def _stored_page_filters(conn, doc_id: int, sample: int = 1) -> str:
    """The filter signature recorded on this document's pages ("" if none).

    A document's pages are produced by one chain, so sampling a few rows is
    enough to answer "does this document need re-extraction for a filter
    change?" without walking every page row.
    """
    rows = conn.execute(
        "SELECT filters FROM pages WHERE document_id = ? AND status = 'done' "
        "ORDER BY page_no LIMIT ?", (doc_id, sample),
    ).fetchall()
    for r in rows:
        sig = r["filters"]
        if sig:
            return sig
    return ""


def _is_artifact(cfg: Config, spec) -> bool:
    """Is this filter a pure side-effect (artifact) filter (`returns: none`)?"""
    from .filters import load_filter
    try:
        return load_filter(cfg.filters_dir, spec.name).returns == "none"
    except FilterError:
        return False


def _artifact_due(cfg: Config, conn, doc_id: int, encoder: str, spec,
                  edited_dir: Path | None) -> bool:
    """Should this artifact filter re-run? See filters.artifact_stale."""
    from .filters import artifact_stale, load_filter
    try:
        f = load_filter(cfg.filters_dir, spec.name)
    except FilterError:
        return False
    lib_dir = _library_dir_for(cfg, conn, doc_id)
    if lib_dir is None:
        return True
    return artifact_stale(lib_dir, encoder, f, pages_dir_edited=edited_dir)


def _encoder_stage_for(cfg: Config, doc_path: Path, resolved: str, enc_file: Path | None):
    """The sidecar stage carrying this encoder's filter chains, if any.

    Encoders are listed in `pha.yaml` with both `rules` and `model`; an
    auto-discovered collection encoder (no sidecar entry) simply has no
    filters.
    """
    try:
        sc = _doc_sidecar(cfg, doc_path)
    except Exception:  # noqa: BLE001 - no sidecar is not an error
        return None
    for st in (sc.encoders or []):
        if st.rules == resolved:
            return st
    return None


_PAGE_BLOCK_RE = re.compile(r"^--- page (\d+) ---$", re.MULTILINE)


def _split_page_blocks(text: str) -> list[tuple[int, str]]:
    """Re-split filtered whole-document text into (page_no, text) pairs.

    Inverse of the `--- page N ---` join used to build the encoder input, so an
    `encoder.pre` filter can work on the whole document and still leave the
    encoder's per-page grounding (and `pages:` scoping) intact. Text before the
    first marker is attached to the first page.
    """
    if not text:
        return []
    marks = list(_PAGE_BLOCK_RE.finditer(text))
    if not marks:
        return []
    out: list[tuple[int, str]] = []
    lead = text[:marks[0].start()].strip()
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if i == 0 and lead:
            body = f"{lead}\n{body}".strip()
        out.append((int(m.group(1)), body))
    return out


def _pages_dir_for(cfg: Config, doc, variant: str) -> Path | None:
    """The library folder for one variant (`transcription-<pal>` / `edited-<ed>`).

    The bare `variant` and its `variant@<model>` sibling are ONE variant, so the
    directory matching the document's recorded model wins and the bare one is
    only used when it *is* the current (model-less) output — it is never
    preferred merely for sorting first, which used to serve an older generation
    of `*waiting*` placeholders (docs 47/50 in the bug report).
    """
    base = cfg.library / Path(doc["dir_path"] or "") / _doc_slug(doc)
    if not base.is_dir():
        return None
    return _pick_pages_dir(base, variant, doc)


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
        **_bibliography_front_matter(doc),
    }
    for p in pages:
        fm = dict(base)
        fm["page"] = p["page_no"]
        fm["status"] = "done" if p["status"] == "done" else "waiting"
        if p["reviewed_at"]:
            fm["reviewed"] = True
        # Per-page reading provenance: a targeted re-read (`pha scan --page N`)
        # records the palaeographer/model that read THIS page, overriding the
        # document-level pair shown above (the folder name stays the stage id —
        # the page file is authoritative). `pinned` marks a deliberate reading
        # a later bulk pass must not discard.
        try:
            page_pal = p["palaeographer"]
        except (IndexError, KeyError):
            page_pal = None
        if page_pal:
            fm["palaeographer"] = page_pal
            fm["model"] = p["palaeographer_model"] or None
        try:
            pinned = p["pinned_at"]
        except (IndexError, KeyError):
            pinned = None
        if pinned:
            fm["pinned"] = True
        # provenance: which stage filters shaped this page (name + short hash),
        # so "why is this text like this" is answerable from the artifact
        try:
            sig = p["filters"]
        except (IndexError, KeyError):
            sig = None
        if sig:
            fm["filters"] = sig
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

def _edit_needed(
    page,
    edit_row,
    editor: Editor,
    reprocess: bool,
    model_files: tuple = (),
    expected_filters: str = "",
) -> bool:
    """Does this page need (re-)editing?"""
    if edit_row is not None and edit_row["reviewed_at"]:
        return False  # a human corrected this edit; never re-edit over it
    if reprocess:
        return True
    if edit_row is None or edit_row["status"] != "done" or not edit_row["text"]:
        return True
    if edit_row["raw_sha"] != _raw_sha(page["raw_text"]):
        return True  # page was re-transcribed since the edit
    # The editor's filter chain changed (script hash, params, added/removed):
    # re-run this stage, exactly as a changed rules/model file does.
    try:
        recorded = edit_row["filters"]
    except (IndexError, KeyError):
        recorded = None
    if filters_changed(recorded, expected_filters):
        return True
    if editor.prompt_file:
        try:
            if editor.prompt_file.stat().st_mtime > (edit_row["updated_at"] or 0):
                return True  # the editor's prompt changed
        except OSError:
            pass
    for mf in model_files:
        try:
            if mf.stat().st_mtime > (edit_row["updated_at"] or 0):
                return True  # the editor's MODEL file changed (models/<id>.md)
        except OSError:
            pass
    return False


def write_edited_pages(cfg: Config, conn, doc_id: int, editor_id: str,
                       *, model: str | None) -> Path | None:
    """Write the editor's per-page output to library/.../edited-<editor>[@<model>].

    ``model`` is the RESOLVED model for this pass, passed by the caller — the
    directory name is the variant's identity, so it must not be read back from
    ``documents.editor_model``, which is transiently NULL while an editor change
    is being recorded. Reading it there wrote one logical variant into two
    directories, ``edited-<rules>`` and ``edited-<rules>@<model>`` (see
    ``enhancements/pha-duplicate-edited-variants-bug-report.md``).
    """
    doc = db.get_document(conn, doc_id)
    if not doc:
        return None
    ed_model = model or None
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
        "model": ed_model,
        **_bibliography_front_matter(doc),
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

def _library_doc_dir(cfg: Config, doc) -> Path | None:
    """The library folder holding a document's versions (`<dir_path>/<stem>_<date>`)."""
    if not doc:
        return None
    if not isinstance(doc, dict):  # accept sqlite3.Row too
        try:
            doc = dict(doc)
        except (TypeError, ValueError):
            return None
    base = cfg.library / Path(doc["dir_path"] or "")
    doc_dir = base / _doc_slug(doc)
    if doc_dir.exists():
        return doc_dir
    stem = Path(doc["path"]).stem
    folders = sorted(base.glob(f"{stem}_*"),
                     key=lambda p: p.stat().st_mtime if p.is_dir() else 0.0)
    return folders[-1] if folders else None


def _pending_scan(conn, targets: list[tuple[int, Path]]) -> list[dict]:
    """Fast library pending check for `(document_id, library doc dir)` targets.

    Same verdict as the historical `library/**` walk — a page file is pending
    when its mtime is newer than the row's `exported_at`, or, for a legacy row
    with `exported_at` NULL, when its body differs — but the page is derived
    from the FILE NAME (`page-NNN.md`, or the source name for a
    directory-of-images document) and the file is only READ for those legacy
    rows. Reading every page body is what made `pha status` spend minutes on a
    large archive; here only directory entries and their mtimes are touched
    (`os.scandir` stats come for free on macOS/APFS).

    A file whose name matches no page falls back to its front matter, exactly
    like the old walk, so hand-renamed files are still caught.
    """
    targets = [(int(d), p) for d, p in targets if p is not None]
    if not targets:
        return []
    doc_ids = [d for d, _ in targets]
    by_no: dict[int, dict[int, object]] = {}
    by_src: dict[int, dict[str, object]] = {}
    edits: dict[tuple[int, str], object] = {}
    # An archive can hold many documents, and SQLite caps bound parameters, so
    # index them in batches.
    for i in range(0, len(doc_ids), 500):
        batch = doc_ids[i:i + 500]
        marks = ",".join("?" * len(batch))
        for r in conn.execute(
            "SELECT id, document_id, page_no, source_name, exported_at FROM pages "
            f"WHERE document_id IN ({marks})", batch,
        ):
            did = int(r["document_id"])
            by_no.setdefault(did, {})[int(r["page_no"])] = r
            if r["source_name"]:
                by_src.setdefault(did, {})[r["source_name"]] = r
        for r in conn.execute(
            "SELECT pe.page_id, pe.editor, pe.exported_at FROM page_edits pe "
            "JOIN pages p ON p.id = pe.page_id "
            f"WHERE p.document_id IN ({marks})", batch,
        ):
            edits[(int(r["page_id"]), r["editor"] or "")] = r

    def read_body(path: Path) -> str:
        parsed = _parse_library_file(path)
        return (parsed[1] if parsed else "").strip()

    def raw_body(row) -> str:
        r = conn.execute("SELECT raw_text FROM pages WHERE id=?", (int(row["id"]),)).fetchone()
        raw = ((r["raw_text"] if r else "") or "").strip()
        return (format_notes(raw) if raw else "*waiting*").strip()

    def edit_body(page_id: int, editor: str | None) -> str:
        r = conn.execute("SELECT text FROM page_edits WHERE page_id=? AND editor=?",
                         (page_id, editor)).fetchone()
        return ((((r["text"] if r else "") or "") or "*waiting*")).strip()

    out: list[dict] = []
    for doc_id, doc_dir in targets:
        pages_no, pages_src = by_no.get(doc_id, {}), by_src.get(doc_id, {})
        try:
            with os.scandir(doc_dir) as it:
                variants = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        # A bare `edited-X` folder is the same variant as `edited-X@Y` (the bare
        # name only records that the model was unknown when it was written), so
        # its files must not be read back as human corrections: a stale alias
        # would otherwise be imported over good text and stamped `reviewed`.
        # Same rule as every other read path (addresses.collapse_variant_aliases).
        from .addresses import collapse_variant_aliases

        doc = db.get_document(conn, doc_id)
        keep = set(collapse_variant_aliases(
            [e.name for e in variants if e.is_dir()],
            current={
                "edited": (_doc_field(doc, "editor"), _doc_field(doc, "editor_model")),
                "transcription": (_doc_field(doc, "palaeographer"),
                                  _doc_field(doc, "palaeographer_model")),
            })) if doc else None
        for vdir in variants:
            variant = vdir.name
            if not vdir.is_dir():
                continue
            if keep is not None and variant not in keep:
                continue
            if not (variant.startswith("transcription-") or variant.startswith("edited-")):
                continue
            is_edited = variant.startswith("edited-")
            editor = variant[len("edited-"):].split("@", 1)[0] if is_edited else None
            try:
                with os.scandir(vdir.path) as it:
                    files = sorted(it, key=lambda e: e.name)
            except OSError:
                continue
            for f in files:
                if not f.name.endswith(".md"):
                    continue
                stem = f.name[:-3]
                row = pages_no.get(int(stem[5:])) if (
                    stem.startswith("page-") and stem[5:].isdigit()) else pages_src.get(stem)
                fm_body: str | None = None
                if row is None:
                    parsed = _parse_library_file(Path(f.path))
                    if not parsed:
                        continue
                    fm, fm_body = parsed
                    d_id, page_no = fm.get("document_id"), fm.get("page")
                    if d_id is None or page_no is None:
                        continue
                    row = conn.execute(
                        "SELECT id, document_id, page_no, source_name, exported_at FROM pages "
                        "WHERE document_id=? AND page_no=?",
                        (int(d_id), int(page_no))).fetchone()
                    if row is None:
                        continue
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                did, page_no = int(row["document_id"]), int(row["page_no"])
                if is_edited:
                    edit = edits.get((int(row["id"]), editor or ""))
                    if edit is not None and edit["exported_at"] is not None:
                        pending = mtime > edit["exported_at"]
                    else:
                        body = fm_body if fm_body is not None else read_body(Path(f.path))
                        pending = body != edit_body(int(row["id"]), editor)
                    if pending:
                        out.append({"path": f.path, "document_id": did, "page_no": page_no,
                                    "variant": variant, "editor": editor})
                else:
                    if row["exported_at"] is not None:
                        pending = mtime > row["exported_at"]
                    else:
                        body = fm_body if fm_body is not None else read_body(Path(f.path))
                        pending = body != raw_body(row)
                    if pending:
                        out.append({"path": f.path, "document_id": did,
                                    "page_no": page_no, "variant": variant})
    out.sort(key=lambda r: r["path"])
    return out


def pending_review_files(cfg: Config, conn, doc_id: int | None = None) -> list[dict]:
    """Find library page files a human edited since pha last wrote/imported them.

    Timestamp-based: a file is pending if its filesystem mtime is NEWER than
    the page/edit's `exported_at` (when pha last wrote that file). For legacy
    rows with exported_at NULL we fall back to comparing the file body to the
    DB text. Returns {path, document_id, page_no, variant, editor}.

    Both forms are DB-driven now (one `pages` + one `page_edits` query, then a
    directory scan per document) — the old form walked every `library/**/*.md`
    file and read each one, which cost minutes on an archive with tens of
    thousands of page files. `doc_id` narrows the same scan to one document.
    """
    if doc_id is not None:
        doc = db.get_document(conn, int(doc_id))
        doc_dir = _library_doc_dir(cfg, doc) if doc else None
        return _pending_scan(conn, [(int(doc_id), doc_dir)]) if doc_dir is not None else []
    targets: list[tuple[int, Path]] = []
    for doc in db.list_documents(conn, limit=100000):
        doc_dir = _library_doc_dir(cfg, doc)
        if doc_dir is not None:
            targets.append((int(doc["id"]), doc_dir))
    return _pending_scan(conn, targets)


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


def _all_review_files(cfg: Config, doc_id: int | None = None) -> list[dict]:
    """Every importable library page file, pending or not (for `review --all`).

    The deliberate "import and stamp everything" path. A file that cannot be
    parsed, or whose front matter names no document/page, counts as skipped;
    the work of resolving a file to a DB page lives in `review_import`.
    Returns the same `{path, document_id, page_no, variant, editor}` shape as
    `pending_review_files`.
    """
    out: list[dict] = []
    for p in sorted(cfg.library.rglob("*.md")):
        rel_parts = p.relative_to(cfg.library).parts
        # library/<rel_dir>/<slug>/transcription-<pal>/<file>.md
        # library/<rel_dir>/<slug>/edited-<editor>/<file>.md
        if len(rel_parts) < 3:
            continue
        variant = rel_parts[-2]  # transcription-xxx or edited-xxx
        if not (variant.startswith("transcription-") or variant.startswith("edited-")):
            continue
        parsed = _parse_library_file(p)
        if not parsed:
            out.append({"path": str(p), "unparsed": True})
            continue
        fm, _body = parsed
        d_id, page_no = fm.get("document_id"), fm.get("page")
        if d_id is None or page_no is None:
            out.append({"path": str(p), "unparsed": True})
            continue
        if doc_id is not None and int(d_id) != int(doc_id):
            continue
        rec = {"path": str(p), "document_id": int(d_id), "page_no": int(page_no),
               "variant": variant}
        if variant.startswith("edited-"):
            rec["editor"] = variant[len("edited-"):].split("@", 1)[0]
        out.append(rec)
    return out


def review_import(cfg: Config, conn, doc_id: int | None = None, verbose: bool = True,
                  include_all: bool = False) -> dict:
    """Import human corrections from the library markdown files back into the DB.

    The historian edits `library/.../transcription-<pal>/<stem>.md` or
    `library/.../edited-<editor>/<stem>.md`; this reads them back, updates
    pages.raw_text / page_edits.text, and stamps them reviewed so later
    scan/edit passes skip them. Returns counts.

    **Scope.** Only files a human actually changed since pha last wrote them
    are imported and stamped — the same `pending_review_files()` set `pha
    status` reports. Stamping every library file would freeze the archive:
    a `reviewed` row is never re-processed, even by `--reprocess`. Pass
    `include_all=True` (`pha review --all`) for the deliberate blanket import.
    """
    candidates = (
        _all_review_files(cfg, doc_id) if include_all
        else pending_review_files(cfg, conn, doc_id=doc_id)
    )

    updated_pages = 0
    updated_edits = 0
    skipped = 0
    missing = 0
    for rec in candidates:
        if rec.get("unparsed"):
            skipped += 1
            continue
        p = Path(rec["path"])
        d_id, page_no = int(rec["document_id"]), int(rec["page_no"])
        variant = rec["variant"]
        parsed = _parse_library_file(p)
        if not parsed:
            skipped += 1
            continue
        body = parsed[1]
        if db.get_document(conn, d_id) is None:
            missing += 1
            continue
        page = conn.execute(
            "SELECT id FROM pages WHERE document_id = ? AND page_no = ?",
            (d_id, page_no)).fetchone()
        if not page:
            missing += 1
            continue
        if variant.startswith("transcription-"):
            db.mark_page_reviewed(conn, page["id"], body)
            updated_pages += 1
            if verbose:
                print(f"  reviewed transcription: doc {d_id} page {page_no} ({p.name})")
        elif variant.startswith("edited-"):
            editor = rec.get("editor") or variant[len("edited-"):].split("@", 1)[0]
            db.set_page_edit(conn, page["id"], editor, text=body,
                             raw_sha=_raw_sha(page_row_raw(conn, page["id"])))
            db.mark_edit_reviewed(conn, page["id"], editor, body)
            updated_edits += 1
            if verbose:
                print(f"  reviewed edit: doc {d_id} page {page_no} ({p.name}, editor {editor})")
    conn.commit()
    return {"pages": updated_pages, "edits": updated_edits, "skipped": skipped,
            "missing": missing, "scanned": len(candidates)}


def unreview_import(cfg: Config, conn, doc_id: int | None = None,
                    page_no: int | None = None, verbose: bool = True) -> dict:
    """Clear the `reviewed` protection from transcription pages and edits.

    The undo for `review_import` (and for a mistaken blanket `pha review`).
    Text is NOT changed — only the stamp is lifted, so the pages become
    eligible for `pha scan` / `pha edit` again. Scoped by document and,
    optionally, a single page. Returns counts of cleared stamps.
    """
    pages = db.clear_page_reviewed(conn, doc_id=doc_id, page_no=page_no)
    edits = db.clear_edit_reviewed(conn, doc_id=doc_id, page_no=page_no)
    conn.commit()
    if verbose:
        scope = "all documents" if doc_id is None else f"doc {doc_id}"
        if page_no is not None:
            scope += f" page {page_no}"
        print(f"  unreviewed: {pages} transcription page(s), {edits} edit(s) ({scope})")
    return {"pages": pages, "edits": edits}


def _edit_null(cfg: Config, conn, doc_id: int, resolved: str,
               reprocess: bool, verbose: bool, page_no: int | None = None) -> dict:
    """Null/passthrough editor: copy each page's transcription verbatim as
    the 'edited' text (no model call). Produces edited-<resolved>/ pages and
    records the editor on the document for provenance."""
    doc = db.get_document(conn, doc_id)
    pages = db.get_pages(conn, doc_id)
    edited = 0
    for p in pages:
        if page_no is not None and p["page_no"] != page_no:
            continue  # targeted: only this one page
        raw = (p["raw_text"] or "").strip()
        if not raw:
            continue
        row = db.get_page_edit(conn, p["id"], resolved)
        if not reprocess and row is not None and row["status"] == "done" and row["text"] == raw:
            continue
        db.set_page_edit(conn, p["id"], resolved, text=raw, raw_sha=_raw_sha(raw))
        edited += 1
        conn.commit()
        write_edited_pages(cfg, conn, doc_id, resolved, model=None)
    if doc["editor"] != resolved:
        db.update_document(conn, doc_id, editor=resolved, editor_model=None)
        conn.commit()
    write_edited_pages(cfg, conn, doc_id, resolved, model=None)
    return {"action": "edited", "filename": doc["filename"], "editor": resolved, "pages": edited}


# Pages whose transcription has no readable content after the page marker are
# stamped as blank WITHOUT calling the model. Keeping this decision in code (not
# in the editor prompt) stops editors from both fabricating content on empty
# pages and over-applying blank rules to sparse-but-real pages (title/index
# pages, scattered OCR). A page is blank when it has fewer than this many
# letters/digits after stripping the marker: punctuation, whitespace and pure
# OCR noise don't count, so a real short title still reaches the editor model.
_EDIT_BLANK_MIN_CONTENT_CHARS = 1
_BLANK_EDIT_TEXT = "[Blank page -- no readable transcription]"


def _content_chars(text: str) -> int:
    """Count the readable characters (letters/digits) in `text`."""
    return sum(1 for ch in text if ch.isalnum())


def _is_blank_edit(text: str | None) -> bool:
    """True when `text` is the deterministic blank-page stamp (no real content)."""
    return bool(text) and text.strip() == _BLANK_EDIT_TEXT


def _edited_texts(conn, doc_id: int, editor: str | None) -> dict[int, str]:
    """page_id -> editor text for DONE, non-blank edits.

    Blank-page stamps, errors and empty rows are excluded, so callers fall back
    to the page's raw transcription. Shared by the indexer (edited variant) and
    the encoder (edited-preferred whole-document text) so the blank sentinel
    never reaches search or the encoder prompt."""
    out: dict[int, str] = {}
    if not editor:
        return out
    for e in db.edits_for_document(conn, doc_id, editor):
        if e["status"] == "done" and e["text"] and not _is_blank_edit(e["text"]):
            out[e["page_id"]] = e["text"]
    return out


def _strip_page_marker(raw: str) -> str:
    """Return `raw` minus a leading OCR page-marker line (e.g. '--- Page 1 ---')."""
    lines = raw.splitlines()
    if lines and re.match(r"^---\s*page\s+\d+\s*---$", lines[0], re.IGNORECASE):
        lines = lines[1:]
    return "\n".join(lines).strip()


def edit_document(
    cfg: Config,
    conn,
    doc_id: int,
    editor_id: str | None = None,
    reprocess: bool = False,
    verbose: bool = True,
    page_no: int | None = None,
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
    lease = _leases(cfg).get(doc["sha256"])
    if lease is not None:
        return {"action": "skipped", "filename": doc["filename"],
                "reason": f"out on hand-over {lease.handoff_id} "
                          f"(age {_lease_age(lease)})"}
    editor_model = None
    editor_stage = None  # the sidecar stage carrying this editor's filter chains
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
                editor_stage = sc.editor
        else:
            ed_id, _src = resolve_editor_id(
                path.stem, path if path.is_dir() else path.parent, cfg.dropbox
            )
            resolved = ed_id or (doc["editor"] if doc["editor"] in cfg.editors else None)
    if not resolved:
        return {"action": "skipped", "filename": doc["filename"], "reason": "no editor configured"}
    if resolved in ("null", "passthrough"):
        return _edit_null(cfg, conn, doc_id, resolved, reprocess, verbose, page_no=page_no)
    editor = cfg.get_editor(resolved)
    editor = cfg.resolve_model(editor, editor_model)
    # Staleness inputs: (a) the editor's MODEL interface file (models/<id>.md)
    # — editing it (e.g. switching the server model) re-edits, exactly like the
    # rules prompt file; (b) a CHANGE OF MODEL ID since the last run forces a
    # full re-edit (the old text was produced by a different model).
    model_files: list = []
    if editor.model_ref and editor.model_ref in cfg.models:
        mf = cfg.models[editor.model_ref].prompt_file
        if mf is not None:
            model_files.append(mf)
    model_identity_changed = (
        bool(editor.model_ref) and (doc["editor_model"] or None) != editor.model_ref
    )
    force = reprocess or model_identity_changed
    pages = db.get_pages(conn, doc_id)
    # The chain this pass would apply (pre + post), as a signature: a page whose
    # stored signature differs is re-edited even without --reprocess.
    _editor_chain = (
        (editor_stage.pre if editor_stage is not None else [])
        + (editor_stage.post if editor_stage is not None else [])
    )
    # `expected_filters` is WRITTEN (resolved); `acceptable_filters` is what a
    # stored value may be and still count as unchanged (resolved or legacy).
    expected_filters = _configured_filters_signature(cfg, _editor_chain)
    acceptable_filters = _acceptable_filters_signature(cfg, _editor_chain)
    client = ModelClient(editor.base_url, timeout_s=editor.timeout_s, api_key=editor.api_key,
                         api_style=editor.api_style, deadline_s=editor.deadline_s)
    edited = 0
    stalled: list[int] = []  # pages abandoned for THIS pass by a model stall
    try:
        for p in pages:
            if page_no is not None and p["page_no"] != page_no:
                continue  # targeted: only re-edit this one page
            raw = (p["raw_text"] or "").strip()
            if not raw:
                continue
            edit_row = db.get_page_edit(conn, p["id"], resolved)
            if not _edit_needed(p, edit_row, editor, force, model_files=tuple(model_files),
                                expected_filters=acceptable_filters):
                continue
            if verbose:
                print(f"  editing page {p['page_no']}/{doc['page_count']} ...", flush=True)
            started = time.monotonic()
            body = _strip_page_marker(raw)
            if _content_chars(body) < _EDIT_BLANK_MIN_CONTENT_CHARS:
                # Deterministic blank page: no model call, so a page cannot be
                # hallucinated into content when its transcription is empty, and
                # real sparse text (any letters/digits) always reaches the model.
                # Filters are skipped with the model call they wrap, but the
                # configured chain is still recorded so the page is not
                # re-edited on every pass.
                db.set_page_edit(conn, p["id"], resolved, text=_BLANK_EDIT_TEXT,
                                 raw_sha=_raw_sha(raw), filters=expected_filters)
                edited += 1
            else:
                try:
                    # editor.pre shapes the transcription BEFORE the model sees
                    # it; editor.post cleans the model's output before storing.
                    ran: list = []
                    stage_input = raw
                    if editor_stage is not None and editor_stage.pre:
                        stage_input, ran = _run_stage_filters(
                            cfg, editor_stage.pre, hook="editor.pre", value=raw,
                            conn=conn, path=Path(doc["path"]), doc_id=doc_id,
                            stage="editor", page=p["page_no"],
                            source_name=p["source_name"], verbose=verbose,
                            return_ran=True,
                        )
                    prompt = (
                        f"{editor.prompt_text}\n\n"
                        f"Document: {doc['filename']}\nPage: {p['page_no']} of {doc['page_count']}\n\n"
                        f"Transcription to edit:\n{stage_input}"
                    )
                    out = client.chat_text(editor.model, prompt, editor.temperature, editor.max_tokens,
                                           thinking=editor.thinking)
                    if editor_stage is not None and editor_stage.post:
                        out, post_ran = _run_stage_filters(
                            cfg, editor_stage.post, hook="editor.post", value=out,
                            conn=conn, path=Path(doc["path"]), doc_id=doc_id,
                            stage="editor", page=p["page_no"],
                            source_name=p["source_name"], verbose=verbose,
                            return_ran=True,
                        )
                        ran = ran + post_ran
                    db.set_page_edit(conn, p["id"], resolved, text=out, raw_sha=_raw_sha(raw),
                                     filters=filters_signature(ran))
                    edited += 1
                    if verbose:
                        print(_page_timing_line(p["page_no"], doc["page_count"],
                                                time.monotonic() - started), flush=True)
                except ModelStall as e:
                    # A stalled provider: leave this page UNEDITED for this pass
                    # (no error row) so a later pass retries it, and keep going
                    # with the rest of the document (report G4).
                    stalled.append(p["page_no"])
                    if verbose:
                        print(f"  editing page {p['page_no']}/{doc['page_count']}: "
                              f"STALLED after {time.monotonic() - started:.1f}s — "
                              f"abandoned for this pass ({e})", flush=True)
                except (ModelError, FilterError) as e:
                    db.set_page_edit(conn, p["id"], resolved, error=str(e), raw_sha=_raw_sha(raw))
            conn.commit()
            # grow output page by page — with the RESOLVED model, so the
            # directory is the same one the final write below produces even
            # while `editor_model` is still unset on the document row.
            write_edited_pages(cfg, conn, doc_id, resolved, model=editor.model_ref or None)
    finally:
        client.close()
    if doc["editor"] != resolved or (doc["editor_model"] or None) != (editor.model_ref or None):
        db.update_document(conn, doc_id, editor=resolved,
                           editor_model=editor.model_ref or None)
        conn.commit()
    write_edited_pages(cfg, conn, doc_id, resolved, model=editor.model_ref or None)
    if stalled and verbose:
        print(f"  {len(stalled)} page(s) abandoned for this pass (model stalled): "
              f"{_pages_list(stalled)} — re-run `pha edit` to retry them; nothing was "
              f"recorded as failed", flush=True)
    return {"action": "edited", "filename": doc["filename"], "editor": resolved,
            "pages": edited, "stalled": sorted(stalled)}


def _index_after_edit(cfg: Config, conn, doc_id: int, edited_pages: int,
                      verbose: bool = True) -> dict:
    """Re-index a document the editor pass just finished.

    Indexing runs when the editor changed pages (their edited variant is new)
    **or** when the document has no chunks at all — the repair case from the
    2026-09-15 incident: `pha edit` re-edited pages of a document whose index
    had never been written, and left it that way. A failed embed is reported
    and the pass continues; it is not fatal to the other documents.
    """
    stats = db.chunk_stats(conn, doc_id) or {}
    has_chunks = stats.get(doc_id, {}).get("chunks", 0) > 0
    if edited_pages <= 0 and has_chunks:
        return {"indexed": False}
    try:
        chunks = index_document(cfg, conn, doc_id, verbose=verbose)
    except ModelError as e:
        if verbose:
            print(f"  ! not re-indexed: {e}", flush=True)
        return {"indexed": False, "index_error": str(e)}
    if verbose and edited_pages <= 0:
        print(f"  index repaired: {chunks} chunk(s) (the document had none)", flush=True)
    return {"indexed": True, "chunks": chunks}


def edit_all(cfg: Config, reprocess: bool = False, verbose: bool = True,
             page_no: int | None = None) -> dict:
    """Run the editor pass for every document that has an editor configured.

    Takes a lock on every model-server the matched documents may talk to (plus
    the embedding model), so an edit and a scan cannot evict each other's models
    — and two jobs on *different* servers still run concurrently (see locks.py).
    If another job holds a needed server, this pass reports which one and exits."""
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    lock = None
    try:
        docs = db.list_documents(conn, limit=10000)
        keys = _job_keys(cfg, [Path(d["path"]) for d in docs if d["path"]],
                         include_pal=False)   # the editor reads no page
        lock = locks.acquire(cfg, keys, label="pha edit")
        if not lock.ok:
            reason = lock.reason()
            print(f"  {reason}", flush=True)
            return {"results": [{"action": "skipped", "filename": "(edit)",
                                 "reason": reason}]}
        results = []
        indexed = 0
        index_failed: list[str] = []
        for d in docs:
            res = edit_document(cfg, conn, d["id"], reprocess=reprocess,
                                verbose=verbose, page_no=page_no)
            if res.get("action") == "edited":
                idx = _index_after_edit(cfg, conn, d["id"], res.get("pages", 0),
                                        verbose=verbose)
                indexed += 1 if idx.get("indexed") else 0
                if idx.get("index_error"):
                    index_failed.append(f"{d['filename']}: {idx['index_error']}")
            results.append(res)
        return {"results": results, "indexed": indexed, "index_failed": index_failed}
    finally:
        conn.close()
        locks.release(lock)


def edit_documents_under(
    cfg: Config, path: str, reprocess: bool = False, verbose: bool = True,
    page_no: int | None = None,
) -> dict:
    """Run the editor pass for JUST the documents under a dropbox subpath
    (`pha edit --path collections/COLX`, a document folder, ...).

    Shares the model-server locks with scan_once/edit_all (one model per server)
    and indexes what it re-edited. Only ALREADY-INGESTED documents are edited; a
    document whose transcription is missing is skipped (run `pha scan --path`
    for it first)."""
    root = Path(path)
    if not root.is_absolute():
        root = cfg.dropbox / root
    root = root.resolve()
    if root != cfg.dropbox.resolve() and cfg.dropbox.resolve() not in root.parents:
        if not root.exists():
            print(f"  target path does not exist: {path}", flush=True)
            return {"results": []}
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    lock = None
    try:
        matched: list[tuple] = []
        seen: set[int] = set()
        for f in discover(cfg.dropbox, cfg.dir_documents, root=root, exclude=[cfg.inbox]):
            doc = db.get_document_by_path(conn, str(f))
            if doc is None or doc["id"] in seen:
                continue
            if doc["status"] != "done":
                if verbose:
                    print(f"  skipping {doc['filename']}: not extracted yet "
                          f"(status={doc['status']})", flush=True)
                continue
            seen.add(doc["id"])
            matched.append((doc, f))
        lock = locks.acquire(cfg, _job_keys(cfg, [f for _d, f in matched],
                                            include_pal=False),
                             label="pha edit")
        if not lock.ok:
            reason = lock.reason()
            print(f"  {reason}", flush=True)
            return {"results": [{"action": "skipped", "filename": "(edit)",
                                 "reason": reason}]}
        results = []
        indexed = 0
        index_failed: list[str] = []
        for doc, _f in matched:
            res = edit_document(cfg, conn, doc["id"], reprocess=reprocess,
                                verbose=verbose, page_no=page_no)
            if res.get("action") == "edited":
                idx = _index_after_edit(cfg, conn, doc["id"], res.get("pages", 0),
                                        verbose=verbose)
                indexed += 1 if idx.get("indexed") else 0
                if idx.get("index_error"):
                    index_failed.append(f"{doc['filename']}: {idx['index_error']}")
            results.append(res)
        return {"results": results, "indexed": indexed, "index_failed": index_failed}
    finally:
        conn.close()
        locks.release(lock)


# --------------------------------------------------------------------------- encoders (structured records)

def make_encoder_client(cfg: Config, encoder_id: str | None = None,
                        encoder: Encoder | None = None) -> tuple[ModelClient, Encoder]:
    """Create a ModelClient for an encoder. Pass either a global encoder_id
    (legacy) or the loaded encoder object (collection-local)."""
    if encoder is None:
        encoder = cfg.get_encoder(encoder_id)
    encoder = cfg.resolve_model(encoder)  # bind the default model when rules-only
    return ModelClient(encoder.base_url, timeout_s=encoder.timeout_s, api_key=encoder.api_key,
                       api_style=encoder.api_style, deadline_s=encoder.deadline_s), encoder


def _parse_json_array(text: str) -> list | None:
    """Extract the first balanced JSON array from a model response, or None
    when no valid JSON array is present.

    A valid-but-EMPTY array returns ``[]`` (falsy but NOT None), so callers
    can tell the model's honest "nothing to extract" answer apart from an
    unparseable response and stop retrying instead of insisting. Also accepts
    a JSON object wrapping an array under a list-valued key
    (e.g. {"records": [...]})."""
    if not text:
        return None
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
            return None
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
                    return None
    return None


def _encode_needed(cfg: Config, conn, doc_id: int, encoder: Encoder, resolved: str,
                   doc_path: Path, reprocess: bool, enc_file: Path | None = None,
                   expected_filters: str = "") -> bool:
    """Re-encode when no records yet, or the encoder file / the document's
    encoder.prompt.md / encoder-prompt-langextract.md / the source
    transcription changed since the records were created. A changed encoder
    FILTER chain (`expected_filters`) re-encodes too."""
    if reprocess:
        return True
    row = conn.execute(
        "SELECT MAX(created_at) AS m, MAX(filters) AS f FROM records "
        "WHERE document_id = ? AND encoder = ?",
        (doc_id, resolved),
    ).fetchone()
    if not row or not row["m"]:
        return True
    latest = row["m"]
    if filters_changed(row["f"], expected_filters):
        return True  # an encoder filter was edited, retuned, added or removed
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
    # provenance: the stage filters that shaped this encoder's input/output
    try:
        sig = rows[0]["filters"] if rows else None
    except (IndexError, KeyError):
        sig = None
    if sig:
        payload["filters"] = sig
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
    lease = _leases(cfg).get(doc["sha256"])
    if lease is not None:
        return {"action": "skipped", "filename": doc["filename"],
                "reason": f"out on hand-over {lease.handoff_id} "
                          f"(age {_lease_age(lease)})"}
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
        edits = _edited_texts(conn, doc_id, doc["editor"])
    texts = [(p["page_no"], (edits.get(p["id"]) or p["raw_text"] or "").strip())
             for p in pages if (edits.get(p["id"]) or p["raw_text"] or "").strip()]
    page_filter = _page_filter(encoder)
    if page_filter is not None:
        texts = [x for x in texts if x[0] in page_filter]
    if not texts:
        return {"action": "skipped", "filename": doc["filename"], "reason": "no text"}

    enc_stage = _encoder_stage_for(cfg, doc_path, resolved, enc_file)
    lib_dir = _library_dir_for(cfg, conn, doc_id)
    _, edited_dir = _stage_filter_dirs(cfg, conn, doc_id)
    records_file = (lib_dir / f"records-{resolved}.json") if lib_dir else None
    concat_file = (lib_dir / f"concatenated-{resolved}.md") if lib_dir else None
    # Only the value-shaping filters (pre + non-artifact post) affect WHAT is
    # stored, so they alone drive re-encoding; an artifact filter is governed by
    # its own stamp.
    _record_chain = (
        (enc_stage.pre if enc_stage is not None else [])
        + [F for F in (enc_stage.post if enc_stage is not None else [])
           if not _is_artifact(cfg, F)]
    )
    # `record_filters` is WRITTEN (resolved); the acceptable form also admits the
    # legacy spelling, so the resolution fix does not force one mass re-encode.
    record_filters = _configured_filters_signature(cfg, _record_chain)
    acceptable_record_filters = _acceptable_filters_signature(cfg, _record_chain)
    if not _encode_needed(cfg, conn, doc_id, encoder, resolved, doc_path, reprocess,
                          enc_file, expected_filters=acceptable_record_filters):
        return {"action": "skipped", "filename": doc["filename"], "reason": "records up to date"}

    # The encoder's filter chain: `pre` normalises the whole-document text the
    # model will read (BEFORE any call is built), `post` sees the parsed records
    # (and is where artifact filters materialise files). An encoder file is
    # collection-local, so its stage comes from the sidecar when it names one.
    if enc_stage is not None and enc_stage.pre:
        whole = "\n\n".join(f"--- page {p} ---\n{t}" for p, t in texts)
        whole = _run_stage_filters(
            cfg, enc_stage.pre, hook="encoder.pre", value=whole, conn=conn,
            path=doc_path, doc_id=doc_id, stage="encoder", encoder=resolved,
            library_dir=lib_dir, pages_dir_edited=edited_dir,
            records_file=records_file, concatenated_file=concat_file, verbose=verbose,
        )
        texts = _split_page_blocks(whole)

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
                #
                # NOTE: a valid-but-EMPTY array ([]) is an honest answer — the
                # model used the "not really an entry, return []" escape hatch —
                # and MUST break the loop. `_parse_json_array` returns None only
                # when there is genuinely no parseable array, so `parsed is not
                # None` (rather than truthiness) is what distinguishes success.
                parsed: list | None = None
                for attempt in range(3):
                    out = client.chat_text(encoder.model, prompt, encoder.temperature,
                                           max(8192, encoder.max_tokens),
                                           thinking=encoder.thinking)
                    parsed = _parse_json_array(out)
                    if parsed is not None or not out.strip():
                        break
                    if verbose:
                        print(f"    (retry {attempt + 1}: response {len(out)} chars not parseable, "
                              f"head: {out[:120]!r})", flush=True)
                    if start_page and attempt >= 1:
                        # The detector flagged this page; drop the escape
                        # hatch and insist only when the model returned prose
                        # (not a valid JSON array) — a valid [] above would
                        # already have broken the loop, so insisting here can
                        # no longer override an honest "no entry" answer.
                        prompt = prompt.replace(
                            "If page {0} is not really an entry after all, return [].".format(start_page),
                            "A detector flagged page {0} as an entry start. Extract it.".format(start_page),
                        )
                if verbose and parsed is None and out.strip():
                    print(f"    (model returned no parseable JSON array; "
                          f"response {len(out)} chars, head: {out[:160]!r})", flush=True)
                for rec in _expand_records(parsed or []):
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

    # Store first, so an artifact filter can read the records file it consumes.
    db.clear_records(conn, doc_id, resolved)
    for rec in records:
        db.add_record(conn, doc_id, resolved, str(rec.get("kind") or rec.get("type") or "record"),
                      json.dumps(rec, ensure_ascii=False), str(rec.get("page") or ""),
                      filters=record_filters)
    if doc["encoder"] != resolved:
        db.update_document(conn, doc_id, encoder=resolved)
    conn.commit()
    write_records_file(cfg, conn, doc_id, resolved)
    write_concatenated_file(cfg, conn, doc_id, texts, resolved)

    # encoder.post: the parsed records, once per encoder (NOT per chunk/pass).
    # An artifact filter (returns: none) consumes this list, writes files and
    # leaves the records unchanged; it only re-runs when its stamp says its
    # sources changed, so a model pass does not rewrite unchanged artifacts.
    # It runs after the records file is on disk, which is what it reads.
    if enc_stage is not None and enc_stage.post:
        due = [F for F in enc_stage.post
               if not _is_artifact(cfg, F) or _artifact_due(cfg, conn, doc_id, resolved, F, edited_dir)]
        if due:
            records, ran = _run_stage_filters(
                cfg, due, hook="encoder.post", value=records,
                conn=conn, path=doc_path, doc_id=doc_id, stage="encoder",
                encoder=resolved, library_dir=lib_dir, pages_dir_edited=edited_dir,
                records_file=records_file, concatenated_file=concat_file, verbose=verbose,
                return_ran=True,
            )
            # A successful artifact run is stamped, which is what stops it from
            # re-running until its sources change.
            for r in ran:
                spec = next((F for F in due if F.name == r["name"]), None)
                if spec is None or not _is_artifact(cfg, spec) or lib_dir is None:
                    continue
                try:
                    from .filters import load_filter
                    write_stamp(lib_dir, resolved, load_filter(cfg.filters_dir, r["name"]))
                except FilterError:
                    pass

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


def _leases(cfg: Config):
    """The hand-over lease map, or {} when the feature is not in use.

    Imported lazily: `handoff` imports this module, so a module-level import
    would be circular. The lease is keyed by sha256, the only identity that
    means the same on two machines.
    """
    try:
        from . import handoff
        return handoff.leased_shas(cfg)
    except Exception:  # noqa: BLE001 - never let the lease machinery break a run
        return {}


def _leased_document(cfg: Config, conn, path: Path, include_leased: bool = False):
    """The lease holding this path's document, unless overridden."""
    if include_leased:
        return None
    doc = db.get_document_by_path(conn, str(path))
    if doc is None:
        return None
    return _leases(cfg).get(doc["sha256"])


def _lease_age(lease) -> str:
    try:
        from .handoff import _fmt_age
        return _fmt_age(lease.age_s())
    except Exception:  # noqa: BLE001
        return "?"


def plan_page_rescan(cfg: Config, conn, path: Path, pages) -> list[dict]:
    """What a `pha scan --page N` would do — no model call, no writes.

    One entry per page: the reading it would replace, whether it is pinned,
    whether it will be refused (a human-reviewed transcription) and whether a
    human-corrected edit will be kept."""
    doc = db.get_document_by_path(conn, str(path))
    if doc is None:
        return [{"page": int(p), "action": "error",
                 "reason": "not scanned yet — run `pha scan --path <doc>` first"}
                for p in sorted(pages)]
    plan: list[dict] = []
    for n in sorted(pages):
        row = conn.execute(
            "SELECT * FROM pages WHERE document_id = ? AND page_no = ?",
            (doc["id"], int(n))).fetchone()
        if row is None:
            plan.append({"page": int(n), "action": "error",
                         "reason": f"no page {int(n)} of {doc['page_count'] or 0}"})
            continue
        edited = conn.execute(
            "SELECT reviewed_at FROM page_edits WHERE page_id = ? AND reviewed_at IS NOT NULL",
            (row["id"],)).fetchone()
        entry = {
            "page": int(n),
            "action": "refused" if row["reviewed_at"] else "re-read",
            "from": {"palaeographer": row["palaeographer"] or doc["palaeographer"],
                     "model": row["palaeographer_model"] or doc["palaeographer_model"]},
            "pinned": bool(row["pinned_at"]),
            "human_reviewed": bool(row["reviewed_at"]),
            "human_edit_kept": edited is not None,
            "chars": len(row["raw_text"] or ""),
        }
        if row["reviewed_at"]:
            entry["reason"] = (f"human-reviewed — run `pha review --unset --doc "
                               f"{doc['id']} --page {int(n)}` to release it")
        plan.append(entry)
    return plan


def unpin_pages(cfg: Config, conn, path: Path, pages=None) -> dict:
    """`pha scan --unpin`: clear the pin on a document's pages, keep the text.

    The recorded provenance stays (it says who read the page), so `pha status`
    can still explain the reading; only the protection is lifted, which is what
    lets a later bulk pass (or `--reprocess`) re-read the page."""
    doc = db.get_document_by_path(conn, str(path))
    name = Path(path).name
    if doc is None:
        print(f"  {name}: not scanned yet — nothing to unpin", flush=True)
        return {"action": "skipped", "filename": name, "unpinned": 0}
    cleared = 0
    if pages is not None:
        for n in sorted(pages):
            cleared += db.clear_page_pins(conn, doc["id"], page_no=int(n))
    else:
        cleared = db.clear_page_pins(conn, doc["id"])
    conn.commit()
    write_document_pages(cfg, conn, doc["id"])
    return {"action": "unpinned", "filename": doc["filename"], "unpinned": cleared}


def scan_once(
    cfg: Config,
    client: ModelClient,
    palaeographer: Palaeographer,
    explicit_prompt: str | None = None,
    reprocess: bool = False,
    verbose: bool = True,
    path: str | None = None,
    include_leased: bool = False,
    pages: set[int] | None = None,
    pal_override: str | None = None,
    model_override: str | None = None,
    dry_run: bool = False,
    unpin: bool = False,
    pin: bool = True,
) -> dict:
    """Scan the dropbox (or a subpath), or re-read named pages of ONE document.

    With `pages`, `path` must resolve to exactly one document and
    `pal_override`/`model_override` are authoritative for that run; only those
    pages are rendered and transcribed, each recording its own provenance and
    (unless `pin=False`) a pin. `dry_run` prints the plan and calls no model;
    `unpin` clears the pins instead of re-reading."""
    cfg.ensure_dirs()
    if dry_run and pages is None:
        # A dry run is a plan for a targeted page action; never let it fall
        # through to a real full scan.
        return {"scanned": 0, "results": [{"action": "error", "filename": "(scan)",
                                           "error": "--dry-run needs --page"}]}
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
    # Discover BEFORE locking: the lock must cover every model-server the job
    # may touch, and that is known only from the matched documents' config.
    files = discover(cfg.dropbox, cfg.dir_documents, root=scan_root, exclude=[cfg.inbox])

    # ---- a targeted page action needs exactly ONE document -----------------
    if pages is not None or unpin:
        if len(files) != 1:
            msg = (f"--page/--unpin needs a target resolving to exactly one document; "
                   f"{len(files)} matched — narrow --path to the document")
            print(f"  {msg}", flush=True)
            return {"scanned": 0, "results": [{"action": "error", "filename": "(scan)",
                                               "error": msg}]}
        if dry_run:
            if pages is None:
                msg = "--dry-run needs --page (there is nothing else to plan)"
                print(f"  {msg}", flush=True)
                return {"scanned": 1, "results": [{"action": "error", "filename": "(scan)",
                                                   "error": msg}]}
            conn = db.connect(cfg.db_path)
            try:
                plan = plan_page_rescan(cfg, conn, files[0], pages)
            finally:
                conn.close()
            return {"scanned": 1,
                    "results": [{"action": "planned", "filename": files[0].name,
                                 "path": str(files[0]), "plan": plan}]}

    if unpin:
        lock = locks.acquire(cfg, _job_keys(cfg, files), label="pha scan --unpin")
        if not lock.ok:
            reason = lock.reason()
            print(f"  {reason}", flush=True)
            return {"scanned": 0, "results": [{"action": "skipped", "filename": "(scan)",
                                               "reason": reason}]}
        conn = db.connect(cfg.db_path)
        try:
            results = [unpin_pages(cfg, conn, files[0], pages)]
        finally:
            conn.close()
            locks.release(lock)
        return {"scanned": 1, "results": results}

    lock = locks.acquire(cfg, _job_keys(cfg, files, default_pal=palaeographer,
                                        pal_override=pal_override,
                                        model_override=model_override, warn=False),
                         label="pha scan")
    if not lock.ok:
        reason = lock.reason()
        print(f"  {reason}", flush=True)
        return {"scanned": 0, "results": [{"action": "skipped", "filename": "(scan)",
                                           "reason": reason}]}
    conn = db.connect(cfg.db_path)
    # vision clients per effective palaeographer (rules + model override): the
    # default is the passed client; other palaeographers get their own.
    clients: dict[tuple, tuple[ModelClient, Palaeographer]] = {
        _client_key(palaeographer): (client, palaeographer)
    }
    try:
        db.backfill_dir_path(conn, cfg.dropbox)
        results = []
        for i, f in enumerate(files, 1):
            if verbose:
                print(f"[{i}/{len(files)}] {f.name}", flush=True)
            pal, sc = _effective_palaeographer(cfg, f, palaeographer, pal_override,
                                               model_override)
            key = _client_key(pal)
            if key not in clients:
                clients[key] = (_vision_client(pal), pal)
                if verbose:
                    print(f"  palaeographer: {pal.id} ({pal.description or pal.model})", flush=True)
            leased = _leased_document(cfg, conn, f, include_leased)
            if leased is not None:
                results.append({
                    "action": "skipped", "filename": f.name,
                    "reason": f"out on hand-over {leased.handoff_id} "
                              f"(age {_lease_age(leased)}); "
                              f"use --include-leased to override",
                })
                continue
            results.append(
                ingest_file(cfg, conn, clients[key][0], f, clients[key][1],
                            explicit_prompt, reprocess, verbose, sidecar=sc,
                            pages=pages, pin=pin)
            )
        # Refresh bibliographic references LAST, so documents added by this scan
        # are covered too. This is metadata only: it never touches page text,
        # document status or sha256, so a new/edited sidecar cannot mark a
        # document for re-transcription.
        bib_stats = sync_bibliography(cfg, conn, verbose=verbose)
        for _doc_id, name, warning in bib_stats["warnings"]:
            print(f"  warning: {name}: {warning}", flush=True)
        return {"scanned": len(files), "results": results}
    finally:
        conn.close()
        locks.release(lock)
        for pid, (c, _p) in clients.items():
            # Only the clients this scan created: the caller owns the one it
            # passed in (the keys are (id, model_ref) tuples).
            if pid != _client_key(palaeographer):
                c.close()


def reindex_all(cfg: Config, client: ModelClient, verbose: bool = True,
                path: str | None = None, doc: int | None = None,
                page: int | None = None, force: bool = False) -> dict:
    """Re-embed chunks for every ingested document, or only for the one named
    by `doc` (`pha reindex --doc N`), or only for the documents under a dropbox
    subpath (`pha reindex --path collections/COLX`, a document folder, or a
    single document file). Documents whose status is not 'done' are skipped —
    their transcription is not final yet.

    Re-indexing is **incremental by default**: a chunk whose text is unchanged
    and whose stored vector came from the current embed model is reused, so
    correcting one page re-embeds that page, not the whole volume. `force=True`
    (`pha reindex --force`) re-embeds every chunk regardless — that is what
    you run to rebuild the index from scratch. Switching the embed model also
    forces a full re-embed (the stored vectors' model no longer matches).

    `page` (a 1-based page number) restricts the pass to one page of one
    document (`--doc` is required): only that page's chunks are replaced, so
    every other page keeps its rows and vectors.

    A document whose embed fails is **reported and left untouched** (see
    `index_document`): it keeps its existing chunks and vectors, so a failed
    re-index can never quietly strip embeddings from a finished document. It
    is counted in `failed`, not `reindexed`.

    Takes the SAME lock as `scan_once`/`edit_all`: reindexing embeds through
    the local embed model, so running it alongside a scan or edit is the
    "two local models at once" swap that this lock exists to prevent — and
    Takes a lock on the embedding model's server (see locks.py): reindexing
    embeds through the embed model, so running it alongside a scan or edit that
    uses the same server is the "two local models at once" swap this lock
    exists to prevent — and embed-endpoint contention is exactly what makes
    `embed()` time out. A scan using a *different* server can run alongside."""
    if page is not None and doc is None:
        return {"reindexed": 0, "chunks": {}, "failed": [],
                "reason": "--page needs --doc (refusing to reindex page N of every document)"}
    cfg.ensure_dirs()
    keys = _job_keys(cfg, [], embed=True)
    lock = locks.acquire(cfg, keys, label="pha reindex")
    if not lock.ok:
        # cmd_reindex reports the reason (and exits 2); keep the message in the
        # return value rather than printing it twice.
        return {"reindexed": 0, "chunks": {}, "failed": [], "reason": lock.reason()}
    conn = db.connect(cfg.db_path)
    try:
        db.backfill_dir_path(conn, cfg.dropbox)
        if doc is not None:
            one = db.get_document(conn, doc)
            if one is None:
                return {"reindexed": 0, "chunks": {}, "failed": [],
                        "reason": f"no document #{doc}"}
            docs = [one]
        elif path:
            docs = _documents_under(cfg, conn, path)
            if docs is None:
                return {"reindexed": 0, "chunks": {}, "failed": []}
        else:
            docs = db.list_documents(conn, limit=10000)
        counts = {}
        failed: list[dict] = []
        leases = _leases(cfg)
        pages = None if page is None else {page}
        for d in docs:
            if d["status"] != "done":
                continue
            if d["sha256"] in leases:
                lease = leases[d["sha256"]]
                if verbose:
                    print(f"  - {d['filename']}: out on hand-over {lease.handoff_id}; "
                          f"skipped", flush=True)
                continue
            try:
                counts[d["id"]] = index_document(
                    cfg, conn, d["id"], embed_client=client, verbose=verbose,
                    incremental=not force, pages=pages,
                )
            except ModelError as e:
                failed.append({"id": d["id"], "filename": d["filename"], "error": str(e)})
                if verbose:
                    print(f"  ! {d['filename']}: {e}", flush=True)
        return {"reindexed": len(counts), "chunks": counts, "failed": failed}
    finally:
        conn.close()
        locks.release(lock)


def _bibliography_front_matter(doc) -> dict:
    """Front-matter keys carrying a document's bibliographic reference.

    Written into every library page file so whoever reads the page knows the
    work it belongs to. Returns ``{}`` when the document has no sidecar, so the
    front matter of an unreferenced document is byte-for-byte unchanged.
    """
    from . import bibliography as biblib

    bib, _warning = biblib.load_bibliography(doc)
    if bib is None:
        return {}
    out: dict = {"bibliographic_reference": biblib.compose_reference(bib)}
    if bib.is_unverified():
        out["bibliographic_reference_unverified"] = True
    if bib.record_id:
        out["bibliographic_record_id"] = bib.record_id
    return out


def sync_bibliography(cfg: Config, conn, doc_id: int | None = None,
                      verbose: bool = False) -> dict:
    """Refresh stored bibliographic references from document sidecars.

    Cheap and idempotent: a sidecar is re-parsed only when its content hash
    changed. A reference is *metadata*, so this never marks a document for
    re-transcription and never alters its content hash, status or pages —
    editing a sidecar cannot trigger a re-scan.

    A document with no sidecar, or with a malformed/empty one, keeps no
    reference, so `pha cite` falls back to its original filename-only string.
    """
    from . import bibliography as biblib

    if doc_id is not None:
        row = db.get_document(conn, doc_id)
        docs = [row] if row is not None else []
    else:
        docs = conn.execute("SELECT * FROM documents").fetchall()
    stats: dict = {"parsed": 0, "unchanged": 0, "cleared": 0, "warnings": []}
    for doc in docs:
        path, _fmt, warning = biblib.find_sidecar(doc)
        if warning:
            stats["warnings"].append((doc["id"], doc["filename"], warning))
        stored = db.get_bibliography(conn, doc["id"])
        if path is None:
            if stored is not None:
                db.clear_bibliography(conn, doc["id"])
                stats["cleared"] += 1
            continue
        sha = biblib.sidecar_sha(path)
        if (stored is not None and stored["sidecar_sha"] == sha
                and stored["sidecar_path"] == str(path)):
            stats["unchanged"] += 1
            continue
        loaded, err = biblib.load_bibliography(doc)
        if err:
            stats["warnings"].append((doc["id"], doc["filename"], err))
        if loaded is None:
            if stored is not None:
                db.clear_bibliography(conn, doc["id"])
                stats["cleared"] += 1
            continue
        db.set_bibliography(
            conn, doc["id"],
            sidecar_path=str(path),
            sidecar_sha=sha,
            source_format=loaded.source_format,
            citation=biblib.compose_reference(loaded),
            parsed_json=json.dumps(loaded.to_dict(), ensure_ascii=False),
            record_origin=loaded.record_origin,
        )
        stats["parsed"] += 1
    if stats["parsed"] or stats["cleared"]:
        conn.commit()
    if verbose and (stats["parsed"] or stats["cleared"]):
        print(f"  bibliography: {stats['parsed']} parsed, "
              f"{stats['cleared']} cleared", flush=True)
    return stats


def _documents_under(cfg: Config, conn, path: str) -> list | None:
    """Resolve a dropbox-relative subpath to the ingested documents under it.

    Mirrors the `--path` semantics of `pha scan` / `pha edit`, extended to a
    single document file: the target may be a collection dir, a single document
    file, or a directory-of-images. Returns None when the target does not exist
    (the caller reports it and returns an empty result)."""
    root = Path(path)
    if not root.is_absolute():
        root = cfg.dropbox / root
    root = root.resolve()
    if root != cfg.dropbox.resolve() and cfg.dropbox.resolve() not in root.parents:
        if not root.exists():
            print(f"  target path does not exist: {path}", flush=True)
            return None
    if root.is_file():
        units = [root] if is_supported(root.name) else []
    else:
        units = discover(cfg.dropbox, cfg.dir_documents, root=root, exclude=[cfg.inbox])
    docs: list = []
    seen: set[int] = set()
    for f in units:
        d = db.get_document_by_path(conn, str(f))
        if d is None or d["id"] in seen:
            continue
        seen.add(d["id"])
        docs.append(d)
    return docs


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

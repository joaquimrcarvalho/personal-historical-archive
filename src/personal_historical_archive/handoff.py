"""Two-machine hand-over: lend a document, take the results back.

The archive machine can sleep for hours while a second, always-on machine on
the same LAN does the work. This module implements that round trip:

    pha handoff out    <targets> -o DIR    # lease + export (archive machine)
    pha handoff in     DIR                 # import + resume    (worker machine)
    pha handoff work   DIR                 # scan -> edit -> encode (worker)
    pha handoff back   DIR -o DIR2         # build the result    (worker)
    pha handoff fetch  DIR2                # apply in place      (archive)
    pha handoff status / cancel            # what is out, and releasing it

The payload is a directory; moving it (rsync, a share, a USB stick) is the
user's business. Both machines are awake at hand-out and at fetch by
definition, which is what makes a file-based lease sufficient.

Design of record: `enhancements/pha-handoff-enhancement-request.md`. Two
properties drive most of the code:

- **Identity is content, never ids.** Documents join on `sha256` (+ `relpath`
  as a human cross-check), so a returned result updates the local document in
  place: same `documents.id`, same slug, same citations. The worker's ids are
  meaningless here and are discarded on apply.
- **A `*waiting*` stub is the absence of a page, not content.** A partially
  processed document exports its un-extracted pages as stubs; they must never
  be imported as text (see `bundle._is_waiting_stub`), so the document stays
  short of its page count and the ordinary resume path finishes it.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import db
from .bundle import (
    _copy_dropbox_payload,
    _document_units,
    _install_defs,
    _is_waiting_stub,
    _parse_library_file,
    _resolve_target,
    export_bundle,
)
from .config import Config
from .ingest import sha256_of, sha256_of_dir
from .sidecar import resolve_sidecar

HANDOFF_FORMAT = "pha-handoff"
HANDOFF_VERSION = 1

MANIFEST_NAME = "handoff.json"
RESULT_NAME = "handoff-result.json"

STATE_OUT = "out"
STATE_APPLIED = "applied"
STATE_CANCELLED = "cancelled"


class HandoffError(RuntimeError):
    """A hand-off could not be created, imported or applied."""


# --------------------------------------------------------------------------- lease

def handoff_dir(cfg: Config) -> Path:
    """Where this archive records what it has lent out.

    `<archive>/.pha/` is the machine-local, gitignored area that also holds
    `location.json`. A lease is a fact about THIS machine's copy — it must not
    travel inside the archive, so it lives here and not in `library/`.
    """
    return cfg.archive_dir / ".pha" / "handoffs"


def lease_path(cfg: Config, handoff_id: str) -> Path:
    return handoff_dir(cfg) / f"{handoff_id}.json"


@dataclass
class Lease:
    """One hand-out: what is out, to whom, and since when."""

    handoff_id: str
    worker: str = ""
    state: str = STATE_OUT
    created_at: float = 0.0
    documents: list[dict] = field(default_factory=list)  # {sha256, relpath, doc_id}

    def shas(self) -> set[str]:
        return {d["sha256"] for d in self.documents if d.get("sha256")}

    def age_s(self, now: float | None = None) -> float:
        if not self.created_at:
            return 0.0
        return max(0.0, (now or time.time()) - self.created_at)


def new_handoff_id(label: str = "") -> str:
    """A sortable id, e.g. `DI-vol04-20260920T2130Z`."""
    stamp = time.strftime("%Y%m%dT%H%MZ", time.gmtime())
    base = "".join(
        c if (c.isalnum() or c in "-_") else "-" for c in (label or "handoff")
    ).strip("-")
    return f"{base or 'handoff'}-{stamp}"


def write_lease(cfg: Config, lease: Lease) -> Path:
    d = handoff_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    p = lease_path(cfg, lease.handoff_id)
    payload = {
        "format": HANDOFF_FORMAT,
        "version": HANDOFF_VERSION,
        "handoff_id": lease.handoff_id,
        "state": lease.state,
        "worker": lease.worker,
        "created_at": lease.created_at or time.time(),
        "documents": lease.documents,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def _lease_from_payload(payload: dict) -> Lease:
    return Lease(
        handoff_id=str(payload.get("handoff_id") or ""),
        worker=str(payload.get("worker") or ""),
        state=str(payload.get("state") or STATE_OUT),
        created_at=float(payload.get("created_at") or 0.0),
        documents=list(payload.get("documents") or []),
    )


def read_lease(cfg: Config, handoff_id: str) -> Lease | None:
    p = lease_path(cfg, handoff_id)
    if not p.exists():
        return None
    try:
        return _lease_from_payload(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def active_leases(cfg: Config) -> list[Lease]:
    """Every hand-out still OUT (applied/cancelled ones are history)."""
    d = handoff_dir(cfg)
    if not d.is_dir():
        return []
    out: list[Lease] = []
    for f in sorted(d.glob("*.json")):
        lease = read_lease(cfg, f.stem)
        if lease is not None and lease.state == STATE_OUT:
            out.append(lease)
    return out


def leased_shas(cfg: Config) -> dict[str, Lease]:
    """{sha256: Lease} for every document currently out on hand-over.

    The pipeline consults this to skip a lent document. Keyed by content
    because that is the only identity that means the same on both machines.
    """
    out: dict[str, Lease] = {}
    for lease in active_leases(cfg):
        for sha in lease.shas():
            out.setdefault(sha, lease)
    return out


def lease_for_document(cfg: Config, doc, conn=None) -> Lease | None:
    """The active lease holding this document, if any.

    `doc` is a document row (or a dict with `sha256`); `conn` is unused and
    accepted so call sites in the pipeline can pass their connection without
    special-casing.
    """
    if doc is None:
        return None
    try:
        sha = doc["sha256"]
    except (TypeError, KeyError, IndexError):
        return None
    return leased_shas(cfg).get(sha)


def release(cfg: Config, handoff_id: str, state: str) -> Lease:
    lease = read_lease(cfg, handoff_id)
    if lease is None:
        raise HandoffError(f"no hand-off {handoff_id!r} recorded in this archive")
    lease.state = state
    write_lease(cfg, lease)
    return lease


def _fmt_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def status_lines(cfg: Config) -> list[str]:
    """Human-readable 'what is out' lines (also feeds `pha status`)."""
    leases = active_leases(cfg)
    if not leases:
        return []
    lines: list[str] = []
    now = time.time()
    for lease in leases:
        where = f" to {lease.worker}" if lease.worker else ""
        lines.append(
            f"{lease.handoff_id}{where} — {len(lease.documents)} document(s), "
            f"out {_fmt_age(lease.age_s(now))}"
        )
        for d in lease.documents:
            lines.append(f"    {d.get('relpath') or (d.get('sha256') or '?')[:12]}")
    return lines


# ------------------------------------------------------------------ hand-out (out)

def _label_for(target: str) -> str:
    name = Path(str(target).rstrip("/")).name
    return name or "handoff"


def _doc_sha(cfg: Config, path: Path) -> str:
    return sha256_of_dir(path) if path.is_dir() else sha256_of(path)


def _annotation(cfg: Config, conn, path: Path, rel: str, sha: str) -> dict:
    """The per-document hand-off record: identity + config + resume point."""
    doc = db.get_document_by_path(conn, str(path))
    done = []
    if doc is not None:
        done = [
            str(r["source_name"] or f"{int(r['page_no']):04d}")
            for r in conn.execute(
                "SELECT page_no, source_name FROM pages WHERE document_id = ? "
                "AND status = 'done' AND raw_text IS NOT NULL ORDER BY page_no",
                (int(doc["id"]),),
            )
        ]
    return {
        "relpath": rel,
        "sha256": sha,
        "doc_id": int(doc["id"]) if doc is not None else None,
        "page_count": int(doc["page_count"] or 0) if doc is not None else 0,
        "palaeographer": doc["palaeographer"] if doc is not None else None,
        "editor": doc["editor"] if doc is not None else None,
        "config_signature": _config_signature(cfg, path, doc),
        "pages_done": done,
    }


def _config_signature(cfg: Config, path: Path, doc=None) -> str:
    """The resolved configuration a document is processed with (for R9).

    Enough to answer "is the worker's output stale under MY current config?"
    at apply time. Encoded as one comparable string; a failure to resolve is
    reported as unknown rather than aborting the hand-out.

    The document's own recorded stages seed the signature, so it is never
    empty for a document that has been processed — a sidecar may be absent
    even when the archive recorded a palaeographer/editor on the row.
    """
    parts: list[str] = []
    if doc is not None:
        parts += [
            f"doc.pal={doc['palaeographer'] or ''}",
            f"doc.palmodel={doc['palaeographer_model'] or ''}",
            f"doc.ed={doc['editor'] or ''}",
            f"doc.edmodel={doc['editor_model'] or ''}",
        ]
    try:
        sc = resolve_sidecar(cfg.dropbox, path if path.is_dir() else path.parent)
    except Exception:  # noqa: BLE001 - a signature is diagnostic, never fatal
        return "|".join(parts)
    if sc.palaeographer is not None:
        parts.append(f"pal={sc.palaeographer.rules}")
        parts.append(f"palmodel={sc.palaeographer.model}")
        parts.append(f"palpost={_filter_names(sc.palaeographer.post)}")
    if sc.editor is not None:
        parts.append(f"ed={sc.editor.rules}")
        parts.append(f"edmodel={sc.editor.model}")
        parts.append(f"edpre={_filter_names(sc.editor.pre)}")
        parts.append(f"edpost={_filter_names(sc.editor.post)}")
    for enc in (sc.encoders or []):
        parts.append(f"enc={enc.rules}:{enc.model}")
    return "|".join(parts)


def _filter_names(specs) -> str:
    return ",".join(
        f"{s.name}:{json.dumps(s.params, sort_keys=True)}" for s in (specs or [])
    )


def _drop_stub_pages(library_root: Path) -> int:
    """Remove `*waiting*` placeholder files from an exported payload.

    The worker must not receive a placeholder as if it were a page: with the
    file absent, the imported document is short of its page count, stays
    `processing`, and `pha scan` resumes the missing pages. Returns how many
    files were dropped (reported to the operator).
    """
    if not library_root.is_dir():
        return 0
    dropped = 0
    for f in sorted(library_root.rglob("*.md")):
        parsed = _parse_library_file(f)
        if parsed is None:
            continue
        fm, body = parsed
        if _is_waiting_stub(fm, body):
            f.unlink(missing_ok=True)
            dropped += 1
    return dropped


def export_handoff(
    cfg: Config,
    targets: list[str],
    out: Path,
    worker: str = "",
    force: bool = False,
    verbose: bool = True,
) -> dict:
    """Lease the targets and export a hand-out payload.

    Reuses `pha bundle` for the payload (definitions actually used, collection
    sidecars, finished library files) and then:

    - refuses a target already leased, unless `force` supersedes that hand-out,
    - drops the render cache (the worker re-renders from source) and the stub
      pages (so the worker resumes rather than importing placeholders),
    - writes `handoff.json` — the bundle manifest plus the hand-off fields —
      and removes `manifest.json`, so a reader cannot mistake the payload for a
      plain bundle, and
    - records the lease in `<archive>/.pha/handoffs/`.
    """
    out = Path(out)
    if not targets:
        raise HandoffError("no targets given")
    worker = (worker or "").strip()

    conn = db.connect(cfg.db_path)
    try:
        already = leased_shas(cfg)
        docs: list[dict] = []
        for t in targets:
            resolved = _resolve_target(cfg, t)
            if resolved is None:
                raise HandoffError(f"target not found in the dropbox: {t}")
            for unit in _document_units(cfg, resolved):
                if not unit.exists():
                    continue
                rel = str(unit.relative_to(cfg.dropbox))
                sha = _doc_sha(cfg, unit)
                docs.append(_annotation(cfg, conn, unit, rel, sha))
        if not docs:
            raise HandoffError("no documents found for the given targets")

        clash = [d for d in docs if d["sha256"] in already]
        if clash:
            ids = sorted({already[d["sha256"]].handoff_id for d in clash})
            names = ", ".join(d["relpath"] for d in clash)
            if not force:
                raise HandoffError(
                    f"already out on hand-over {'/'.join(ids)}: {names} "
                    f"(use --force to supersede)"
                )
            for old in ids:
                release(cfg, old, STATE_CANCELLED)
                if verbose:
                    print(f"  superseded hand-over {old}", flush=True)

        handoff_id = new_handoff_id(_label_for(targets[0]))
        res = export_bundle(cfg, targets, out, force=force, verbose=verbose)

        renders = out / "renders"
        if renders.is_dir():
            shutil.rmtree(renders)
        dropped = _drop_stub_pages(out / "library")

        payload = {
            "format": HANDOFF_FORMAT,
            "version": HANDOFF_VERSION,
            "handoff_id": handoff_id,
            "worker": worker,
            "origin_archive": str(cfg.archive_dir),
            "created_at": time.time(),
            "documents": docs,
            "stubs_dropped": dropped,
        }
        (out / MANIFEST_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "manifest.json").unlink(missing_ok=True)

        lease = Lease(
            handoff_id=handoff_id,
            worker=worker,
            state=STATE_OUT,
            created_at=time.time(),
            documents=[
                {"sha256": d["sha256"], "relpath": d["relpath"], "doc_id": d["doc_id"]}
                for d in docs
            ],
        )
        write_lease(cfg, lease)
        return {
            "handoff_id": handoff_id,
            "out": str(out),
            "documents": docs,
            "stubs_dropped": dropped,
            "bundled": res,
        }
    finally:
        conn.close()


# --------------------------------------------------------- worker import (in)

def read_manifest(handoff_dir_path: Path) -> dict:
    """Read and validate a hand-out payload's manifest."""
    p = Path(handoff_dir_path) / MANIFEST_NAME
    if not p.exists():
        raise HandoffError(
            f"not a pha hand-off (no {MANIFEST_NAME}): {handoff_dir_path}"
        )
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HandoffError(f"unreadable {p}: {e}") from e
    if payload.get("format") != HANDOFF_FORMAT:
        raise HandoffError(
            f"not a pha hand-off (format {payload.get('format')!r}): {handoff_dir_path}"
        )
    if int(payload.get("version") or 0) > HANDOFF_VERSION:
        raise HandoffError(
            f"hand-off version {payload.get('version')} is newer than this pha "
            f"understands ({HANDOFF_VERSION}); upgrade the worker's pha"
        )
    return payload


def import_handoff(
    cfg: Config, handoff_dir_path: Path, verbose: bool = True, dry_run: bool = False,
) -> dict:
    """Import a hand-out into the WORKER archive and leave it resumable.

    This is `pha unbundle`'s opposite in three ways, and each one matters:

    - the document is created or **reused by `sha256`** (repeated hand-overs of
      one document do not multiply it),
    - pages that arrived are imported as `done`; every page the manifest
      expected but the payload did not carry becomes a **`waiting` row** (with
      no text), so the document is short of its page count and the worker's
      `pha scan` finishes it instead of skipping it,
    - a `*waiting*` placeholder is never imported as text, even if an older
      pha wrote one into the payload.

    No renders travel, so the worker renders from source exactly as a normal
    scan does. Returns a summary dict.
    """
    handoff_dir_path = Path(handoff_dir_path)
    payload = read_manifest(handoff_dir_path)
    handoff_id = str(payload.get("handoff_id") or "")
    docs = list(payload.get("documents") or [])

    if dry_run:
        return {
            "handoff_id": handoff_id,
            "dry_run": True,
            "documents": [
                {"relpath": d.get("relpath"), "sha256": d.get("sha256"),
                 "page_count": d.get("page_count"), "pages_done": len(d.get("pages_done") or [])}
                for d in docs
            ],
        }

    installed = _install_defs(cfg, handoff_dir_path, verbose=verbose)
    copied, skipped = _copy_dropbox_payload(cfg, handoff_dir_path, force=False, verbose=verbose)

    conn = db.connect(cfg.db_path)
    results: list[dict] = []
    try:
        for d in docs:
            res = _import_one(cfg, conn, handoff_dir_path, d, verbose=verbose)
            if res is not None:
                results.append(res)
        conn.commit()
    finally:
        conn.close()
    return {
        "handoff_id": handoff_id,
        "installed_defs": installed,
        "dropbox": {"copied": copied, "skipped": skipped},
        "documents": results,
    }


def _payload_library_dir(handoff_dir_path: Path, doc: dict) -> Path | None:
    """The payload's library folder for one document (newest version wins).

    Version folders are `<stem>_<date>` and a re-scan adds a new one, so take
    the newest — mirroring `_library_doc_dir()` on the archive side.
    """
    root = Path(handoff_dir_path) / "library"
    if not root.is_dir():
        return None
    rel = Path(str(doc.get("relpath") or ""))
    # library folders are named from the document PATH's stem: a document
    # `vol04.pdf` lives at `vol04_<date>/`, a directory-of-images document
    # `vol04/` at `vol04_<date>/`. Either way the stem has no extension.
    stem = rel.stem
    base = root / rel.parent
    real = [p for p in base.glob(f"{stem}_*") if p.is_dir()]
    plain = base / stem
    if plain.is_dir():
        real.append(plain)
    if not real:
        return None
    real.sort(key=lambda p: p.stat().st_mtime)
    return real[-1]


def _import_one(cfg: Config, conn, handoff_dir_path: Path, doc: dict,
                verbose: bool = True) -> dict | None:
    """Import one handed-out document: reuse by sha, resume by page.

    Creates the document row when the worker has never seen this content, and
    reuses it otherwise (so handing the same document over twice does not
    multiply rows). Renders are absent by design, so the worker's `pha scan`
    will render and transcribe whatever is still `waiting`.
    """
    rel = str(doc.get("relpath") or "")
    sha = str(doc.get("sha256") or "")
    src = cfg.dropbox / rel
    if not src.exists():
        raise HandoffError(f"payload is missing the source document {rel}")

    existing = None
    if sha:
        existing = conn.execute(
            "SELECT id, status FROM documents WHERE sha256 = ?", (sha,)
        ).fetchone()
    if existing is not None:
        doc_id = int(existing["id"])
    else:
        is_dir = src.is_dir()
        doc_id = db.add_document(
            conn,
            filename=src.name,
            path=str(src),
            sha256=sha or _doc_sha(cfg, src),
            size_bytes=src.stat().st_size,
            mtime=src.stat().st_mtime,
            kind="dir" if is_dir else ("pdf" if src.suffix.lower() == ".pdf" else "image"),
            now=time.time(),
            dir_path=str(Path(rel).parent) if str(Path(rel).parent) != "." else "",
            palaeographer=doc.get("palaeographer"),
            editor=doc.get("editor"),
        )
        conn.commit()

    lib = _payload_library_dir(handoff_dir_path, doc)
    pages_done = 0
    stubs = 0
    if lib is not None:
        for tdir in sorted(p for p in lib.glob("transcription-*") if p.is_dir()):
            for f in sorted(tdir.glob("*.md")):
                parsed = _parse_library_file(f)
                if parsed is None:
                    continue
                fm, body = parsed
                pno = fm.get("page")
                if pno is None:
                    continue
                if _is_waiting_stub(fm, body):
                    stubs += 1  # an older pha may still have shipped one
                    continue
                source_name = fm.get("source_name")
                if not source_name and f.stem != f"page-{int(pno):03d}":
                    source_name = f.stem
                page_id = db.add_page(conn, doc_id, int(pno), source_name=source_name)
                db.set_page_result(conn, page_id, raw_text=body)
                if fm.get("reviewed"):
                    conn.execute(
                        "UPDATE pages SET reviewed_at = ? WHERE id = ?",
                        (time.time(), page_id),
                    )
                pages_done += 1

    expected = int(doc.get("page_count") or 0)
    # Every expected page gets a row, so the resume path knows what is missing
    # and the document cannot look complete while pages are outstanding.
    for n in range(1, expected + 1):
        db.add_page(conn, doc_id, n)
    if expected:
        db.update_document(conn, doc_id, page_count=expected)
    have = conn.execute(
        "SELECT COUNT(*) n FROM pages WHERE document_id = ? AND status = 'done'",
        (doc_id,),
    ).fetchone()["n"]
    status = "done" if (expected and have >= expected) else "processing"
    db.set_document_status(conn, doc_id, status)
    conn.commit()
    if verbose:
        print(
            f"  + {rel} -> #{doc_id} ({status}, {have}/{expected} pages done"
            + (f", {stubs} placeholder(s) ignored" if stubs else "") + ")",
            flush=True,
        )
    return {
        "id": doc_id,
        "relpath": rel,
        "status": status,
        "pages_done": int(have),
        "page_count": expected,
        "stubs_ignored": stubs,
    }

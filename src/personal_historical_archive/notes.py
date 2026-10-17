"""The archive's `notes/` folder as **memory**, indexed so search can use it.

A note is what a researcher writes *after* reading the sources: an
identification ("Zumbae = Sumba"), a rejected hypothesis, a draft narrative.
That makes the notes the one part of the archive whose value is cumulative —
an answer worked out in March should inform a question asked in October —
and until this module existed it was the one part search could not see.

Notes are still **not pipeline output**: `pha scan` / `pha edit` /
`pha encode` never read or write them, and they are never `documents` rows
(no fake collection, no exports, no bundles). They live in their own `notes` +
`notes_fts` tables and are only ever found by search, marked `kind: "note"` so
a caller cannot mistake a conclusion for evidence.

Freshness is decided by content, not by a schedule: `index_notes` walks
`<notes>/*.md`, hashes each file and touches only what changed; `ensure_indexed`
is the cheap version search calls so a note written a minute ago is already
findable. Embeddings are best-effort — a note is indexed text-only when the
embed model is busy or unreachable, and `pha notes index` fills the vectors in.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from . import db, locks
from .embed import pack, prefixed
from .model_client import ModelClient, ModelError

# The folder's own instructions; never a research note.
EXCLUDED_NAMES = frozenset({"readme.md"})

# A note hit carries its text so an agent can answer without another call, but
# a few notes on this archive are 300 kB. Cap the payload and let the caller
# read the file for the rest.
NOTE_TEXT_CHARS = 20000


def note_files(cfg) -> list[Path]:
    """The research notes: top-level `*.md`, sorted by name.

    Deliberately not recursive. `notes/` may hold tool state in subfolders
    (`notes/copilot/copilot-conversations/…`, `.obsidian/`), and those are not
    the researcher's conclusions. A leading `_` or `.` marks a template or a
    partial draft, like the `_sample.md` convention elsewhere in the archive.
    """
    root = Path(cfg.notes)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(root.glob("*.md")):
        if not path.is_file():
            continue
        if path.name.lower() in EXCLUDED_NAMES or path.name.startswith(("_", ".")):
            continue
        out.append(path)
    return out


def note_title(text: str, fallback: str) -> str:
    """The note's own title: front-matter `title:`, else the first `# ` heading."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:60]:
            stripped = line.strip()
            if stripped == "---":
                break
            if stripped.lower().startswith("title:"):
                value = stripped.split(":", 1)[1].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if value:
                    return value.strip()
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("# "):
            heading = line[2:].strip()
            if heading:
                return heading
    return fallback


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_front_matter(text: str) -> tuple[str, str]:
    """(front matter, body) — a leading YAML block is metadata, not prose.

    Notes on this archive follow the Obsidian convention: a `---` block holding
    `title`, `tags`, `sources`, `aliases`, then the prose. Searching the prose
    first matters for more than tidiness — `snippet()` shows the first match, so
    a note that tags itself `timor` would otherwise answer a Timor query with a
    screen of YAML instead of the paragraph that discusses Timor.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for i, line in enumerate(lines[1:80], start=1):
        if line.strip() == "---":
            return "".join(lines[:i + 1]), "".join(lines[i + 1:])
    return "", text


def body_text(text: str) -> str:
    """The prose of a note: no front matter, no leading HTML comments."""
    _meta, body = split_front_matter(text)
    body = body.lstrip()
    while body.startswith("<!--"):
        end = body.find("-->")
        if end == -1:
            break
        body = body[end + 3:].lstrip()
    return body


def search_text(text: str, title: str) -> str:
    """What the FTS row holds: title, then prose, then the metadata block.

    Order is the point. The body comes before the tags/sources block so a hit
    on prose produces a prose snippet; front matter stays searchable (an
    `aliases:` entry is how a note is found under another spelling), and a
    metadata-only match degrades to a metadata snippet rather than vanishing.
    """
    meta, body = split_front_matter(text)
    parts = []
    if title:
        parts.append(title)
    if body.strip():
        parts.append(body.strip())
    if meta:
        parts.append(meta.strip())
    return "\n\n".join(parts) if parts else text


def body_start(text: str, limit: int = 220) -> str:
    """First prose characters of a note, for a hit with no FTS snippet."""
    body = body_text(text).strip()
    return (body or text.strip())[:limit]


def _rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {row["name"]: row for row in db.all_notes(conn)}


def plan(cfg, conn: sqlite3.Connection, force: bool = False) -> dict:
    """What indexing would do, without writing: {changed, removed, unchanged}.

    Change is decided by **content** (sha256), not mtime: restoring a note from
    a backup, or a sync tool touching it, must not re-embed 40 kB of text for
    nothing. `force` re-indexes every note regardless.
    """
    rows = _rows(conn)
    changed: list[dict] = []
    unchanged = 0
    seen: set[str] = set()
    for path in note_files(cfg):
        name = path.stem
        seen.add(name)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
        except OSError:
            # One unreadable note must not stop the pass; it is simply not
            # indexed, and the next run retries it.
            continue
        sha = _sha256(text)
        row = rows.get(name)
        if row is not None and not force and row["sha256"] == sha and row["path"] == str(path):
            unchanged += 1
            continue
        changed.append({
            "name": name,
            "path": str(path),
            "title": note_title(text, name),
            "mtime": stat.st_mtime,
            "size_bytes": stat.st_size,
            "sha256": sha,
            "text": text,
        })
    removed = [row for name, row in rows.items() if name not in seen]
    return {"changed": changed, "removed": removed, "unchanged": unchanged}


def index_notes(cfg, conn: sqlite3.Connection, embed_client: ModelClient | None = None,
                force: bool = False, verbose: bool = False) -> dict:
    """Bring the notes index up to date. Returns a small report dict.

    Order matters, for the same reason as `index_document`: vectors are
    computed **before** any row is written. A note whose text changed has no
    valid vector any more, so a failed embed degrades it to text-only (it is
    still findable by keyword) instead of leaving a stale vector pointing at
    wording that no longer exists.
    """
    if not db.notes_table_exists(conn):
        # An older archive opened read-only: the tables arrive on the next
        # write-capable command (`pha search` builds them, see ensure_indexed).
        return {"changed": 0, "removed": 0, "indexed": 0, "embedded": 0,
                "text_only": 0, "unchanged": 0, "error": "notes table not created"}

    work = plan(cfg, conn, force=force)
    changed = work["changed"]
    removed = work["removed"]
    embed_error: str | None = None
    to_embed = changed
    missing: list[sqlite3.Row] = []
    if embed_client is not None and (cfg.embed_model or "").strip():
        missing = db.notes_missing_embeddings(conn, cfg.embed_model)
        # A note already in `changed` is written fresh, so it is not also in
        # `missing`; embedding the two groups together would embed it twice.
        changed_ids = {item["name"] for item in changed}
        missing = [r for r in missing if r["name"] not in changed_ids]
        to_embed = changed + [{"name": r["name"], "text": r["text"], "id": r["id"]}
                              for r in missing]
    vectors: dict[str, bytes] = {}
    if embed_client is not None and to_embed and (cfg.embed_model or "").strip():
        try:
            vecs = embed_client.embed(
                cfg.embed_model,
                [prefixed(cfg.embed_model, item["text"], "doc") for item in to_embed],
                batch_size=cfg.embed_batch_size,
            )
            for item, vec in zip(to_embed, vecs):
                if vec:
                    vectors[item["name"]] = pack(vec)
        except ModelError as e:
            embed_error = str(e)
            if verbose:
                print(f"  warning: note embeddings unavailable ({e}); "
                      f"indexing notes text-only", flush=True)

    for item in changed:
        blob = vectors.get(item["name"])
        db.upsert_note(
            conn, name=item["name"], path=item["path"], title=item["title"],
            mtime=item["mtime"], size_bytes=item["size_bytes"],
            sha256=item["sha256"], text=item["text"], embedding=blob,
            embed_model=cfg.embed_model if blob else None,
            fts_text=search_text(item["text"], item["title"]),
        )
        if verbose:
            state = "embedded" if blob else "text-only"
            print(f"  note: {item['name']} ({item['size_bytes']} B, {state})", flush=True)
    for row in removed:
        db.delete_note(conn, int(row["id"]))
        if verbose:
            print(f"  note: {row['name']} removed from the archive", flush=True)
    # Notes whose text did not change but whose vector is missing or from
    # another model: those cannot be written by `upsert_note` (which needs the
    # whole record), so they get a targeted update.
    filled = 0
    for row in missing:
        blob = vectors.get(row["name"])
        if blob:
            db.set_note_embedding(conn, int(row["id"]), blob, cfg.embed_model)
            filled += 1
    if changed or removed or filled:
        conn.commit()
    indexed = len(changed) + filled
    return {
        "changed": len(changed),
        "removed": len(removed),
        "indexed": indexed,
        "embedded": sum(1 for item in changed if vectors.get(item["name"])) + filled,
        "text_only": sum(1 for item in changed if not vectors.get(item["name"])),
        "unchanged": work["unchanged"],
        "error": embed_error,
    }


def ensure_indexed(cfg, embed_client: ModelClient | None = None,
                   allow_embed: bool = False, verbose: bool = False) -> dict | None:
    """Cheap freshness pass for a query: returns a report, or None if current.

    `pha search` is a query command, so it must not pay the cost of a write on
    every call. This probes first with a read-only connection and only opens a
    write connection when a note actually appeared, changed or vanished.

    Embeddings are offered only when the embed server is free (or the caller
    passed `--force`): the command already refuses to load a model a running
    scan is using for the *query* vector, and the same rule has to hold for the
    notes it is indexing.
    """
    # Never load the embed model under a running job just to vectorise notes:
    # that is the same contention rule the query vector already follows.
    client = None
    if embed_client is not None and (
        allow_embed or not locks.job_running(cfg, locks.embed_key(cfg))
    ):
        client = embed_client
    try:
        ro = db.connect(cfg.db_path, readonly=True)
    except sqlite3.Error as e:
        return {"error": f"could not open the archive database ({e})"}
    try:
        if not db.notes_table_exists(ro):
            work = {"changed": [], "removed": [], "unchanged": 0}
            missing: list = []
        else:
            work = plan(cfg, ro)
            # Missing vectors are work only when somebody can fill them: a
            # keyword search must not open a write connection just to notice
            # that the notes are text-only.
            missing = (db.notes_missing_embeddings(ro, cfg.embed_model)
                       if client is not None else [])
    except (sqlite3.Error, OSError) as e:
        ro.close()
        return {"error": f"could not read the notes index ({e})"}
    ro.close()
    if not work["changed"] and not work["removed"] and not missing:
        return None
    try:
        rw = db.connect(cfg.db_path)
    except sqlite3.Error as e:
        return {"error": f"notes not indexed: the archive database is busy ({e})"}
    try:
        res = index_notes(cfg, rw, embed_client=client, verbose=verbose)
    except sqlite3.Error as e:
        return {"error": f"notes not indexed ({e})"}
    finally:
        rw.close()
    return res


def stats(cfg, conn: sqlite3.Connection) -> dict:
    """How much of `notes/` is searchable: files, indexed rows, what is pending.

    `stale` is measured with the SAME rule the indexer uses (`plan`: content,
    not mtime), so a backup that only touches timestamps does not make
    `pha status` claim the notes are out of date when a search would not touch
    them.
    """
    files = note_files(cfg)
    indexed = (db.notes_stats(conn) if db.notes_table_exists(conn)
               else {"indexed": 0, "embedded": 0})
    stale = removed = 0
    if db.notes_table_exists(conn):
        try:
            work = plan(cfg, conn)
            stale = len(work["changed"])
            removed = len(work["removed"])
        except (sqlite3.Error, OSError):
            stale = removed = 0
    return {"files": len(files), "indexed": indexed["indexed"],
            "embedded": indexed["embedded"], "stale": stale, "removed": removed}


def describe(res: dict | None) -> str | None:
    """A one-line, human-readable report for `pha search`'s `note:` line."""
    if not res:
        return None
    if res.get("error") and not res.get("changed"):
        return f"notes not indexed: {res['error']}"
    parts = []
    if res.get("changed"):
        parts.append(f"{res['changed']} note(s) indexed")
    if res.get("removed"):
        parts.append(f"{res['removed']} removed")
    if not parts:
        return None
    if res.get("error"):
        parts.append(f"text-only (no embeddings: {res['error']})")
    return ", ".join(parts) + " for search"



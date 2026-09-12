# Enhancement request — stable page addresses, citations, and render serving

**Status:** implemented — `src/personal_historical_archive/addresses.py` (slug /
rel path / render / variant set), `pha cite`, the `pha page --json` fields and
`pha serve`; tests in `tests/test_addresses.py`, `tests/test_cli_cite.py` and
`tests/test_serve.py`.
**Author/date:** archive work session (Jesuit archive; Obsidian vault integration).
**Relates to:** `WEB_INTERFACE_PLAN.md` (adds the render endpoint its API surface
lacks), `pha-encoder-tools-enhancement-request.md` (same "make generated output
consumable outside pha" concern — here the output is a *citation*), and the
`dsh-pha` plugin's `/pha/pageImage` route, whose lookup logic this proposal lifts
into pha.

## 1. Motivation

pha generates a per-page transcription, an edited variant per editor/model, and a
rendered page image per source page. All three are reachable from the CLI, but
**none of the addresses is stable across a re-process**, so pha's own agent
guidance tells authors not to link to them at all:

> Keep the footnote as this plain pointer — don't embed a file path or a
> wikilink, because library paths carry the version date and go stale when a
> document is re-processed. (`config.py:1483`)

That advice is correct for hand-written paths. Its cost is that the archive cannot
be *cited by link* from anything outside it — an Obsidian vault, a thesis note, a
web page. The concrete goal: a research note whose footnote opens the cited page
image, and which still works after the volume is re-scanned.

Why each address pha exposes today fails:

| Address | Example | Stable? |
|---|---|---|
| `documents.id` | `22` | **no** — on a content change `ingest_file` calls `db.delete_document(...)` and re-inserts, so the id increments |
| library page file | `library/…/…_2026-09-08/edited-…/page-437.md` | **no** — `_doc_slug` is `<stem>_<created_at>`; a re-scan creates a new dated folder |
| render image | `renders/<sha256>/p437.jpg` | **no** — keyed on the content hash; `remove_render_if_orphaned` deletes the old dir |
| dropbox-relative source path + page no. | `collections/…/vol04_1548-1550.pdf` p. 437 | **yes** — changes only on rename/move |

Two further gaps make the problem worse for any external consumer:

- **`pha page --json` does not report the render or the variant set.** It returns
  `document_id, filename, collection, source, page_no, variant, editor,
  palaeographer, reviewed, page_file, text`. A consumer wanting the page image must
  re-derive `documents.sha256`, join `pages.source_name`, and guess variant
  directory names (`transcription-<pal>[@model]`, `edited-<editor>[@model]`).
  Re-implementing that outside pha is how it drifts.
- **There is no stable URL for a render anywhere.** `WEB_INTERFACE_PLAN.md`'s API
  surface (`/api/status`, `/api/scan`, `/api/prompts`, …) has no image route. The
  `dsh-pha` plugin implements one privately (`/pha/pageImage`), but it returns a
  base64 `data:` URL — which note tools cannot embed from Markdown image syntax.

## 2. Proposed feature

### 2.1 `slug` — the stable public identity of a document

Define one canonical slug and expose it in `pha page --json`, `pha status`,
`pha info` and the MCP payloads:

```
slug = <dropbox-relative path>
         drop a leading "collections/" segment
         minus the file extension
         lowercased
         every run of non-alphanumeric characters → '-'
         leading/trailing '-' trimmed
```

One worked example, followed exactly by every implementation:

```
collections/pfister-notices/Notices_…Pfister_Louis_t1.pdf
→ pfister-notices-notices-pfister-louis-t1
```

(Only the `collections/` segment is dropped; the rest of the path, including the
noisy filename stem, is kept so the rule stays mechanical — two implementations
must derive the *same* string without judgment. An out-of-tree consumer that has
already invented its own shorter slug must adopt this rule, not the reverse.)

No creation date, no hash, no doc id. The contract to document: *the slug derives
from the path, so it survives re-processing and is invariant under content
changes; renaming or moving the source document changes it.*

Also surface the **dropbox-relative path** itself. Today `--json` gives an
absolute `source` and a `collection`, but not the relative path the slug is
derived from, so a consumer cannot compute a stable identity without string
surgery on a machine-specific absolute path.

### 2.2 `pha cite` — a citation that names the exact variant

```
$ pha cite 47 51 --edited
Pfister, Notices t1 — doc 47, p. 51 (edited: french-ocr@deepseek-v4-flash)
  slug:  pfister-notices-notices-pfister-louis-t1
  page:  51
  file:  …/edited-french-ocr@deepseek-v4-flash/page-051.md
```

`--json` for machine consumers. Rules worth pinning down:

- **A variant with no text is not a citation.** `--edited` must resolve to a
  *filled* variant and exit non-zero if the only match is empty. The hazard is
  real, but it sits one layer below `cite` today: `page_edits` is keyed on
  `(page_id, editor)` with **no model column** (`db.py:44-54`), and the
  `edited-<editor>@<model>` directory is export-time naming only (`ingest.py:937`).
  So on document 47 page 51 the library holds a 391-byte
  `edited-french-ocr/page-051.md` reading `*waiting*` next to a filled
  `edited-french-ocr@deepseek-v4-flash/page-051.md`, while
  `pha page 47 51 --edited` currently returns the filled DB text (exit 0) and
  reports a `page_file` that points at the *stub*. The archive already contains
  notes citing "doc 47, p. 51 (edited)" (`notes/coimbra-in-pfister.md:124`) —
  ambiguous between those two directories, so a resolver that guesses can
  silently cite nothing. Making the variant set explicit (§2.3) is what gives
  `cite` an unambiguous thing to check; the emptiness rule follows from that.
- When several variants are filled and neither `--edited` nor a
  `--palaeographer`/`--editor` flag disambiguates, list them and require a choice
  rather than picking one silently.
- Output must be stable enough to paste into a footnote and machine-parseable
  enough for a note generator to consume.

### 2.3 `pha page --json` gains the render and the variant set

Additive fields; existing keys unchanged:

```json
{
  "slug": "pfister-notices-notices-pfister-louis-t1",
  "rel_path": "collections/pfister-notices/Notices_…_t1.pdf",
  "sha256": "31bb2217…",
  "render": "/…/renders/31bb2217…/p051.jpg",
  "render_exists": true,
  "variants": {
    "transcription-ocr@liteparse-fra":        {"filled": true,  "file": "…"},
    "edited-french-ocr":                      {"filled": false, "file": "…"},
    "edited-french-ocr@deepseek-v4-flash":    {"filled": true,  "file": "…"}
  }
}
```

This is the change that lets external tools stop encoding schema knowledge.

### 2.4 `pha serve` — read-only HTTP endpoint with stable URLs

```
pha serve [--host 127.0.0.1] [--port 8765]
```

| method | path | returns |
|---|---|---|
| GET | `/doc/{slug}/p{page}.jpg` | the render JPEG, resolving the current sha **per request** |
| GET | `/doc/{slug}/meta.json` | rel_path, page count, variant set, current sha |
| GET | `/health` | `{ok, archive, docs, last_db_mtime}` |

Implementation notes:

- **Read-only.** `archive.db` opened `immutable=1` — the mode `dsh-pha` already
  uses to open this WAL database without `-wal`/`-shm`. No route mutates anything;
  mutations stay on the existing `scan`/`edit`/`review` lock semantics.
- **Resolve `slug → current sha256` per request**, caching the map keyed on DB
  mtime, so a re-scan is picked up *without a restart* and the URL never changes.
  This is the whole point: the consumer's markdown is written once.
- **Named-image fallback.** Where a document's pages were exported as named
  images rather than `p{NNN}.jpg`, fall back to `pages.source_name + '.jpg'` — the
  same two-candidate lookup as `dsh-pha/lib/index.js:485-487`.
- **Loopback by default.** `--host 0.0.0.0` is an explicit opt-in for LAN/mobile
  access and should log a warning, since it exposes the archive to the network.
- `/pha/pageImage` in `dsh-pha` should be re-pointed at (or replaced by) this
  route, and should return bytes rather than a data URL once it exists.

**Why a server and not a `file://` path.** A `file://` embed genuinely works in
Obsidian desktop — a bare absolute path fails with *"Not allowed to load local
resource"*, and adding the scheme fixes it. But it embeds
`renders/<sha256>/pNNN.jpg` in the consumer's markdown, so every re-process
rewrites the consumer's files, and the image is broken in the window between a
re-scan and the next regeneration. A stable URL moves that volatility behind the
endpoint, which is the same indirection pha already applies elsewhere (e.g.
prompt composition). It is also the only form that works on mobile, where
`file://` does not resolve at all.

## 3. Interim fallback (no repo change needed today)

Everything above is computable out-of-tree from `archive.db` read-only: a small
script can derive the slug, resolve `doc`/`page`/variant to the current render,
emit the footnote and serve the bytes — no pha release required. That is a
reasonable stopgap while §2 is unimplemented.

Treat it as a stopgap only. The fallback **duplicates schema knowledge that
belongs in pha** — the `documents.sha256` → `renders/<sha>/`
convention, the `pages.source_name` join, and the `edited-<editor>[@model]` /
`transcription-<pal>[@model]` directory grammar. Every one of those is internal
and can change; when it does, out-of-tree consumers break silently and the
`:1483` guidance becomes true again by accident.

## 4. Reference example (what the consumer generates)

An Obsidian page note produced by an out-of-tree consumer:

```markdown
---
archive_slug: pfister-notices-notices-pfister-louis-t1
page: 51
variants: ["edited-french-ocr@deepseek-v4-flash", "transcription-ocr@liteparse-fra"]
render_url: "http://127.0.0.1:8765/doc/pfister-notices-notices-pfister-louis-t1/p051.jpg"
---
# Pfister, *Notices* t1 — p. 51

![](http://127.0.0.1:8765/doc/pfister-notices-notices-pfister-louis-t1/p051.jpg)

## Edited — french-ocr@deepseek-v4-flash
…

## Transcription — ocr@liteparse-fra
…
```

and the footnote in the research note that links to it:

```markdown
[^1]: Pfister, *Notices* t1, p. 51 (edited: french-ocr@deepseek-v4-flash) —
      [[Archive/Pages/pfister-notices-notices-pfister-louis-t1/p051|p. 51]] ·
      `pha page 47 51 --edited`
```

Note the URL contains neither a date nor a hash, while the note still records the
exact variant — which is what makes the citation both durable and falsifiable.

## 5. Tests

- `slug` is unchanged by `--reprocess` and by a content change; a rename changes
  it (document this as intended, not as a regression);
- `pha cite --edited` exits non-zero when the only matching variant is empty, and
  lists candidates when several are filled;
- `pha page --json` includes `slug`/`rel_path`/`sha256`/`render`/`variants` and
  keeps every existing key;
- `pha serve` resolves the **new** sha after a reprocess with no restart;
- `/doc/{slug}/p{n}.jpg` 404s for an unknown slug, and for a page beyond
  `page_count`;
- loopback binding by default; `--host 0.0.0.0` logs the exposure warning;
- the two-candidate render lookup still finds named images where `p{NNN}.jpg` is
  absent.

## 6. Non-goals

- **No full web UI.** That is `WEB_INTERFACE_PLAN.md`; `pha serve` is read-only
  and serves bytes plus metadata only.
- **No Obsidian-specific code or vault paths in pha.** The vault bridge stays in
  the archive; pha provides the stable identity and the endpoint.
- **No writes through the server.**
- **No on-disk renaming.** Library and render layout is untouched; the slug is a
  derived, exposed identifier, not a new directory name.

## Reference implementation notes

- **The Obsidian/vault bridge itself is out of scope here.** This request is only
  about what pha must expose (the slug, `cite`, the richer `--json`, `serve`); any
  bridge or consumer is a separate, archive-local workstream and pha must not
  depend on it.
- Motivating archive data: document 47 (`Notices … Pfister_Louis_t1.pdf`, sha
  `31bb2217…`, 618 pages) and its page 51, whose library holds an empty
  (`status: waiting`) `edited-french-ocr` beside a filled
  `edited-french-ocr@deepseek-v4-flash` — the ambiguity §2.2 exists to prevent.

# Enhancement request — raw and edited text in the `pha serve` page viewer

**Status:** stored for later implementation. Not implemented. (Plan/draft for discussion; no code changes yet.)
**Author/date:** archive work session.
**Relates to:** `pha-page-navigation-enhancement-request.md` (its §4 deliberately
settled on a render-only viewer; this request reverses that decision now that
the served citation should be a self-contained reading surface),
`pha-stable-page-addresses-enhancement-request.md` (same stable
`/doc/<slug>/p<page>` address), `WEB_INTERFACE_PLAN.md` (the separate browser
management UI this must not become), and `notes/README.md` / the archive docs
(the text shown here must match what `pha page` and the MCP tools say).

## 1. Motivation

`pha serve` currently has one page-reading route:

| route | today |
|---|---|
| `/doc/<slug>/p<NNN>` | HTML viewer: page render + prev/next/first/last + jump box |
| `/doc/<slug>/p<NNN>.jpg` | bare JPEG |
| `/doc/<slug>/meta.json` | document metadata |

The viewer was deliberately built as an image-only navigation surface: its
`_viewer` docstring says *"Deliberately shows the render ONLY — the PHA view
remains the place to read the transcription text"*, and the navigation
enhancement request records that as a settled decision. That made sense while
the only alternative was the desktop PHA view. It is now a gap:

- A citation placed in a note opens a picture of a page but cannot show the
  transcribed words the note is actually based on. A reader has to switch to
  the CLI (`pha page`) or the GUI to see what the page says.
- The archive already stores the text: raw transcription in
  `pages.raw_text`, edited/translated readings in `page_edits.text`.
- `pha_get_page` (MCP) and `pha page` already expose those readings, including
  the *effective* edited reading (human-reviewed → pinned → document editor).
  The HTTP viewer is the one surface that does not.
- A served footnote should be legible on a tablet/phone/LAN browser without the
  archive client; the image alone is not enough once the page is difficult to
  read, and the raw/edited split is exactly what a historian needs to judge.

This request makes the viewer a **reading surface**: image + raw text + the
served edited text if one is available. It stays read-only, dependency-free,
and on the same stable URL.

## 2. Proposed feature

### 2.1 Same address, richer page

`GET /doc/<slug>/p<NNN>` keeps its address, navigation, headers and missing-
render behaviour. Inside the main area it gains, beside the image:

1. **Raw transcription** — `pages.raw_text` for that page. Always shown when a
   page record and non-empty text exist.
2. **Edited reading** — the reading `db.effective_edit_for_page()` selects:
   a human `reviewed_at` row first, then a deliberate `pinned_at` per-page
   override, then the document's configured editor row. Shown only when the row
   is `done` and its `text` is non-empty; empty `*waiting*` stubs must never be
   rendered as if they were a reading.
3. **Provenance** — a small muted line under each heading, e.g.
   `transcription: ocr@liteparse-fra · reviewed` or
   `edited: modern-portuguese@deepseek-v4-flash · pinned`. Use the same
   `addresses.variant_label` grammar as `pha page` / `pha cite`, and use the
   page's stored per-page palaeographer/editor/model when present.
4. **Empty / pending states** — if raw text is absent, say so with the page
   status (`waiting`, `pending`, …) rather than silently showing nothing. If no
   filled edited reading exists, omit the edited section; a `muted` sentence may
   name the editor that still has to produce it, but an empty edited panel is not
   rendered.

No new user-facing route is required for the HTML feature. The existing `.jpg`
route stays byte-for-byte unchanged so notes that embed the image keep working.

### 2.2 Reading scope: DB, not library files

The viewer should read the same source as `pha page`, `pha cite` and the MCP
tools: the database. Reasons:

- library filenames carry variant aliases (`edited-X` beside `edited-X@Y`) and
  the dated document folder; the DB is the canonical resolved reading;
- a human edit may exist in a library file but not yet have been imported by
  `pha review`; serving it through HTTP would present un-imported text as the
  archive's official reading, contradicting `pha page`;
- `pages.raw_text` and `page_edits.text` are already the rows the search index,
  citation tool and encoder consume;
- no path traversal or file-size concerns are introduced.

A human correction already in the DB (`reviewed_at`) is part of the effective
edit and must be shown with its badge; this feature therefore surfaces review
work rather than hiding it.

### 2.3 Layout

Desktop: two panes, image left and text right; the text pane scrolls
independently so a long transcription does not push the image away. Mobile
(<= ~900 px): image first, text below, no horizontal overflow.

Suggested structure (CSS only; no JS needed for the panels):

```html
<main class="reader">
  <section class="reader-media">
    <img class="page" src="/doc/<slug>/p<NNN>.jpg" alt="…">
  </section>
  <section class="reader-text">
    <section class="reading">
      <h2>Transcription <span class="muted">raw</span></h2>
      <p class="provenance">transcription: ocr@liteparse-fra</p>
      <pre class="transcript">…escaped raw text…</pre>
    </section>
    <section class="reading" id="edited">
      <h2>Edited reading</h2>
      <p class="provenance">edited: modern-portuguese@deepseek-v4-flash · reviewed</p>
      <pre class="transcript">…escaped edited text…</pre>
    </section>
  </section>
</main>
```

- Render text with `html.escape()` inside `<pre class="transcript">` with
  `white-space: pre-wrap; overflow-wrap: anywhere`. Do **not** render Markdown,
  raw HTML or the library file's YAML front matter.
- Give the scrollable transcript `tabindex="0"` so keyboard users can scroll it.
- Keep the existing header/footer, navigation controls, position and jump box.
- Keep the no-render state: if the image is missing, show the existing notice
  and still render the text panels and working prev/next controls.

### 2.4 Multiple edited readings (optional, recommended)

`page_edits` may contain several `done` editors for one page. The default
display should remain the single effective/served reading to match `pha cite`
and avoid presenting a secondary model's guess as the archive's reading.
For completeness, a collapsed `<details><summary>Other edited readings (N)</summary>`
can list the other filled rows with their provenance. This is additive and can
be deferred without blocking the main feature.

### 2.5 Optional page JSON endpoint

The HTML is for humans. Add one machine route beside `meta.json`:

| route | returns |
|---|---|
| `/doc/<slug>/p<NNN>.json` | page-level JSON (raw, effective edit, provenance, nav and render URLs) |

Shape (additive; no existing route changes):

```json
{
  "ok": true,
  "slug": "colx-d",
  "rel_path": "collections/COLX/d.pdf",
  "page": 3,
  "page_count": 10,
  "raw": "…",
  "raw_status": "done",
  "transcription": {"id": "ocr", "model": "liteparse-fra",
                    "pinned": false, "reviewed": true},
  "edited": {"editor": "modern-portuguese",
             "model": "deepseek-v4-flash",
             "text": "…", "pinned": false, "reviewed": false},
  "prev_page": 2,
  "next_page": 4,
  "render_url": "/doc/colx-d/p003.jpg",
  "viewer_url": "/doc/colx-d/p003"
}
```

Why include it: `meta.json` is document-level only, and MCP is not available to
plain HTTP consumers. This route gives note generators and tests one stable way
to fetch the same reading. If it is considered scope creep, defer it to a second
phase; the HTML viewer does not depend on it.

## 3. Implementation notes

### 3.1 `serve.py` data helper

`_source_name()` already opens a read-only connection per page request. Extend
that pattern into a helper used by `_viewer()` (image requests can stay
lightweight) rather than adding a second connection:

```python
def _page_reading(self, doc: dict, page: int):
    """Return {page row + effective edit} or (None, reason). Never raises."""
    try:
        conn, _ = _connect_ro(cfg.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM pages WHERE document_id=? AND page_no=?",
                (doc.get("id"), page)).fetchone()
            if row is None:
                return None, "no page record"
            edit = db.effective_edit_for_page(
                conn, row["id"], db.row_get(doc, "editor"))
            return {"row": row, "edit": edit}, None
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return None, str(exc)
```

Rules:

- use `db.row_get(row, …)` for columns a read-only connection may not have
  migrated (`source_name`, `palaeographer`, `palaeographer_model`, `pinned_at`,
  `reviewed_at`), per `AGENTS.md`'s read-only-query rule;
- treat only `status == "done"` and a non-empty `text` as an available edit;
- a failed text read must not turn the viewer into a 500: catch `sqlite3.Error`,
  log it, render the image/nav and a small "page text temporarily unavailable"
  notice;
- do not cache page text in `_Index`: it would duplicate a potentially large
  column and complicate reload; one indexed row per page request is cheap.

### 3.2 DB hardening

`db.reviewed_edit_for_page()` (`db.py`, around line 724) queries `reviewed_at`
without the `has_column` guard that `pinned_edit_for_page()` already uses. On a
legacy archive opened read-only (no migration) this will now be reached by every
viewer request and would fail. Add the same guard and return `None` when the
column is absent. This is a small, independent fix that the feature makes
reachable.

### 3.3 CSS and keyboard interaction

- Replace the viewer's centered `main` layout with a `main.reader` grid; keep
  `@media (max-width:900px)` stacking.
- The existing keydown script maps ArrowLeft/Right to page navigation and only
  skips `INPUT`/`TEXTAREA`. Add `pre` (and any future transcript control) to that
  guard, or check `e.target.closest("[data-keys-off]")`, so arrow keys can
  scroll a focused transcript instead of flipping the page.
- Keep the existing CSP. Escaped text contains no executable markup; no
  `unsafe-inline` requirement changes.

### 3.4 Route changes

For the optional JSON route, add a regex beside `_PAGE_RE`/`_VIEW_RE`:

```python
_PAGE_JSON_RE = re.compile(r"^(?P<slug>.+)/p(?P<page>\d+)\.json$")
```

and dispatch it before or after `_PAGE_RE` as long as `.jpg`/`.jpeg` still win.
`meta.json` stays document-level; optionally add an additive
`page_text_url: "/doc/<slug>/p{page}.json"` template.

### 3.5 Error and degraded states

| case | behaviour |
|---|---|
| page record missing | 200 viewer, existing render logic + "No page record" text notice, nav still works |
| raw text empty, page done | 200, raw section says "empty page / no text stored"; edited section if available |
| raw text empty, page waiting/error | 200, status named, no fake text |
| no filled edited row | 200, edited section omitted (optional muted one-line hint) |
| DB text read fails / `_connect_ro` degrades | 200, image + nav still served; "text unavailable" notice; details to stderr, not the page |
| page outside `1..page_count` | unchanged 404 HTML |
| `.jpg` request | unchanged; no text is read or returned |

## 4. Backward compatibility

- `/doc/<slug>/p<NNN>` still has the same address and navigation semantics; the
  HTML body now contains the text. No consumer should have depended on the exact
  body, but the addition is what the request asks for.
- `/doc/<slug>/p<NNN>.jpg` and `/doc/<slug>/meta.json` remain untouched.
- No schema migration, no new dependency, no new asset, no JavaScript required.
- Read-only behaviour is preserved: the helper opens `mode=ro`/`immutable=1`
  exactly like the existing routes and never writes.
- The old "viewer is image-only" decision in
  `pha-page-navigation-enhancement-request.md` must be marked superseded in the
  same change; otherwise the docs contradict the implementation.

## 5. Tests to add

Extend `tests/test_serve.py` (the fixture already sets raw text for every page;
`_db.set_page_edit`/`mark_edit_reviewed` can add edited rows):

- **raw + edited shown**: viewer body contains the raw text and the effective
  edited text, with their provenance labels.
- **raw only**: with no `page_edits`, raw text is present and no empty "Edited
  reading" panel is emitted.
- **effective priority**: with a document editor row and a human-reviewed row
  from another editor, the reviewed text is the one served; a pinned row loses
  to a reviewed row.
- **waiting edit hidden**: a `status='waiting'` / empty edit is not rendered as
  a reading.
- **escaping**: raw and edited text containing `<script>`, `&`, `<>` is escaped;
  no executable markup appears.
- **missing render**: page with text but no image returns 200, contains the text
  and the existing "No render" notice, and keeps prev/next.
- **DB read failure**: monkeypatch the page-reading helper/`_connect_ro` to
  raise; viewer still returns 200 with image/nav and a text-unavailable notice.
- **`.jpg` unchanged**: image response bytes and content type are unchanged by
  the text feature.
- **page JSON** (if shipped): route returns raw, effective edit, provenance,
  nav and URLs; unknown slug / bad page 404; no `page_edits` yields
  `edited: null`.
- **legacy DB**: `reviewed_edit_for_page()` returns `None` when `reviewed_at`
  is absent (drop/rename the column in a test DB opened read-only), and the
  viewer still renders raw text.

Existing navigation, overview, meta, CSP/cache and bibliography tests should
continue to pass unchanged.

## 6. Docs to update

- `src/personal_historical_archive/serve.py`: module docstring and `_viewer`
  docstring (remove "render ONLY").
- `README.md`: "Paging a document" prose and the route table — viewer now shows
  render **plus raw transcription and served edited reading when available**.
- `src/personal_historical_archive/archive_init.py` (`ARCHIVE_README_MD`, around
  the "Writing a note" / viewer description): same change, because dedicated
  archives are refreshed with it.
- `src/personal_historical_archive/config.py` (`_NOTES_README_MD`, around line
  2216) and the repo's `notes/README.md`: say the viewer carries the raw and
  edited readings. Note that `notes/README.md` is seeded once and an existing
  archive's copy is user-owned; a feature note in the changelog/README is enough
  for old archives.
- `enhancements/pha-page-navigation-enhancement-request.md`: mark its §4
  render-only decision as superseded by this request.
- `enhancements/pha-enhancement-requests-INDEX.md`: add this document to the
  open-proposals table when accepted for implementation.
- Optional: `dsh-pha/README.md`'s comparison table if it claims the served
  viewer is image-only.

## 7. Suggested implementation order

1. **DB guard** — harden `reviewed_edit_for_page()` for legacy read-only DBs.
2. **Helper** — add `_page_reading()` in `serve.py`; keep `_image()` untouched.
3. **HTML/CSS** — render raw/edited panels into `_viewer()`; update keyboard
   guard; handle missing text and DB failure.
4. **Tests** — cover text rendering, escaping, effective priority, missing
   render, degraded read, and legacy DB.
5. **Docs** — serve docstring, README, archive/notes templates, superseded
   decision note.
6. **Optional** — page JSON endpoint and "other edited readings" disclosure.

## 8. Non-goals

- No in-browser editing, review import, or any write route; `pha serve` stays
  read-only.
- No Markdown renderer or HTML preview of library files; transcriptions are
  displayed as plain preformatted text.
- No dashboard, no web-management UI, no new dependency or build step.
- No change to slugs, `/p<NNN>.jpg`, or existing `meta.json` keys.
- No replacement for `pha page` / the PHA view; this is the citation landing
  surface.
- No attempt to show un-imported library-file edits as if they were the
  archive's served reading.

## 9. Open questions

1. **One edited reading or all?** Recommended default is the effective/served
   one; the optional `<details>` list is there for users who want every filled
   model reading.
2. **Empty edited slot**: omit the panel (recommended) or show a muted "no
   edited reading yet" line with the editor id?
3. **Page JSON endpoint**: ship with this feature or defer? It is small and
   makes the text useful to HTTP clients that are not MCP-aware.
4. **Text size limit**: no server-side truncation by default (the page is the
   source and `pha page` prints it all). A pathological multi-megabyte page can
   be handled later with a cap + "download full text" link if real archives show
   one.
5. **`?view=image` / text toggle**: not proposed; the `.jpg` route already
   serves the image-only use case, so the HTML can carry the reading.

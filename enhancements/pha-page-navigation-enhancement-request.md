# Enhancement request — page navigation for served citations

**Status:** plan / draft for discussion.
**Author/date:** archive work session (Jesuit archive; Obsidian integration).
**Relates to:** `pha-stable-page-addresses-enhancement-request.md` — this is its
follow-on: that work made a citation *land* on a page; this makes the landing
**navigable**. Also `notes/README.md` (the footnote/link convention),
`WEB_INTERFACE_PLAN.md` (the dashboard — deliberately not this), and the
`dsh-pha` PHA view, whose reader already navigates inside the GUI; this brings
the same ability to the served citation.

## 1. Motivation

A footnote links to `/doc/{slug}/p437.jpg`: a static JPEG. It answers "show me
p. 437" and nothing else:

- **no next/previous** — continuing to read means hand-editing the URL
  (437 → 438 → …), one page at a time, in the address bar;
- **no position** — "p. 437" but not "437 of 638", so the reader cannot tell how
  far the document runs or whether p. 437 is near the start;
- **no way back to the beginning** — nothing links to p. 1, so "flip back to the
  start of the volume" is another hand-edited URL;
- **a missing render is a dead end** — `.jpg` returns 404 with no way to step
  over the gap.

The PHA view already solves this for the GUI: its reader builds a page list from
the document and jumps between pages on click (`dsh-pha/lib/client.js:636-683`).
But the **citation path** — the whole point of stable addresses — dead-ends.
Agents have the same gap: `pha_get_page` returns one page with no `page_count`
and no neighbours, so "read the next page" first requires looking the document
up.

A JPEG cannot carry navigation, so the answer is a small HTML viewer served next
to the raw image.

## 2. Proposed feature

### 2.1 The page viewer — `GET /doc/{slug}/p{N}`

New extensionless route beside the existing `/p{N}.jpg`:

- the page image (the same render — no re-encoding), captioned `p. N of M` with
  the filename and `rel_path`; **the render only** — no transcription text
  beside it (settled: the PHA view stays the text reader);
- **navigation**: ◀ prev · next ▶ · ⤒ first · ⤓ last, a `page __ ›` jump form,
  and keyboard ←/→ (plus j/k), Home/End;
- **plain links + a form, so it works with JavaScript disabled**; JS only adds
  the keyboard shortcuts and neighbour prefetch (`<link rel="prefetch">` or a
  hidden `<img>` for p±1) so flipping feels instant;
- secondary links: the raw JPEG (`.jpg`), the document overview
  (`/doc/{slug}/`), and the citation text `` `pha cite <doc> <page>` ``;
- `Cache-Control: no-cache` and a strict
  `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'`
  (no external asset, no CDN — the viewer works offline);
- **all metadata HTML-escaped** — real filenames here contain `[`, `]`, `...`
  and `&`.

Boundaries and errors:

| case | behaviour |
|---|---|
| `N = 1` | prev/first rendered disabled (not dead links) |
| `N = M` | next/last disabled |
| unknown slug, or `N` outside `1..M` | **404 HTML** with a link to the overview |
| `N` in range but the render is missing | **200** with "no render for this page" and **working prev/next** — navigation never dead-ends (contrast the `.jpg` route, which 404s) |

### 2.2 The document overview — `GET /doc/{slug}/`

- filename, `rel_path`, page count, variant set and current sha (the `meta.json`
  data), and "start reading at p. 1";
- a **page-range list** (`1–50`, `51–100`, …) plus a jump box — cheap, and it
  works for a 1,383-page volume where a full grid would not. **Settled: this is
  the overview** — no thumbnail grid (40 lazy-loaded full renders is ~20 MB and
  buys little over a range list);
- optional: previous/next **document** in the same collection (alphabetical), so
  reading can continue across volumes — not decided, low stakes.

### 2.3 Land citations in the viewer

`pha cite` prints the viewer URL, and the notes' footnotes link to the
**viewer** rather than the raw JPEG:

```
— [p. 51](http://127.0.0.1:8765/doc/<slug>/p051) · `pha cite 47 51 --edited`
```

- `/p{N}.jpg` stays exactly as it is: existing notes keep working, and the
  viewer links to it.
- **Obsidian caveat**: a note cannot embed the viewer — Obsidian sanitises HTML
  and blocks iframes — so an *inline image* stays `![](…/p051.jpg)` while the
  *link* opens the viewer in the browser. That split is the point: embed =
  picture, link = reading surface.
- **migration is settled**: the 109 footnote links in the four existing notes
  (`coimbra-in-pfister.md`, `malaca.md`, `afonso-mexia.md`,
  `francisco-estrada.md`) are re-pointed to the viewer with a scripted rewrite
  (drop the `.jpg` extension), reversible from the timestamped
  `.notes-backup-*` directory beside `notes/`. **Sequencing matters: migrate
  after the viewer route ships**, so no note ever points at a 404 in the
  meantime.

### 2.4 Programmatic navigation (agents, GUI, note generators)

Additive fields, existing keys unchanged:

- `GET /doc/{slug}/meta.json` → `pages: {first, last}`, plus `page_url` and
  `viewer_url` templates (it already returns `render_url`);
- MCP `pha_get_page` (`mcp_server.py:93`) → `page_count`, `prev_page`,
  `next_page`, `page_url`, `viewer_url`;
- `pha page --json` → `page_count`, `prev_page`, `next_page`, `page_url`, so an
  agent can walk a document without a second lookup;
- `pha cite` (`cli.py:229`) → a `url:` line carrying the viewer URL.

### 2.5 One source for the base URL

A `serve: {host: 127.0.0.1, port: 8765}` block in the archive config, so
`pha serve`, `pha cite` and the docs agree on the base URL. Today `8765` is
hard-coded in the notes' links and in callers of `addresses.render_url`.

## 3. Rejected alternatives

- **A viewer page to embed in Obsidian.** Obsidian sanitises HTML and disallows
  iframes; an embed can only ever be the image.
- **A PDF viewer.** The archive keeps per-page renders, not a PDF per document
  version; adding one adds a file type and a route for no gain over prev/next.
- **A dashboard / web app.** That is `WEB_INTERFACE_PLAN.md`. This is one
  document, two routes, no build step.
- **Navigation inside the JPEG.** Impossible.
- **Fixing it in the PHA view only.** The GUI already navigates; the citation is
  what dead-ends.

## 4. Decisions

Settled:

- **Overview = a page-range list** plus the jump box — not a thumbnail grid (a
  grid can be added later if the range list proves too coarse).
- **The viewer shows the render only**, with no transcription text beside it —
  the PHA view remains the text reader.
- **The 109 existing links are migrated** to the viewer route, in all four notes
  — but only **after** the viewer ships, so no note points at a 404 meanwhile.

Low stakes, still open:

- Neighbour prefetch on by default? (It doubles requests; harmless on loopback.)
- Cross-link to the PHA view of the same document (different origin/port) —
  default no.
- Previous/next document on the overview — optional extra.

## 5. Tests to add

- viewer: `p{N}` returns HTML containing prev `p{N-1}`, next `p{N+1}`, first/last
  and `N of M`; boundaries render disabled; a filename containing `&`/`<` is
  escaped.
- viewer with a missing render → 200, message present, prev/next still present.
- unknown slug / out-of-range → 404; `.jpg` responses byte-identical to today.
- overview: 200, page count, range links, jump-box bounds.
- `meta.json` additions, and the MCP / `pha page --json` additions; every
  pre-existing key still present.
- migration script: `--dry-run` changes nothing; the applied run re-points
  exactly the footnote links and nothing else.
- headers: `Content-Security-Policy` and `Cache-Control` present.

## 6. Non-goals

- No dashboard or general web UI, no auth, no writes — still read-only and
  loopback by default.
- No new dependency, no build step, no external asset (works offline).
- No change to slugs, to `/p{N}.jpg`, or to the existing `/meta.json` keys.
- Not a text reader — settled: the viewer shows the render only; the PHA view is
  the text reader.

## Reference implementation notes

- `src/personal_historical_archive/serve.py` — `_PAGE_RE` (`33`), `route()`
  (`209`), `_meta()` (`236`), `_image()` (`253`); the degrade-safe
  `_connect_ro` / `_Index` read path (`36+`).
- `src/personal_historical_archive/addresses.py` — `render_path` (`96`),
  `variant_files` (`144`), `render_url` (`193`).
- `src/personal_historical_archive/cli.py` — `cmd_cite` (`229`),
  `cmd_serve` (`1767`).
- `src/personal_historical_archive/mcp_server.py` — `pha_get_page` (`93`).
- `dsh-pha/lib/client.js:636-683` — the GUI's page list; the behaviour to mirror
  for citations.
- Archive data: the four notes carry 109 `.jpg` links; the originals sit in the
  timestamped `.notes-backup-*` directory beside `notes/`.

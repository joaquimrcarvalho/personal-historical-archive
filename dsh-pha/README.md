# dsh-pha — DeepSeek Harness plugin for personal-historical-archive (pha)

> [!TIP]
> Quick-start and what-you-get: see [`DSH_PLUGIN.md`](../DSH_PLUGIN.md) at the repo root.
> This file is the full install/development reference.

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) **host plugin** that
exposes the `pha_*` model tools and the archive runner, so any agent session on a machine
that has `pha` + an archive can drive it from chat.

It is intended to live **inside this repository** and be installed by anyone who has both
the `personal-historical-archive` tool and a DeepSeek Harness install, so that *other
machines / other harnesses pick it up* by installing this package (or referencing this
directory) as a plugin in their harness profile.

## What it provides

Model tools (registered globally, so they are available to the agent in **every** session):

| tool | purpose |
| --- | --- |
| `pha_status` | `pha status` text report |
| `pha_documents` | structured document list (id, filename, dir_path, kind, page_count, status, palaeographer, editor, models) |
| `pha_document` | one document's pages + per-page editor entries |
| `pha_page` | one page's text (raw / `--edited`) |
| `pha_search` | full-text search (hybrid) → structured hits |
| `pha_archive` | archive dir + engine health (`pha doctor`) |
| `pha_job_start` / `pha_job_status` / `pha_job_kill` | run `pha scan/edit/encode/reindex/review/inbox` as background jobs |

All reads go through `pha` (any where a structured/JSON surface exists) or a **read-only,
`immutable=1`** sqlite open of `archive.db` — which is the one mode that opens this WAL
database without `-wal`/`-shm`. Mutations always go through the real `pha` CLI, so pha's own
single-model lock, staleness, and review round-trip semantics are respected.

## Requirements

- A DeepSeek Harness install (`dsh` on PATH) and the profile you want to attach this to
  (the Desktop/`web` profile, or a custom one).
- The `personal-historical-archive` package installed and its CLI on PATH (`pha`), with an
  archive configured (`pha set archive-dir <path>` or `PHA_ARCHIVE_DIR`). The plugin
  discovers the archive from `pha info --json` at runtime (falling back to
  `pha doctor --json`, then `pha status`).

## Install (per machine / per harness)

The harness composes plugins from **profiles** of `cordis` patch layers. To attach this
plugin to a profile there are two parts: make the package resolvable, and add one row.

### 1. Make the package resolvable in the profile

The profile's workspace lives under `$DSH_HOME/profiles/<name>/` (e.g. `desktop`). Add this
package as a dependency (pnpm, since that is how the bundled profiles are laid out):

```sh
# from inside the profile's workspace (where its package.json / pnpm-workspace.yaml live)
pnpm add /path/to/this/repo/dsh-pha
```

If the profile uses a lockfile/workspace layout you prefer not to touch, an alternative is to
copy/symlink the package into the profile's `node_modules`:

```sh
mkdir -p "$DSH_HOME/profiles/<name>/node_modules/@personal-historical-archive"
ln -s /path/to/repo/dsh-pha "$DSH_HOME/profiles/<name>/node_modules/@personal-historical-archive/dsh-pha"
```

> [!NOTE]
> The plugin has **no dependencies** and imports nothing, on purpose: a linked install is
> resolved to this repository, so a bare import of a harness package would be looked up here
> — outside the profile's `node_modules` — and fail. Since one unloadable row makes the whole
> harness refuse to start, the host half builds its tool definitions itself (the same raw JSON
> Schema the registry validates) instead of importing `@deepseek-ai/dsh-tools`. `npm run smoke`
> checks that invariant, the eleven tool definitions and the routes offline.

### 2. Add a composition row to the profile

Append to the profile's `cordis.patch.yml` (see `cordis.patch.example.yml` in this folder):

```yaml
- insert:
    - id: dsh-pha
      name: '@personal-historical-archive/dsh-pha'
```

If the file is the stock empty flow array `[]`, replace that `[]` with the block sequence
above instead of appending after it — a patch file is a single top-level YAML array, and
adding a second one there makes the profile fail to load.

### 3. Restart the harness profile

```sh
dsh --profile <name> --help   # confirm it boots; then start the app as you normally do
```

**Which profile?** The one your app actually runs — `install.sh` with no argument picks it
from a running harness process, and prints the choice:

```sh
./install.sh          # detects the running profile (e.g. `web` for DSH Desktop)
./install.sh web      # …or name it explicitly
```

DSH Desktop and `dsh web` run the **`web`** profile. Installing into `desktop` while the app
runs `web` looks successful and does nothing: the plugin is only ever composed from the
profile the process booted.

A patch-layer change needs that restart. A profile may set `dsh.profile.patchReload: "live"`
(the `web` profile does), but the live reload is driven by the Cordis HMR service, which a
packaged app does not compose — so inserted rows land at the next profile start. Rebuilt
*client* bundles are different: the running app stat-polls them and reloads the view without
a restart.

After the profile starts, the eleven `pha_*` tools are registered in the host `tools`
registry and appear in every session's tool catalog. Trigger them like any tool, e.g. ask the
agent: `list the documents`, `show document 1 page 2`, `search for missão`. Mutating actions
(`pha scan/edit/encode/reindex/review/inbox`) can be started with `pha_job_start` and polled
with `pha_job_status`; pha's own lock prevents overlapping local-model jobs.

To confirm the host half is live, ask the agent for `pha_status` — the `pha_*` tools exist only
while the row is active. The same-origin routes answer on the instance's own port (`DSH_WEB_URL`):

```sh
curl -s "$DSH_WEB_URL/pha/documents"
```

A JSON body means the row activated. `404` means it did not (wrong profile, or no restart since the
row was added). `403` means the route exists but that instance requires its session credentials —
not a plugin problem; use the tool call instead. The port differs per launch, so never hard-code it.

### Bibliographic references (sidecars)

A document's reference (`<stem>.dc.json`, `<stem>.bib` or `<stem>.mods.xml`, parsed into one
record by pha) is part of the view:

- the **document header** shows the formatted reference exactly as `pha bib` / `pha cite`
  render it, under the filename;
- a **`bib` chip** marks every document that carries a sidecar — read from *disk*, so it is
  right even on an archive whose DB snapshot has not been refreshed since the sidecar was
  added — amber when the record's own `record_origin` says it is unverified
  (`agent-drafted-unverified`, `fetched-from-zotero-unverified`);
- **Reference** opens the record: every field with content, the sidecar path and format,
  the origin, and an **Edit sidecar** button that opens the sidecar in your editor. MODS is
  never offered for editing (interchange only) — the pane prints the
  `pha bib <doc> --to-json --write` conversion instead;
- `GET /pha/bib?doc=N` is the live read (`pha bib <doc> --json`, which writes nothing), so a
  sidecar added since the last scan shows up immediately. Presence-only, exactly as pha
  defines it: a document without a sidecar shows none, and a reference is never inherited
  from a neighbour.

The document list and the single-document read also carry the stored snapshot
(`document_bibliography`) when an archive has one. That table only exists once a `pha` run
has migrated the archive, and the plugin **never migrates**: it joins the table when it is
present and reports no stored references when it is not, rather than failing the whole
read. `tests/test_dsh_pha_sql.py` runs the plugin's embedded SQL program against both
schemas — the unmigrated one is what a real archive looked like when this was added.

### What is not in the archive yet

The left pane distinguishes three things a reader needs to keep apart, and all of them come
from **one `pha status --json` call** (the CLI's own answer, so the view applies no rules of
its own — a directory-of-images is one document, a sidecar is not a document, the inbox is
excluded from the dropbox walk):

- **documents** — rows in the archive, grouped by collection;
- **`new in dropbox — not scanned`** — in the dropbox but with no row yet. Not selectable
  (there is nothing to read yet), with a count per collection and an **Ask to scan** button
  that drafts *"Scan the dropbox items that are not in the archive yet — 6 document(s):
  inacio-loyola (2), …"* into the message box. The view never starts a scan itself: scanning
  takes a model-server lock;
- **`inbox — on hold`** — parked documents, selectable, with the **Move to Dropbox** action
  (`pha inbox --plan`/`--move` still own that).

Before this, the inbox was read with its own walk and un-scanned dropbox items were invisible
entirely — so a collection that had never been scanned simply did not exist as far as the
view was concerned, which is exactly what made `tacchi-venturi` and `inacio-loyola` impossible
to find.

### The inbox (parked documents)

The left pane lists the archive's `inbox/` — the sibling of `dropbox/` where documents
wait on hold — directly after the dropbox groups. Each entry (a collection directory, or a
single document inside one) is selectable, and selecting it opens a pane with a **Move to
Dropbox** button:

1. the button first asks the CLI for the plan (`pha inbox --dry-run --json`), so the
   confirm step states the real file count and the destination;
2. **Confirm move** performs it (`pha inbox --move [PATH]`), preserving the relative
   layout — `inbox/collections/CAT` becomes `dropbox/collections/CAT`, merging into an
   existing directory rather than nesting — and then re-reads the inbox;
3. the pane reminds you that `pha scan` is what ingests the moved documents.

This is the plugin's **only mutating route** (`POST /pha/inbox/move`, or `?confirm=1` for a
carrier that routes only GET): it moves things *out of the inbox into the dropbox* and
nothing else. The CLI re-validates the path, so it can never reach outside `inbox/` — a
`../` path, an absolute path or a dot-path is refused before anything moves, and a bare GET
is rejected without an explicit confirmation.

### Finding a document by number

Every pha command — and most archive feedback — names a document by its number
(`pha page 18 74`, "doc #18"). The view therefore shows that number on every row of the
document list and of the search results (`#18  [status]  filename`), and the search box
accepts it directly: typing **`#18`** opens document 18 instead of searching. A number that
does not exist says so in the list rather than silently searching.

Bare digits remain a full-text search on purpose — `1553` is a year and a perfectly good
query — so only the explicit `#` form means "this document". (`src/client/query.js`,
unit-tested by `node scripts/check-view.mjs`.)

### Ask about what you are reading

The harness's **composer belongs to the session shell**, so the message box is already there
while the PHA view is active — you can ask a question without leaving the reading pane. What
was missing was the context, which the view now supplies itself: a conversation target
receives the input shell's `inputActions` and `useInput` as standard props, and
`inputActions.setDraft(...)` is the same call the shell uses to restore a stored draft.

**Ask in Chat** drafts a citation-accurate header for what is on screen —
`Context: DOCUMENTA-INDICA-1550-1553.pdf (doc 19), page 496 — edited (latin-to-english@…) variant`,
plus the document's reference (marked `[unverified reference…]` when the sidecar says so) and
the `pha page`/`pha document` command to recover the full text. It **drafts rather than
sends**, appending below anything you have already typed, so you can edit it first.

It stays a *pointer* deliberately: the agent has the `pha_page` tool and the archive's own
rule is to recover the full page before answering, so naming the exact page and variant beats
pasting text that goes stale when a historian corrects the page. If a future harness stops
handing views the input props, the button falls back to putting the same text on the
clipboard instead of failing silently (`src/client/askcontext.js`, unit-tested by
`node scripts/check-view.mjs`).

### The reading position survives a tab switch

The harness renders one conversation view at a time, so switching to Chat **unmounts** the
PHA view and React state dies with it — the document and page you were reading used to be
forgotten. The client now keeps one snapshot per session in module memory (`src/client/viewmemory.js`,
unit-tested by `node scripts/check-view.mjs`): the document + page + raw/edited variant, an
open note, a definition, an inbox item, search results, the txt/md override, the image/text
toggles and the splitter width.

On the way back the *selection* is seeded synchronously (so nothing flashes empty) while the
*content* is re-fetched — never cached — so a restored page always shows current text. The
snapshot lives for the life of the page: a full reload starts fresh, and two sessions in the
same page remember their own positions.

### Links in the notes view

`notes/` files are read as Obsidian-compatible markdown, and every link shape the
archive's own conventions produce is routed deliberately:

| in a note | in the viewer |
|---|---|
| `[[Note]]`, `[[Note|alias]]`, `[[Note.md]]` | opens that note; matching is case-, space- and accent-insensitive (`[[the strait]]` finds `The-Strait.md`) |
| `[[Note#Heading]]`, `[text](#id)` | opens the note (if needed) and scrolls to the heading |
| `[[missing-note]]` | rendered as an *unresolved* (amber, dotted) link instead of failing silently |
| `[p. 437](http://127.0.0.1:8765/doc/<slug>/p437)` — a `pha cite` footnote | opens **that page of that document in this view** (image + text), resolved by the same slug rule `pha serve` uses — no `pha serve`, no leaving the harness |
| `[other](other-note.md)` | opens `other` in the notes viewer |
| any other `http(s)://`, `mailto:` | a new tab, so the harness page (and the conversation) stays put |

Footnote references (`[^n]`) jump to their note and each footnote has a `↩` back to its
reference. The rules live in `src/client/links.js` — dependency-free, unit-tested by
`node scripts/check-links.mjs`, and inlined into `lib/client.js` by the build, so the
served bundle stays one self-contained module. `node scripts/check-render.mjs` renders a
note containing every link shape against a stubbed React and walks the result, which is
what catches a *runtime* mistake in this glue (`node --check` only sees syntax — an
undefined identifier there once blanked the whole view); `npm run check` runs all three
checks.

## GUI (conversation) view

The graphical **PHA view** is preserved in this package as:

- **Host data layer** (`lib/index.js`): same-origin read-only JSON routes under `/pha/*`
  (`/pha/documents`, `/pha/document`, `/pha/page`, `/pha/search`, `/pha/pageImage`,
  `/pha/status`, `/pha/archive`) registered on the harness `webServer` service.
- **Client module source** (`src/client/index.js`): the PHA conversation view (document
  list + search, page reader with raw/edited, side-by-side page image, markdown rendering,
  draggable splitter), registered into `conversation.view`, fetching `/pha/*`.

`package.json` already declares the module (`dsh.client` → `./client`) and the `dsh.client`
inject list. The view ships **self-contained**: the browser bundle `lib/client.js` is
committed in this repo, so after install + restart the PHA view appears in the conversation
header (labelled **PHA**), alongside the `pha_*` tools — no harness client build needed on
the end-user machine.

Install + compose + restart (as above), and the harness's `dsh-client-modules` head half
reads `exports["./client"]` → `lib/client.js` and serves it to the browser.

To rebuild the bundle after editing `src/client/index.js` (packaged as
`dsh-pha/scripts/build-client.mjs`, which compiles the module into the harness client
bundle format that calls `window.__ModuleLoader__.load({ id, factory })`):

```sh
node dsh-pha/scripts/build-client.mjs   # -> lib/client.js
```

then commit `lib/client.js` so the change ships.

> [!WARNING]
> The `dsh.client.inject` list is a best-effort guess against the installed version's
> client module contract (`@deepseek-ai/dsh-client-ui-slots`). If the view doesn't mount,
> adjust that inject list to the client APIs the module actually uses; the host data layer
> and tools are independent of it and work regardless.

## Development

- `lib/index.js` is the host plugin (`export { apply, inject, name }`), ESM, no build step.
- `src/client/index.js` is the client module source; it is compiled to `lib/client.js` by
  `scripts/build-client.mjs` (the harness reads the built bundle, not the source). Keep the
  source using a **named** `export const apply`, and edit `src/client/index.js` then re-run
  the script + commit `lib/client.js`.
- The tool bodies mirror the logic proven in the session-scoped prototype; keep the same
  read-only `immutable=1` DB open and the same pha-CLI mutation path.

## Notes / caveats

- `immutable=1` reads the main DB file and ignores any still-uncheckpointed WAL, so during an
  *active* `pha scan` brand-new pages may not appear until pha checkpoints; `pha_search` is
  always current.
- The plugin assumes `pha` is on the harness process's PATH or at a common install location
  (`~/.local/bin/pha`, `/usr/local/bin/pha`, `/opt/homebrew/bin/pha`); adjust the candidate
  list in `lib/index.js` if needed.

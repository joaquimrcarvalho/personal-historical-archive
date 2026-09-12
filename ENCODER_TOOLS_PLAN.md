# Implementation plan — pha encoders with bundled tools (phases A + B: tool runner + built-in markdown-from-records)

> **SUPERSEDED (2026-09).** This plan was merged into
> [`FILTERS_PLAN.md`](FILTERS_PLAN.md): the "bundled tools" runner is absorbed
> by the stage-filter framework, and `markdown-from-records` becomes its first
> **artifact filter** (`encoder.post`). Kept for history. The context
> contract, the stamp staleness design (incl. edited-page tracking) and the
> overwrite-in-place policy live on in the filters plan; the prescan/`pages:`
> part of the original request is not yet planned.

Design doc for the enhancement proposal, tracked in this repo at
`enhancements/pha-encoder-tools-enhancement-request.md` (byte-identical to
the archive's `<archive_dir>/.writing/` copy; see also
`enhancements/pha-enhancement-requests-INDEX.md`). Written before any code
and **re-verified against pha 0.16.1** (2026-09): the reference encoder files
(`documenta-indica/encoders/{documents,apparatus}.md` + `*.tools/`,
`_tools/`, `prescan/doca_prescan.py`) load cleanly and the proposal's layout
is inert for the current loader.

Anchors below name **functions/regions, not line numbers**, so they cannot
rot as the code moves; the current positions are listed once in §2 as an
informational map.

Scope of THIS plan = **phase A (the tool runner) + phase B (the built-in
`markdown-from-records` tool), implemented together** (proposal §2.1–2.4).
Phase C (structure prescan → layout injection, §3.4) is a separate follow-up
after A+B works on a real volume; §3.1–3.3 of the proposal are separate
encoder-stage changes, listed for reference only.

### Decisions recorded (archive-owner rulings)

- **DEC-1 (scope/approach):** keep the **generic tool runner**; the
  motivating built-in is the per-record "export segments" tool. Its defining
  correctness requirement — everything else follows from it — is that the
  encoder must be able to **reconstruct documents that span pages and that
  start in the middle of a page**, as is common in historical source
  editions. This drives the phase-B reader/spanning logic and its tests
  (§6).
- **DEC-2 (sequencing):** phases **A + B ship together** as one feature;
  phase C comes after (own review cycle).
- **DEC-3 (= D1):** **LLM-backed tools are deferred** — phase A ships
  `type: script` only; a `tools:` item declaring `model:` is validated but
  skipped with a warning (the `structure-document` second-stage tool is
  therefore not part of this work).
- **DEC-4 (= D2):** two-level staleness with stamp files, as designed below.
- **DEC-5 (overwrite policy):** tool outputs use deterministic file names and
  are **overwritten in place** on re-run. They are regenerated artifacts,
  like `renders/` — the human review/correction surface stays the
  `library/` edited-* pages; a correction is applied there and the segments
  are rebuilt (see DEC-6), not hand-edited.
- **DEC-6 (review interplay):** tool staleness **tracks corrections to the
  edited pages**. A page file under `pages_dir_edited` newer than the tool's
  stamp marks the tool stale, so a historian's correction to an edited page
  re-materialises the affected segments on the next `pha encode` — whether
  or not the correction has been imported with `pha review` (the tool reads
  the page files).
- **DEC-7 (defaults adopted, veto anytime):** on tool failure `pha encode`
  still succeeds for the records and reports the tool failure (exit 0; a
  `pha test` report shows it); `pha test` runs script tools by default
  (`--no-tools` to skip); a tool with no `out_dir` defaults it to the tool
  name; tool artifacts are **not** indexed for search (a later enhancement);
  `pha bundle` keeps carrying the whole `encoders/` tree (payloads included)
  while collection-sibling script dirs (`prescan/`) travel only once phase C
  lands. The record JSON format stays unchanged (proposal §4 non-goal).

### Relationship to the other enhancement requests

The repo's `enhancements/pha-enhancement-requests-INDEX.md` groups this with
**stage filters** (`pre`/`post` text filters around a stage's model) and
**`extends`** (base-rules + delta prompt composition), suggested order
filters → extends → encoder tools. They are independent and can land in any
order. The one interaction worth noting: a filter that rewrites edited pages
is exactly the kind of change DEC-6's edited-page mtime tracking detects, so
tools re-materialise after a filter change with no extra work; if filters
land first, tools simply consume the already-filtered edited pages.

## 1. What we build

After `pha encode` has stored a document's records
(`library/<slug>/records-<encoder>.json`) and the concatenated input
(`concatenated-<encoder>.md`), pha runs the encoder's **bundled tools** in
front-matter order. A tool is a directory next to the encoder definition
carrying a `tool.md` manifest + payload; pha invokes it with a small JSON
context describing the document, the records and the library page folders,
and reports per-tool success/failure. Tool runs are **idempotent** via a
stamp file, so a tool-only edit does not force a re-encode of the document
model pass.

### Resolved design decisions

- **D1 — Tool types: phase A ships `type: script` only.** A `tools:` item
  that declares `model:` (the optional LLM-backed `structure-document`
  tool) is validated but **skipped with a warning** ("LLM-backed tools are
  not implemented yet"). The runner contract (context JSON, params, stamping)
  is type-agnostic so `type: llm` can be added later without rework.
- **D2 — Staleness is two-level and the tool level never triggers the model
  pass.** `_encode_needed` (records freshness) stays untouched. Tools run
  when (a) records were just written by this encode call, or (b) any tool
  source file is newer than the tool's last successful run, or (c) any
  edited-page file the tool consumes is newer than the tool's last run
  (DEC-6 — a human correction to an edited page marks the tool stale).
  Freshness is recorded in
  `library/<slug>/.tools-stamps/<encoder>.<tool>.stamp` containing the newest
  mtime (float) of the tool's sources **and the edited pages it reads** at
  the last success; a failed run writes no stamp, so the next `pha encode`
  retries.
- **D3 — Invocation.** Payload entrypoint defaults to `<tool>/<name>.py`
  executed with `sys.executable` (pha's own interpreter — always present);
  a manifest `command:` (string or list) overrides the whole argv. Arguments:
  `--context <file>` with the context JSON (D4). Spawn with a timeout
  (manifest `timeout_s`, default 1800), capture stdout/stderr (last ~2 KB
  kept for reporting), exit code != 0 → tool failed.
- **D4 — Context over stdin is rejected.** `--context <file>` only (a temp
  file removed after the run): portable on Windows, and lets the human run a
  tool interactively by pointing it at a saved context file. The context
  document in the proposal is the spec (encoder, document path,
  records_file, concatenated_file, library_dir, pages_dir_edited,
  pages_dir_raw, params). Fixed keys (encoder/document/records_file/…) are
  **not overridable** by front matter.
- **D5 — Param merging.** Manifest `params:` = defaults; encoder front-matter
  `tools:` item keys (other than `name`) override; merged dict is what the
  context `params` carries. Tool authors read only context `params`.
- **D6 — Security posture.** Tool payloads are arbitrary local code supplied
  by the archive owner — same trust as palaeographer/editor prompts. **No
  sandbox.** Documented in README/AGENTS and in `_ENC_SAMPLE`.
- **D7 — Reporting.** `encode_document`'s result dict gains `"tools":
  [{tool, action: ran|skipped|stale-failed, error?, out_dir?}]`; `pha encode`
  prints one line per tool; `pha encoder <file>` shows each encoder's
  configured tools; `pha test` runs tools into its scratch dir (flag
  `--no-tools` to skip) and lists them in `report.md`.
- **D8 — Bad config never loses records.** Unknown tool name / missing tool
  dir / malformed manifest → warning + tool skipped, records kept, encode
  still reports success for the records.

### Manifest format (`<tool>/tool.md`)

```markdown
---
name: markdown-from-records     # must equal the tool dir name (warning only)
type: script                    # phase A: only "script"
description: one-line summary
command: []                     # optional override of the default argv
timeout_s: 1800
params:                         # defaults; free-form
  out_dir: segments-documents
  page_marker: "--- page N ---"
  footnote_policy: page
---
Human-readable description and usage notes (shown by `pha encoder`).
```

`name`/`type`/`params` are read; unknown keys are carried in the manifest
dict for forward compatibility. A tool dir with **no** `tool.md` and no
`<name>.py` is not a tool (ignored).

## 2. File-by-file changes

Current code anchors (pha 0.16.1, informational only): `_encoder_from_frontmatter`
config.py:944 · `library_page_path` ingest.py:553 · `_encode_needed` 1531 ·
`write_concatenated_file` 1659 · `write_records_file` 1676 · `_page_filter`
1700 · `encode_document` 1724 (records written at its tail; the "records up to
date" early return near its top) · `encode_all` 1924 · `cmd_encoder`
cli.py:1232 · `cmd_encode` 1265 · testrun encoder stage ~440-480.

### New module `src/personal_historical_archive/encoder_tools.py`

Keeps the runner out of the already-large `ingest.py`. Imports only
`config`/`extract`/stdlib (no import of `ingest`, to avoid a cycle — the
library-dir resolution helpers live in `ingest` and results are *passed in*).

- `@dataclass Tool`: `name`, `dir: Path`, `manifest: dict`, `payload:
  Path | None` (entrypoint script when a `command:` override is absent).
- `discover_tools(enc_file: Path | None) -> dict[str, Tool]` — per-encoder
  tool dirs in order: `enc_file.parent/f"{enc_file.stem}.tools"`, then
  `enc_file.parent/"_tools"`, then the package built-in dir
  (`personal_historical_archive/tools/builtin`, empty until phase B). Each
  dir contributes `<tool>/` subdirs that contain `tool.md` **or**
  `<tool>/<tool>.py`. A subdir named like a stage-prompt companion is never
  a tool (only exact `<tool>/tool.md` + payload count). `enc_file=None`
  (legacy global encoder, no collection dir) → no tools.
- `load_manifest(tool_dir) -> dict` — parse YAML front matter with the same
  `_split_frontmatter` used elsewhere (config.py); on parse failure return
  `{}` and warn.
- `tool_sources(tool) -> list[Path]` — every regular file under `tool.dir`
  (mtime inputs for D2).
- `merge_params(manifest, item) -> dict` — manifest `params:` defaults,
  overlaid with the front-matter item's extra keys.
- `build_context(...) -> dict` — the D4 context JSON.
- `run_tool(tool, ctx, *, cwd) -> dict` — resolves argv (D3), spawns via
  `subprocess.run` with `timeout`, temp `--context` file, returns
  `{"ok": bool, "returncode": int|None, "output_tail": str, "timed_out": bool}`.
- `run_encoder_tools(cfg, *, doc, enc_file, encoder, records_file,
  concatenated_file, library_dir, pages_dir_edited, pages_dir_raw,
  force=False) -> list[dict]` — orchestration: resolve the encoder's
  `tools:` items (D8), discover each, decide run/skip per D2 using stamps
  under `library_dir/.tools-stamps/`, call `run_tool`, write/refresh the
  stamp on success only, and return one result dict per configured tool.

### `src/personal_historical_archive/config.py`

- `Encoder` dataclass: add `tools: list[dict] = field(default_factory=list)`.
- `_encoder_from_frontmatter` (config.py): after `pages`, parse
  `fm.get("tools")` — must be a list of dicts each with a string `name`;
  anything else → warning and empty list (D8). Items are stored **raw**
  (params + possible future `model:` key survive untouched).
- `_ENC_SAMPLE`: add a commented `tools:` example block and a one-line note
  that tool dirs (`<name>.tools/`, `_tools/`) are inert for the loader.
- No change to `Config` paths for phase A (the built-in tool dir is package
  data, not an archive dir).

### `src/personal_historical_archive/ingest.py`

- Add `library_variant_dir(cfg, doc, variant, editor_id=None) -> Path | None`
  next to `library_page_path`: like that helper's prefix logic
  but returns the **directory**; when several match (e.g. a legacy
  `edited-<editor>` and `edited-<editor>@<model>`), prefer the one ending
  `@…` (newest run-folder convention, commit "Name library run folders
  rules@model"), else the newest by mtime. Raw variant dir =
  `transcription-<pal>` with the same rule. Returns `None` when the doc has
  no such folder (raw should always exist for a done doc; edited may not).
- `encode_document` (after the `write_records_file` /
  `write_concatenated_file` calls): build the four paths from the doc record +
  resolved editor and call `run_encoder_tools(..., force=True)`; add the
  results to the returned dict as `"tools"`.
- `encode_document` **skip path** (the early return with reason "records up
  to date"): instead of returning immediately, resolve the variant dirs and
  call `run_encoder_tools(..., force=False)`; include `"tools"` in the
  returned skipped dict. This is the D2 tool-only re-run path.
- Import `run_encoder_tools` lazily inside `encode_document` (module
  import at top is fine too — `encoder_tools` does not import `ingest`).
- Leave `_encode_needed`, `_parse_json_array`, chunking untouched (phase A).

### `src/personal_historical_archive/cli.py`

- `cmd_encode`: after printing per-document encode results,
  print each result's tool lines — `tool <name>: ok` / `failed (rc N)` /
  `skipped (stale-unconfigured)` — and a summary count of tool failures.
- `cmd_encoder`: when listing a document's resolved encoders or
  the collection encoders, show `tools: <names>` from
  `cfg.encoder_from_file(f).tools` when non-empty.

### `src/personal_historical_archive/testrun.py`

- In the encoder stage (the loop that writes `records-<eid>.json` /
  `concatenated-<eid>.md`), after the test records are written, run the
  encoder's tools with the scratch subdirectory
  (`.pha-test/<doc>-<ts>/<doc-slug>/`) standing in for `library_dir`.
- **The scratch does NOT currently mirror the library page layout** (verified
  in 0.16.1): it writes flat `page-NNN-transcription.md` /
  `page-NNN-edited.md` preview files, while tools read the library contract
  (`transcription-<pal>[@model]/page-NNN.md`,
  `edited-<editor>[@model]/page-NNN.md`). So `pha test` must additionally
  materialise the sampled pages in library layout inside the scratch (variant
  dirs + `page-NNN.md` names) and pass those as `pages_dir_raw` /
  `pages_dir_edited`; the existing flat preview files stay as they are. This
  also gives the historian a preview of the real segment files. (Rejected
  alternatives: a flat-layout adapter mode in the runner, or skipping tools in
  `pha test`.)
- Tools failing must not abort the report — record per-tool lines in
  `report.md` and continue. Add `--no-tools` to the parser and thread it
  through.

### `src/personal_historical_archive/mcp_server.py` + `dsh-pha` (cheap, optional)

- `pha_encoders`: include `"tools": [names]` per encoder by
  parsing the file with `cfg.encoder_from_file` when available. Nice-to-have
  for remote config checks; can land with phase A or later.
- The **dsh-pha plugin** (`dsh-pha/`) has its own same-origin `/pha/*` API and
  surfaces encoders in its view; if tools are exposed over MCP, mirror the
  same `tools` list there for parity (see `DSH_PLUGIN.md`).

### Docs

- `README.md` §"Encoders": new subsection "Bundled tools (encoder tool
  runner)" — layout, `tool.md` manifest, `tools:` front matter, context
  fields, param merge, stamp-based re-run semantics, security note, and the
  pointer that tool payloads travel with the collection (bundle already
  copies the whole `encoders/` tree).
- `AGENTS.md`: short conventions bullets — encoder tool dirs are inert for
  the loader; tools are archive-owner code (no sandbox); tool edits re-run
  only the tool (not the model pass); tool artifacts are **not** indexed and
  are not `notes/` (that folder is the research-note surface).
- `skills/pha-document-operations`: note that a tool-payload edit is picked
  up by `pha encode` (re-runs only the stale tool).
- `encoders/_sample.md` (`_ENC_SAMPLE` above).

## 3. Behavior contract (what a tool author gets)

Context JSON passed via `--context <file>` (exact proposal §2.3 fields):

```json
{
  "encoder": "documents",
  "document": "/path/to/dropbox/.../DOCUMENTA-INDICA-1540-49.pdf",
  "records_file": "…/library/<slug>/records-documents.json",
  "concatenated_file": "…/library/<slug>/concatenated-documents.md",
  "library_dir": "…/library/<slug>",
  "pages_dir_edited": "…/edited-latin-to-english@deepseek-v4-flash",
  "pages_dir_raw": "…/transcription-gemma-4-e4b-it@gemma-4-e4b-it",
  "params": { "out_dir": "segments-documents", "page_marker": "--- page N ---" }
}
```

- A field may be `null` when it does not exist (e.g. no edited variant);
  tools must tolerate that (exit 0 with a warning when the variant they need
  is missing is acceptable; exit non-zero only on real failure).
- The tool writes artifacts under `library_dir/<out_dir>/` by default (its
  own choice — pha does not restrict writes, D6), prints progress to stdout,
  exits 0 on success. pha never parses the tool's output; the exit code is
  the contract.
- Out-dir overwrite policy is the tool's; the shipped built-in (phase B)
  overwrites in place per DEC-5.

## 4. Staleness rules (D2, precise)

Tool `T` for encoder `E`, doc `D` runs when **any** of:

1. this call just wrote `D`'s records (action `encoded`), or `--reprocess`;
2. no stamp `library/<slug>/.tools-stamps/E.T.stamp` exists;
3. newest mtime of `tool_sources(T)` > the float stored in the stamp;
4. newest mtime of any **edited-page file under `pages_dir_edited`** > the
   float stored in the stamp (DEC-6: a human correction to an edited page
   marks the tool stale even when `_encode_needed` sees the records as
   fresh).

After a successful run pha rewrites the stamp with the current newest mtime
over (tool sources ∪ edited pages). A failed/timed-out run leaves the old
stamp (or none) → next run retries. Records freshness is untouched
(`_encode_needed` unchanged), so a `pha encode` on an up-to-date document is
cheap: `library_variant_dir` + stat comparisons + (at most) the stale tools.

## 5. Test plan (`tests/test_encoder_tools.py` + integration tweaks)

1. Discovery: per-encoder `<name>.tools/` wins over `_tools/`; missing dir →
   `{}`; `enc_file=None` → no tools; stage-prompt companions never tools.
2. Manifest: parse defaults + description; malformed front matter → `{}` +
   warning; `name` mismatch warning only.
3. Param merge: manifest default < front-matter override; fixed context keys
   not overridable (a front-matter `records_file:` is ignored).
4. End-to-end with a fake script tool (writes a file to `out_dir`):
   `force=True` runs and stamps; second `force=False` run skips; touching the
   payload re-runs; non-zero exit → `ok=False`, no stamp, records intact.
5. `encode_document` integration: doc with existing records + stale tool →
   result action `skipped` **with** a tool result `ran`; action `encoded`
   path runs tools (verified via a records-bearing fixture + monkeypatched
   model so no network).
6. Unknown/missing tool configured in front matter → warning, tool skipped,
   encode success (D8).
7. No edited variant dir → context `pages_dir_edited: null`, tool that
   ignores it exits 0.
8. `pha test`: the scratch gets library-layout pages for the sampled pages;
   tools run against them into the scratch (or are skipped with
   `--no-tools`); a failing tool does not break `report.md`. Tool fixtures
   must respect the current `_parse_json_array` semantics (a valid empty
   array is `[]`, not an error — commit 09219c2).
9. DEC-6 staleness: records fresh + a page file under `pages_dir_edited`
   touched → tool re-runs on the next `pha encode`; untouched → skips.
10. Back-compat sweep: existing fixtures with encoder front matter lacking
    `tools:` behave identically (no tools, no stamps).

Run: `.venv/bin/python -m pytest tests/test_encoder_tools.py` + the existing
`test_ingest.py`/`test_testrun.py`/`test_mcp.py` suites (no regressions).

## 6. Phase B — built-in `markdown-from-records` (in scope, DEC-2)

Move the validated reference script
(`documenta-indica/encoders/_tools/markdown-from-records/markdown_from_records.py`,
235 lines, stdlib) into package data
`src/personal_historical_archive/tools/builtin/markdown-from-records/` with a
`tool.md` manifest; the documenta-indica collection then references the
built-in (single source of truth, mtime-driven updates) instead of its local
copy. Add a **shared library-page reader** in `ingest.py` (page md → body
with YAML front matter and any trailing `## Notes` block stripped — the
reference re-implements this by string parsing today) and improve
`library_page_path`'s first-match loop to prefer the `@model` folder (needed
by both the tool and review).

**Correctness requirement (DEC-1).** The built-in must reconstruct each
record as its own document even when records **span pages** and when a new
record **starts mid-page** — the reference implementation already does this
(`page_chunks` infers `page_end` as the page before the next record's
`page_start`, or the last page; `find_boundary` uses the record's
`line_start`, falling back to a header search, to split a shared page so the
top stays with the previous document and the header onward starts the next).
Port that behaviour faithfully and cover it with explicit test vectors:
(i) a record spanning several pages, (ii) the next record's header starting
mid-page (`line_start` set), (iii) the last record ending at the volume's
final page, (iv) two records sharing one page, (v) records with no
`line_start` where the header is found by search.

## 7. Later (after A+B): phase C and the §3 follow-ups

- **Phase C — structure prescan → layout injection** (proposal §3.4):
  `structure: prescan` front matter resolved **per document**; lazy run +
  cache `library/<slug>/structure-<slug>.json` (mtime-invalidated); `pages:
  "@documents"` indirection in `_page_filter`; register block appended to the
  composed prompt; bundle support for collection-sibling scripts
  (`prescan/` today does not travel with `pha bundle`, only the encoders
  tree does). Until C, `doca_prescan.py --write-encoder-pages` remains the
  operative mechanism.
- **Proposal §3.1** model-assisted entry detection (extend
  `detect_entry_pages` beyond the regex fast path) and **§3.2**
  character-aware chunking (chunk by `effective_max_input_chars`, not
  `batch_pages`) — independent encoder-stage improvements; **§3.3** raw/
  edited cross-reference is a phase-B-adjacent tool enhancement once
  `pages_dir_raw` is in every context.

## 8. Risks / compat notes

- **Cyclic imports**: `encoder_tools.py` must not import `ingest` (helpers
  passed in as paths). `config._split_frontmatter` is import-safe.
- **Run-folder ambiguity**: several `edited-*` folders may exist for one
  document (legacy + `@model`); `library_variant_dir`'s preference rule
  must match what `write_edited_pages` actually produced last
  (`ingest.write_edited_pages`) or tools read stale pages. Covered by tests
  5 + 8.
- **Tool payload size / bundle**: `pha bundle` copies whole `encoders/`
  trees already → payloads travel; no change needed in phase A.
- **Windows**: no stdin piping, no shell interpolation (`subprocess.run`
  with a list argv); timestamps float comparison is fine.
- **Verbose output**: cap captured tool output (last ~2 KB) so a chatty tool
  cannot flood `pha encode` logs.

---
name: markdown-from-records
description: one Markdown file per record, built from the edited pages
accepts: records
returns: none
timeout_s: 1800
params:
  out_dir: segments
  strip_notes: true
  write_index: false
---
Makes an encode run's records usable outside pha: for every extracted record it
writes one Markdown file — the record's pages, verbatim from the library — into

    library/<dir>/<slug>/<out_dir>/<kind>-<page>-<slug>.md

with YAML front matter naming the record kind, its page span, the source
document and the encoder, plus the record's own scalar attributes (date, place,
name, ...). The body is the edited text for those pages (the raw transcription
when the document has no editor), with the page front matter and the editor's
`## Notes` block stripped.

Design notes:

- **Records span pages, and a record may start mid-page.** The span is inferred
  from the next record's page (`page_end` = the page before it), so two records
  sharing a page do not duplicate each other's text.
- **Deterministic names, overwritten in place.** The same records always
  produce the same filenames, so re-running replaces the artifacts instead of
  accumulating them; writes go through a temp file + rename.
- **This is an artifact filter** (`returns: none`): it consumes the records,
  writes files and leaves the pipeline's value untouched. pha stamps it under
  `library/<slug>/.filter-stamps/`, so it re-runs only when its own files, its
  declared inputs, or the EDITED pages change — never just because a model pass
  re-ran.
- **The library pages remain the human review surface.** These files are
  generated output; correcting one does not feed back (`pha review` reads the
  library pages, not artifacts).

    encoders:
      - rules: documents
        model: deepseek-v4-flash
        post:
          - {name: markdown-from-records, params: {out_dir: segments-documents}}

# Skills

This folder holds the **agent skills pha bundles with an archive** — short,
self-contained instruction files that tell an AI agent how to operate *this*
archive (and do recurring research tasks on it) without reading the pha source
code. They are the archive's own copy, seeded here when the archive was
created, so an agent that was pointed at this directory alone can find them (no
pha checkout, no network).

Each skill is one folder with a `SKILL.md` inside:

    skills/<name>/SKILL.md

A skill may also ship the files it needs — helper `scripts/`, `examples/`, a
`LICENSE`, its own `README.md`. `pha` seeds every bundled file, so an archive
with no source checkout gets the whole skill, not only the instructions.

Paths inside a skill are relative to the skill's own folder: run its
`scripts/...` from there (or with the full path), not from the archive root.

The `SKILL.md` file starts with YAML front matter carrying a `name` (which
**must match the folder name**) and a `description` (the trigger — when an
agent should reach for it), followed by the instructions:

    ---
    name: pha-search-context
    description: Use when an agent has run `pha search` and must answer ...
    ---

    # pha Search Context
    ...

## How an agent should use these

**Using the archive:** before the matching task, read
`skills/<name>/SKILL.md` and follow it. The four skills seeded here cover the
most common tasks:

- `pha-search-context` — a search hit is a *snippet*; recover the full page
  (raw and edited) before quoting or summarizing.
- `pha-document-operations` — re-scan / re-edit / re-encode one
  **already-ingested** document or collection (`pha scan --path … --reprocess`,
  `pha edit --path … --page N`, `pha test`).
- `pha-zotero-bibliography` — import a PDF from Zotero with its bibliographic
  sidecar, or build/refresh one document's reference from the owner's Zotero
  library (the local API's MODS, an RDF export, `pha bib <doc> --to-json
  --write`, and the provenance rules that keep an unverified reference from
  being cited as fact).
- `palaeographers-compare` — compare two or more palaeographers' (or
  transcription models') readings of the same pages into a uniform comparative
  edition: a `comparison/` folder with one file per page (readings stacked
  under `## Entry-by-entry comparison` plus a `## Key differences on this
  page` list) and an `overview.md`; the compared pages' images are copied to a sibling `images/` folder. Ships `scripts/normalize_comparison.py`
  (skeleton normalisation), `scripts/verify_comparison.py` (skeleton +
  reading-count checks) and `scripts/make_reference.py` (one-line-per-entry
  `reference/` variant); a reviewed `human/` folder is read-only for agents.

**Installing them into an agent runtime:** some runtimes (DeepSeek Harness and
other tools that read the shared agent-skills convention) discover skills from
a user-level directory instead of the archive. Copy the whole skill folder
there — including any `scripts/`, `examples/`, … files it ships:

    cp -R skills/palaeographers-compare ~/.agents/skills/

Keep the folder names unchanged — a skill's front-matter `name` must match its
folder name.

## Editing and updating

`pha` seeds this folder from the skills bundled with the installed pha
version: a bundled file is written **only when it is missing**, and an existing
file is never overwritten — so your edits survive. A skill or file added in a
newer pha version appears here on the next pha run, which is how an updated
install reaches an existing archive. (A bundled file you delete is re-seeded
the same way; to keep a custom version, edit the file in place instead.)

Add your own skills — any folder holding a `SKILL.md` conforming to the format
above — and edit or extend the bundled ones freely. To pick up a newer authored
version of a skill, copy it from the `skills/` folder of the pha source
repository, or from a newly created archive of the same pha version; pha will
not replace the files you already have.

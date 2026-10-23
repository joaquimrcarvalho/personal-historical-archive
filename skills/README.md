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
`skills/<name>/SKILL.md` and follow it. The eight skills seeded here cover the
most common tasks:

- `pha-search-context` — a search hit is a *snippet*; recover the full page
  (raw and edited) before quoting or summarizing. Also covers the archive's
  **notes** as memory: search them beside the sources, and offer them to the
  user as earlier conclusions to check, never as evidence.
- `pha-document-operations` — re-scan / re-edit / re-encode one
  **already-ingested** document or collection (`pha scan --path … --reprocess`,
  `pha edit --path … --page N`, `pha test`).
- `pha-library-folders` — find and consolidate a document whose library folder
  was **split in two**: two dated folders (`<stem>_<date>`, what the timezone bug
  did) or a bare `edited-<editor>` beside its `edited-<editor>@<model>` sibling.
  Ships `scripts/consolidate_library_date_dupes.py` (dry run by default; moves
  the encoder deliverables into the canonical folder and parks the remainder
  under `bin/` with a rollback README) and points at
  `pha prune --library-variants` for the bare alias.
- `lmstudio-model-locality` — determine whether an LM Studio model is
  local or remote when LM Link is enabled, before a `pha scan`/`pha edit`;
  helper `scripts/lmstudio_locality.py` joins `lms ls` / `lms ps` / `lms link status`
  and prints the pre-load recipe for a deterministic local instance.
- `pha-zotero-bibliography` — import a PDF from Zotero with its bibliographic
  sidecar, or build/refresh one document's reference from the owner's Zotero
  library (the local API's MODS **and the attachment file itself**, the RDF
  export as the fallback, `pha bib <doc> --to-json --write`, and the provenance
  rules that keep an unverified reference from being cited as fact), and link the
  two records both ways (`record_id` ↔ the item's `pha-doc`/`pha-slug`/`pha-path`
  lines plus a viewer link attachment).
- `palaeographers-compare` — compare two or more palaeographers' (or
  transcription models') readings of the same pages into a uniform comparative
  edition: a `comparison/` folder with one file per page (readings stacked
  under `## Entry-by-entry comparison` plus a `## Key differences on this
  page` list) and an `overview.md`; the compared pages' images are copied to a sibling `images/` folder. Ships `scripts/normalize_comparison.py`
  (skeleton normalisation), `scripts/verify_comparison.py` (skeleton +
  reading-count checks) and `scripts/make_reference.py` (one-line-per-entry
  `reference/` variant); a reviewed `human/` folder is read-only for agents.
- `obsidian-vault` - search, create and organise notes in the owner's
  Obsidian vault (wikilinks, index notes), and keep archive-derived notes in
  sync: YAML provenance is checked/stamped with
  `scripts/archive_note_sync.py` (`OBSIDIAN_VAULT`, `PHA_ARCHIVE_DIR`; flags
  `--vault`/`--archive` override).
- `timelink-kleio-provenance` - cite facts from Timelink/Dehergne
  prosopography by going from the SQLite attribute row back to the Kleio
  file and line, and render a `vscode://file/<absolute-path>:<line>` link;
  helper `timelink_provenance.py` (`MHK_HOME`).

**Installing them into an agent runtime:** some runtimes (DeepSeek Harness —
<https://www.deepseek.com/en/harness/> — and other tools that read the shared
agent-skills convention) discover skills from
a user-level directory instead of the archive. Copy the whole skill folder
there — including any `scripts/`, `examples/`, … files it ships:

    cp -R skills/palaeographers-compare ~/.agents/skills/

Keep the folder names unchanged — a skill's front-matter `name` must match its
folder name.

## More skills: the open pha skill index

The skills above ship with pha. More are published openly and listed — with the
repository and folder each one lives in — in the **pha skill index**:

    https://github.com/joaquimrcarvalho/pha-skill-index

The index holds only the list. For each skill it gives the name, what it does,
the repository, the folder inside that repository, and the licence (today
`digitarq-get` and `bnp-digital-download`, which download page images from the
Portuguese archives). Its README is written for agents: present the list, then
**ask which one to install** — never invent a skill, a URL or a path that is
not in the index.

To add one to this archive, clone the repository and copy the folder the index
names:

    git clone --depth 1 <repository URL> /tmp/pha-skill
    cp -R /tmp/pha-skill/<skill path> skills/<name>

Then read `skills/<name>/SKILL.md` before the task it covers, and install it
into your agent runtime as above (`cp -R skills/<name> ~/.agents/skills/`). A
skill you add by hand is yours: pha seeds only its own and never touches a
folder it did not write.

## Editing and updating

`pha` seeds this folder from the skills bundled with the installed pha
version, and it keeps improving them — a skill is guidance an agent obeys, so
a stale copy is worse than none. Every file pha writes carries a **stamp** at
the end recording a hash of what it wrote:

    <!-- pha-skill-template: sha256:… -->      (in a .md file)
    # pha-skill-template: sha256:…             (in a script)

That stamp is how pha tells your work from its own:

- **Untouched** (stamp still matches): refreshed on `pha update` or
  `pha skills update`, silently — that is how an improved skill reaches an
  existing archive.
- **Edited by you** (the text no longer matches the stamp, or you appended
  something after it): **never overwritten**. `pha skills status` lists it and
  `pha update` tells you it was kept. To take pha's version anyway:

      pha skills update --replace pha-search-context

  Your copy is written to `.pha/skills-backup/<date>/` first, so the choice is
  reversible. `--replace` with no name takes pha's version of every edited
  file; `--skill <path>` limits it.
- **Written before pha stamped skills** (no stamp, and different from the
  installed pha's copy): pha cannot prove it is untouched, so it treats it as
  yours and reports it as `unverified` rather than guessing. `--replace` is
  again the explicit way to take pha's version.
- A bundled file you delete is re-created; a whole skill added in a newer pha
  version appears on the next run.

Add your own skills — any folder holding a `SKILL.md` conforming to the format
above — and edit or extend the bundled ones freely. Only bundled files are
compared; a folder of your own is never read, reported or replaced.

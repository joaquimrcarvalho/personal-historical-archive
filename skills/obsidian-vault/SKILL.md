---
name: obsidian-vault
description: Search, create, and manage notes in the Obsidian vault with wikilinks and index notes. Use when user wants to find, create, or organize notes in Obsidian, or when syncing archive-derived notes between the pha archive and the main Obsidian vault.
---

# Obsidian Vault

## Addressing the vault

There is **no vault path to learn**. The vault is whatever Obsidian has open,
and notes are addressed by **name** or **vault-relative path**, never by an
absolute filesystem path. Use, in this order:

- the **Obsidian MCP tools**, when the runtime has them:
  `mcp__obsidian__search`, `get_file_contents`, `put_content`,
  `append_content`, `open_file`, `semantic_search`, `list_files_in_vault`;
- the **Obsidian CLI** (`obsidian <command>`, bundled with the app) from a
  shell: `obsidian search query="…"`, `obsidian read file="…"`,
  `obsidian create file="…" content="…"`, `obsidian append …`,
  `obsidian files`, `obsidian backlinks file="…"`;
- the **Local REST API** (`http://127.0.0.1:27123`, Bearer key) for bulk work
  neither can do — the provenance script below uses it.

**Never write into the vault through the filesystem.** Obsidian keeps its own
index and cache: a file written behind it appears in the tree but not in
search, links or the graph until a reload, and the path differs per machine. If
Obsidian is not running, say so and stop — a closed vault is not a reason to
edit its files directly.

Mostly flat at root level, with a main `01 Notes/` folder for topic notes.

## Naming conventions

- **Index notes**: aggregate related topics, e.g. `Ralph Wiggum Index.md`
- **Title case** for note names: `Cosme de Torres.md`
- No folders for organisation inside `01 Notes`; use links and index notes instead.

## Linking

- Use Obsidian `[[wikilinks]]`.
- Add related notes at the bottom under `## See also`.
- If the exact note title differs, use an alias link: `[[Nicolau Lanciloto|Nicolau Lancillotto]]`.

## Archive-derived notes and sync provenance

Some vault notes are summaries or mirrors of pha archive notes living in
`<archive>/notes/`, where `<archive>` is the pha archive root (`PHA_ARCHIVE_DIR`
when set):

When creating such a vault note, record provenance in YAML frontmatter:

```yaml
archive_source: notes/cosme-de-torres.md
archive_source_sha256: <sha256 of the archive note>
archive_source_mtime: 2026-10-04T19:02:04
vault_note_created: 2026-10-04
vault_note_updated: 2026-10-04
vault_synced_at: 2026-10-04T19:50:46+08:00
sync_policy: archive-note-is-source-of-truth
```

Never invent an archive hash: compute it from the archive note at the moment
the vault note is created or updated.

### Checking for stale vault notes

Use the bundled script — it talks to Obsidian's Local REST API, so it takes the
archive root but **no vault path**:

```bash
python3 <archive>/skills/obsidian-vault/scripts/archive_note_sync.py check \
    --archive "<archive>"
```

It asks Obsidian for the notes carrying `archive_source`, recomputes the source
SHA-256/mtime, and prints `UP_TO_DATE`, `STALE`, or `MISSING_SOURCE`.

When a note is genuinely reviewed and brought up to date, stamp it with:

```bash
python3 <archive>/skills/obsidian-vault/scripts/archive_note_sync.py stamp \
    --archive "<archive>"
```

It needs `OBSIDIAN_API_KEY` (the same key the MCP server uses) and takes
`OBSIDIAN_BASE_URL` (default `http://127.0.0.1:27123`); `--archive` defaults to
`$PHA_ARCHIVE_DIR` or the current directory. It only edits the frontmatter keys
`archive_source_sha256`, `archive_source_mtime`, `vault_synced_at`, and
`vault_note_updated`; it never rewrites the note body. Reviewing the archive
change and updating the body is a human or agent decision.

### Which vault is this? The identity line and the sentinel

The Local REST API cannot say **which** vault it serves, and when two vaults are
open the wrong one answers exactly like an empty one: `checked=0` and a clean
exit. So every run starts with an identity line, and each vault carries a
`.pha-vault.md` sentinel at its root naming the vault and the archive it
belongs to (the archive is shown as its resolved absolute path):

    vault: jrc-notes — 65 entries — archive: <archive>

Read that line before the results, and act on the state:

- **`UNIDENTIFIED`** — the vault has no sentinel. Show the line to the user and
  ask whether that is the vault they mean; the top-level entries are how they
  recognise it. Only on a clear yes, write the sentinel and re-run:

      python3 <archive>/skills/obsidian-vault/scripts/archive_note_sync.py identify \
          --vault <name> --archive "<archive>"

  `<name>` is the folder basename — the same name the `obsidian` CLI takes as
  `vault=`. `stamp` **refuses** until the sentinel exists; `check` runs with a
  warning. Never write the sentinel unprompted; with no user to ask, stop.
- **`WRONG`** — the sentinel names another archive. Do **not** overwrite it:
  that is evidence of a wrong target, not a gap to fill. Report both archives
  and let the user decide which side is wrong.
- **unreachable** — Obsidian is not running, or the plugin is off. Say so and
  stop; a closed vault is not a reason to edit its files.

The sentinel is a dotfile: Obsidian hides it, and pha's notes indexer skips
names starting with `.` or `_`, so it never becomes a searchable note.

## Workflows

### Search for notes

```bash
obsidian search query="keyword"      # vault-relative paths of the matches
obsidian files                       # everything, when you need the shape
```

In a runtime with the MCP server, `mcp__obsidian__search` is the same search and
`mcp__obsidian__semantic_search` adds concept matching.

### Create a new note

1. Use **Title Case** for the filename.
2. Write content as a unit of learning.
3. Add `[[wikilinks]]` to related existing notes at the bottom.
4. If derived from an archive note, add the sync provenance frontmatter above.
5. Run the sync checker and, if desired, `stamp`.

### Find related notes

```bash
obsidian backlinks file="Note Title"   # who links to it
obsidian links file="Note Title"       # what it links to
```

### Find index notes

```bash
obsidian files | grep -i index
```

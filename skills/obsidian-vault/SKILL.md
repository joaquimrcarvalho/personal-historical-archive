---
name: obsidian-vault
description: Search, create, and manage notes in the Obsidian vault with wikilinks and index notes. Use when user wants to find, create, or organize notes in Obsidian, or when syncing archive-derived notes between the pha archive and the main Obsidian vault.
---

# Obsidian Vault

## Vault location

The vault path is machine-specific: use the `OBSIDIAN_VAULT` environment
variable (or pass `--vault <path>` to the bundled script). The examples below
write it as `$OBSIDIAN_VAULT`.

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

Use the bundled script:

```bash
python3 <archive>/skills/obsidian-vault/scripts/archive_note_sync.py check \
    --vault "$OBSIDIAN_VAULT" --archive "<archive>"
```

It scans the vault for notes carrying `archive_source`, recomputes the source
SHA-256/mtime, and prints `UP_TO_DATE`, `STALE`, or `MISSING_SOURCE`.

When a note is genuinely reviewed and brought up to date, stamp it with:

```bash
python3 <archive>/skills/obsidian-vault/scripts/archive_note_sync.py stamp \
    --vault "$OBSIDIAN_VAULT" --archive "<archive>"
```

The script defaults to `$OBSIDIAN_VAULT` and `$PHA_ARCHIVE_DIR`, falling back
to `~/Obsidian` and the current directory; pass `--vault`/`--archive` to
override. It only edits the frontmatter keys `archive_source_sha256`,
`archive_source_mtime`, `vault_synced_at`, and `vault_note_updated`; it never
rewrites the note body. Reviewing the archive change and updating the body is a
human or agent decision.

## Workflows

### Search for notes

```bash
find "$OBSIDIAN_VAULT" -name "*.md" | grep -i "keyword"
grep -rl "keyword" "$OBSIDIAN_VAULT" --include="*.md"
```

Or use Grep/Glob tools directly on the vault path.

### Create a new note

1. Use **Title Case** for the filename.
2. Write content as a unit of learning.
3. Add `[[wikilinks]]` to related existing notes at the bottom.
4. If derived from an archive note, add the sync provenance frontmatter above.
5. Run the sync checker and, if desired, `stamp`.

### Find related notes

```bash
grep -rl "\\[\\[Note Title\\]\\]" "$OBSIDIAN_VAULT"
```

### Find index notes

```bash
find "$OBSIDIAN_VAULT" -name "*Index*"
```

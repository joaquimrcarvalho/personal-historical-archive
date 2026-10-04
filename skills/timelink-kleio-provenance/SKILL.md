---
name: timelink-kleio-provenance
description: Use when citing facts that come from Timelink/Dehergne prosopography (entities such as `deh-*`, `bio-*`, `ivc-*`, `manuel-*`) or when a note needs a link to the original Kleio `.cli`/`.kleio` source file and line. Covers where the Timelink SQLite databases live, the `attributes → entities → sources` join that yields the Kleio file path and line number, how to turn that into a `vscode://file/<absolute-path>:<line>` link, and the caveats about line drift, uncertainty markers and file-level bibliographies.
---

# Timelink / Kleio provenance and citations

## What this is for

Timelink prosopographies (the Dehergne *Répertoire des Jésuites de Chine*, the
China–Coimbra project, etc.) are built from **Kleio source files** (`.cli` /
`.kleio`). Those files are imported into a SQLite database, where each fact is
an **attribute** row. The database keeps enough information to point back to
the exact **file and line** of the original fact. This skill says how to find
that file and line, and how to render it as a clickable link (VS Code
`vscode://file/…:line`).

Never cite the Obsidian `deh-*.md` export as if it were the source. Go to the
SQLite row and from there to the Kleio file + line.

## Where the data lives

The Timelink home is `$MHK_HOME` (the helper reads `MHK_HOME`, then `MHK`,
defaulting to `~/mhk-home`; check `.mhk-home`, `.kleio.json`). Useful entry
points:

| path | what it is |
| --- | --- |
| `mhk-home/.db_status` | database schemas and row counts (`dehergne`, `china_coimbra`, `mhk`) |
| `mhk-home/sources/<project>/database/sqlite/<project>.sqlite` | read-only SQLite backup of the project's database (e.g. `sources/dehergne/database/sqlite/dehergne.sqlite`, `sources/china-coimbra/database/sqlite/china_coimbra.sqlite`) |
| `mhk-home/sources/<project>/sources/*.cli` | the Kleio source files (e.g. `sources/dehergne/sources/dehergne-t.cli`) |
| `mhk-home/system/db/mhk/imports/<project>/` | import folders per project |
| `mhk-home/sources/<project>/inferences/` | derived reports (e.g. the Coimbra lists) |

The live services (Kleio at `http://localhost:8088`, MySQL at `:3306`) may not
be running. The SQLite backups are read-only and sufficient. Do **not** modify
them.

## Schema (the bits that matter)

- `attributes`: `id` (the **attr_id**), `entity` (the person id), `the_type`,
  `the_value`, `the_date`, `obs`.
- `entities`: `id`, `class`, `inside`, `the_source`, `the_order`, `the_level`,
  `the_line`, `groupname`, `extra_info`.
- `sources`: `id`, `the_type`, `the_date`, `loc`, `ref`, `kleiofile`,
  `replaces`, `obs`.

Key model facts:

- Every attribute is itself an entity: **`attributes.id = entities.id`**; the
  person it belongs to is `attributes.entity`.
- The attribute entity's **`the_source`** is the source id; **`the_line`** is
  the line for the attribute in the Kleio file.
- **`sources.kleiofile`** is the path of the Kleio file relative to
  *kleio-home*; **`sources.obs`** is the bibliographic description of the
  source (file-level, not necessarily person-level).

## The query

```sql
SELECT a.id AS attr_id, a.entity AS entity_id, a.the_type, a.the_value, a.the_date,
       e.the_line, e.the_source, s.kleiofile, s.obs AS source_desc
FROM attributes a
JOIN entities e ON e.id = a.id
LEFT JOIN sources s ON s.id = e.the_source
WHERE a.entity = ? AND a.the_type LIKE ? AND a.the_value LIKE ?;
```

Worked example:

- `attributes` row: `id = deh-antoine-thomas-att430-124`,
  `entity = deh-antoine-thomas`, `the_type = estadia-x`,
  `the_value = Coimbra`, `the_date = 1678000`.
- `entities` row for that attr_id: `the_line = 670`,
  `the_source = dehergne-t`.
- `sources` row `dehergne-t`: `kleiofile = /kleio-home/sources/dehergne-t.cli`,
  `obs = Dehergne, … Répertoire…, 1973. Letra T…`.
- Result to cite: **`sources/dehergne-t.cli`, line 670**.

## Kleio-home and the local file

*Kleio-home* is the directory that contains the `database` directory of the
SQLite database. For
`$MHK_HOME/sources/dehergne/database/sqlite/dehergne.sqlite`,
kleio-home is `$MHK_HOME/sources/dehergne`; for
`…/sources/china-coimbra/database/sqlite/china_coimbra.sqlite`, it is
`$MHK_HOME/sources/china-coimbra`.

The `kleiofile` value keeps the original container prefix `/kleio-home/…`. To
get the local file:

1. strip `/kleio-home/` from `kleiofile` (e.g. `sources/dehergne-t.cli`, or
   `sources/china-coimbra-biografias/sources/coimbra-visitantes.cli`);
2. locate the file under `mhk-home/sources/**/sources/` by basename, e.g.
   - `sources/dehergne-t.cli` →
     `$MHK_HOME/sources/dehergne/sources/dehergne-t.cli`;
   - `sources/china-coimbra-biografias/sources/coimbra-visitantes.cli` →
     `$MHK_HOME/sources/china-coimbra-biografias/sources/coimbra-visitantes.cli`.

**Verify the local file exists before writing a link.**

## The citation and the link

Cite the `attr_id`, the Kleio file, the line, the source id and `sources.obs`.
VS Code link format:

```
vscode://file/<absolute-path>:<line>
```

The Markdown form (label = relative path + line):

```markdown
[sources/dehergne-t.cli:670](vscode://file/<absolute-path-to-kleio-home>/sources/dehergne-t.cli:670)
```

- No extra slash after `file/`: `vscode://file/<absolute-path>:<line>`, not
  `vscode://file//<absolute-path>:<line>`.
- Paths with spaces must be percent-encoded (`%20`).
- Alternative from a terminal: `code -g <absolute-path>:<line>`.
- Obsidian may or may not make custom URI schemes clickable; always keep the
  plain path + line and the `attr_id` visible as a fallback.

## Caveats

- **Line drift:** `entities.the_line` is the line recorded at import time. If
  the `.cli` has been edited since, the line may have shifted. The `attr_id`
  disambiguates; if a link lands on the wrong line, give the `attr_id` and/or
  search the file for the `the_type`/`the_value` token. Example: the DB
  records line 670 for `deh-antoine-thomas-att430-124`, while the current file
  may have the `ls$estadia-x/Coimbra` line at a different position.
- **Uncertainty:** `%?` in the Kleio line (and often the Timelink record)
  flags the fact as uncertain; keep the `(?)` in the note.
- **File-level bibliography:** `sources.obs` describes the whole source file
  (e.g. "Dehergne, … Letra T"), not the individual person.
- **Multiple projects:** search both `dehergne.sqlite` (entities `deh-*`) and
  `china_coimbra.sqlite` (entities `bio-*`, `ivc-*`, `duarte-*`,
  `manuel-*`, …); a person can appear in both with different ids.

## Common searches

Find every attribute of a person that mentions Coimbra:

```sql
SELECT a.id, a.the_type, a.the_value, a.the_date
FROM attributes a
WHERE a.entity = 'deh-antoine-thomas' AND a.the_value LIKE '%Coimbra%';
```

Find everyone with an `estadia` in Coimbra (not only `jesuita-entrada`):

```sql
SELECT a.id, a.entity, a.the_type, a.the_value, a.the_date
FROM attributes a
WHERE a.the_type LIKE 'estadia%' AND a.the_value LIKE '%Coimbra%';
```

Then run the provenance query above on each `a.id`/`a.entity`.

## Helper

`timelink_provenance.py` in this skill folder (`<archive>/skills/timelink-kleio-provenance/`
when seeded into an archive) takes a database or project name and an entity id,
runs the join and prints the citation and the VS Code link. It reads `MHK_HOME`
(or `MHK`, defaulting to `~/mhk-home`), and `--db` also accepts a `.sqlite` path.
See its `--help`.

# Skills

This folder holds **pha-specific agent skills** — short, self-contained
instruction files that tell an AI agent how to operate *this* archive without
reading the pha source code. They are the archive's own copy, seeded here when
the archive was created, so an agent that was pointed at this directory alone
can find them (no pha checkout, no network).

Each skill is one folder with a `SKILL.md` inside:

    skills/<name>/SKILL.md

The file starts with YAML front matter carrying a `name` (which **must match
the folder name**) and a `description` (the trigger — when an agent should
reach for it), followed by the instructions:

    ---
    name: pha-search-context
    description: Use when an agent has run `pha search` and must answer ...
    ---

    # pha Search Context
    ...

## How an agent should use these

**Using the archive:** before the matching task, read
`skills/<name>/SKILL.md` and follow it. The two seeded here cover the most
common mistakes:

- `pha-search-context` — a search hit is a *snippet*; recover the full page
  (raw and edited) before quoting or summarizing.
- `pha-document-operations` — re-scan / re-edit / re-encode one
  **already-ingested** document or collection (`pha scan --path … --reprocess`,
  `pha edit --path … --page N`, `pha test`).

**Installing them into an agent runtime:** some runtimes (DeepSeek Harness and
other tools that read the shared agent-skills convention) discover skills from
a user-level directory instead of the archive. Copy the folder there:

    cp -R skills/pha-search-context ~/.agents/skills/

Keep the folder names unchanged — a skill's front-matter `name` must match its
folder name.

## Editing and updating

`pha` seeds this folder **once** and never overwrites it: edit these files
freely, delete the ones you do not want, and add your own (any folder holding a
`SKILL.md` conforming to the format above). To pick up a newer version shipped
with pha, copy it from the `skills/` folder of the pha source repository, or
from a newly created archive of the same pha version — pha will not silently
replace your edits.

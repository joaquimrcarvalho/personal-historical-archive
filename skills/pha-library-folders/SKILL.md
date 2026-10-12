---
name: pha-library-folders
description: Find and consolidate duplicated library folders in a personal-historical-archive (pha) archive — one document split across two dated folders (`<stem>_<date>`, which a timezone change causes), or a bare `edited-<editor>` alias sitting beside its `edited-<editor>@<model>` sibling. Use when the owner reports duplicated pages/readings or says a document "should have" files that are not there; when `pha status` or `pha doctor` flags a done document with no library folder; when an artifact filter wrote 0 files for a document that has records; or before re-processing a document whose library folder looks split.
---

# pha Library Folders

Consolidating a library folder that has been split in two — safely, reversibly,
and without re-running a model.

## Talk to a historian first

You are working inside an archive, so the person asking is most likely a
**historian, not a programmer**. Explain in their terms: "this document's pages
are in one folder and its generated records in another, so the search and the
citations were reading half of it; I am going to join them". Report the result
the same way ("3 of your volumes were split; the records are back beside the
pages").

Anything that **moves or deletes files** needs their agreement before you start,
and the machine's permission rules may ask for access outside the archive folder
— say what you need, what it is for, and what will change, then wait for a clear
yes.

## The two things that both look like "duplicate folders"

They have different causes and different fixes. Identify which one you have
before touching anything.

**1. Two DOCUMENT folders for one document.** The folder is
`library/<collection>/<stem>_<YYYY-MM-DD>/`. pha used to recompute that date
from the document's creation time **in the machine's current timezone**, so a
machine (or a laptop that travelled) whose zone changed looked for — and wrote —
a folder under a *different date* than the one on disk. One document then has
two folders: typically the pages (transcription + edited) in one and the encoder
deliverables (`records-*.json`, `concatenated-*.md`, `segments-*`,
`structure.json`, `.filter-stamps/`) in the other.

    ls -d library/*/*/<stem>_*        # two hits for one stem = this bug

Since pha 0.43.1 the folder name is **stored** on the document row
(`documents.library_slug`), so this no longer happens to new work — but the
folders created before the fix are still split, and this skill consolidates
them.

**2. A bare `edited-<editor>` beside `edited-<editor>@<model>`.** These are ONE
variant: the bare name only means the model was unknown when that folder was
written. pha already resolves only the qualified one, so the bare folder is
inert — but it occupies disk and misleads anyone reading `library/` by hand.
**Do not fix this by hand**: pha ships a guarded sweep for it (below).

## Do NOT "fix" a split by re-processing

`pha scan`/`pha edit`/`pha encode` are the wrong tool: they call a model (hours,
and the machine's model-server lock) and they are what *created* the split.
Re-running `pha encode` in particular can cost a full encoder pass over hundreds
of pages to regenerate what is already on disk.

The consolidation is a **filesystem move**. It calls no model, writes no
database row, and is reversible.

## Bug 1 — consolidate the dated folders

The skill ships the script; run it from the skill's own folder:

    # 1. ALWAYS dry-run first. It prints, per document, what it would move.
    python scripts/consolidate_library_date_dupes.py

    # 2. Read the report with the owner, then apply.
    python scripts/consolidate_library_date_dupes.py --apply

What it does, and why it is safe:

- It resolves **the canonical folder** the way pha now does: the folder that
  holds the document's `transcription-*` (its pages). That is where pha will
  write from now on.
- The other folder is *not* a duplicate — it holds the encoder deliverables. So
  every file **absent from, or differing from** the canonical copy is **moved
  into it**; nothing unique is left behind. The `source:` line in each moved
  `segments-*/*.md` is repointed at the new location so the provenance stays
  true.
- Only files already **byte-identical** to the canonical copy stay in the
  leftover folder, which is then parked under
  `bin/<date>-library-date-dupes/` with a `README.md` explaining what happened
  and how to revert. Nothing is deleted.
- The database is opened **read-only**: the consolidation writes no row. The
  folder name is stamped on the document by the next real scan/edit/encode pass.

Verify afterwards:

    pha status        # the ⚠ "library folder missing" marker must be gone
    pha doctor        # the `library` section must not appear
    ls -d library/*/*/<stem>_*        # exactly one hit per document now

## Bug 2 — the bare `edited-*` alias

There is a pha command for it; use that, never `rm`:

    pha prune --library-variants --dry-run     # read the report first
    pha prune --library-variants               # removes only what is provably redundant

Its guard is the point: it removes a bare folder **only when nothing lives in it
alone** — every page with text is exactly what the database holds (so
`pha export` regenerates it) or byte-identical to the qualified sibling. A
folder holding a genuinely different reading (an alternate OCR pass, another
translation generation) is **reported and kept**, and so is a bare-only variant
(a document whose only edited output is that folder). A re-run is a no-op.

Two habits worth keeping:

- **Back the folders up before the sweep**, as the archive already does for
  other retirements: copy each folder it will remove into
  `bin/<date>-bare-edited-variants/` with a short `README.md` (what it was, the
  date, and the `cp -a` command to put it back).
- The folders it keeps are an **archive decision, not a defect**: they hold a
  real reading. Do not delete them to make the tree tidy; tell the owner what
  they are and let them decide.

## If the folder is missing entirely

If a document is `done` but has **no** library folder at all (a cleanup outside
pha, a sync with `--delete`, a folder written under a date this machine no
longer computes), do not go looking for the split — regenerate it:

    pha export --doc N        # rewrites library/ from the database, no model call

Then re-encode if the records are missing too. `pha status` and `pha doctor`
report this case by name.

## What an agent must never do here

- Never `rm -rf` (or `mv`) a library folder to "clean up" a duplicate.
- Never hand-delete a bare `edited-*` folder — run the sweep, which refuses the
  ones that hold something.
- Never re-run `pha scan` / `pha edit` / `pha encode` to repair a split.
- Never edit a `segments-*` file by hand: it is generated output. Regenerate it
  with `pha export` / the encoder pass if it is wrong.
- Never merge two folders that hold **two different readings** of the same pages
  (two models, two translation generations): that is not a duplicate. Compare
  first — the `palaeographers-compare` skill is the tool for that.

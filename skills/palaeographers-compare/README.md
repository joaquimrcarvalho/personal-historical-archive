# palaeographers-compare

Compare two or more independent readings of the same historical document —
different palaeographers, or different transcription models — and produce a
uniform comparative edition: one markdown file per page with the readings
stacked entry by entry, plus an overview of the disagreements.

This folder is the **agent skill** for that work. In an archive managed by
`personal-historical-archive` (`pha`), the corpus around it is declared by
`bench.yaml` and driven by the `pha bench` commands; the skill adds the part no
command can do — judging where the mechanical alignment is wrong.

## Install

- **In a pha archive:** the skill is shipped there. `pha skills status` shows
  whether it is current; `pha skills update` refreshes it (a file you edited is
  reported and left alone until `--replace`).
- **Standalone:** copy this folder to `~/.agents/skills/`, or publish it and
  `npx skills add github.com/USER/palaeographers-compare`.

## Corpus layout

    <corpus>/
      bench.yaml                 # reader order, source document, rules, rights
      <reader>/<page>.md         # one reading per reader per page
      comparison/<page>.md       # this skill's output
      reference/<page>.md        # machine merge ("a/b" variants)
      human/<page>.md            # human-corrected gold (front matter + status)
      images/                    # optional; resolved from the source otherwise

`bench.yaml` fixes the reader order — it is the `1..N` numbering in every
comparison and reference file. `human/` is written only by the reviewer, via
`pha bench serve`: **Save draft** keeps a part-reviewed page (status `draft`,
excluded from scoring), **Save gold** completes it.

## Commands

| command | does |
|---|---|
| `pha bench init <corpus>` | writes `bench.yaml`, inferring readers and models |
| `pha bench run <corpus> --pages …` | produces readings with `pha test` (no archive writes) |
| `pha bench compare <corpus>` | mechanical comparison draft (`comparison/`) |
| `pha bench verify <corpus>` | skeleton, entry counts, images, gold freshness |
| `pha bench reference <corpus>` | the slashed merge (`reference/`) |
| `pha bench serve` | the local workbench: image + readings + gold editor |
| `pha bench score <corpus>` | scores readings against the gold (`scores/results.json`) |
| `pha bench export <corpus> --out DIR` | a self-contained static site |
| `pha bench status <corpus>` | coverage: readings, comparison, gold, stale |

The full workflow, the uniform page skeleton, the alignment and fidelity rules
and the `human/` rules are in [`SKILL.md`](SKILL.md).

## Scripts

`scripts/normalize_comparison.py`, `scripts/verify_comparison.py` and
`scripts/make_reference.py` still work on a machine without `pha`; in an archive
with the current tool, `pha bench verify` and `pha bench reference` supersede
them.

## License

MIT — see [`LICENSE`](LICENSE).

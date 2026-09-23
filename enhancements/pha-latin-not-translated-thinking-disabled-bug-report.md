# Bug report — an editor with `thinking: disabled` silently leaves Latin pages untranslated

**Status:** open. **Found:** 2026-09-21, on a Mac Mini hand-over worker running
`collections/monumenta-brasiliae` (4 volumes, 2 676 pages) with
`editors/latin-to-english-ocr.md` + `models/deepseek-v4-flash.md`
(DeepSeek-V4.1-Flash, `thinking: disabled`).
**Severity:** silent and **paid** — the pages are stored `done` with an empty
`error`, and the model's own `## Notes` block states "Latin translated into
English" while the body is still Latin.

## 1. Summary

An all-Latin page comes back **cleaned but untranslated**. The editor performs
Step 1 of its prompt (OCR clean-up: spacing, `[l. N]` margin marks, letter
confusions) and never performs Step 2 (translate the Latin). Nothing in the
pipeline notices: `page_edits.status = 'done'`, `error` empty, and the `## Notes`
block claims the translation was made.

The behaviour is a property of the **model with reasoning switched off**: the
same prompt with thinking enabled translates the page correctly, and the same
thinking-disabled call translates it correctly once the translation duty is
stated **first** in the prompt (see §4.1).

## 2. Evidence

### 2.1 In the archive (worker DB, two volumes fully edited)

| volume | pages edited | Latin-marker pages (score ≥ 5) | of those, body in English | pages with residual Latin in the body (score ≥ 3) |
|---|---|---|---|---|
| I (1538-1553) | 642 | 2 | 0 | 5 |
| II (1553-1558) | 626 | 18 | 2 | 38 |

No errors anywhere: `SELECT status, count(*) FROM page_edits … GROUP BY status`
returns `done 642` (vol I) with `0` rows carrying a non-empty `error`.

Example — vol I p. 16 (the Latin preface `LECTORI, S. P. D.`), stored edited text:

```
LECTORI, S. P. D.

Non semel nobis linguis modernis uti in praefationibus et adnotationibus nostri
operis Monumenta Historica Societatis Iesu commendatum est, cum praecipue
sectio missionalis etiam viros scientificos, non autem latinitate adeo peritos,
spectet. …
```

…followed by the English `## Notes` block beginning "Language: Latin translated
into English". The Latin is the model's output, not the OCR's: the synthetic
`--- Page 1 ---` separator is gone, runs of spaces are collapsed, and the printed
margin line numbers are placed as `[l. N]` — i.e. Step 1 ran.

Note the trap in measuring this: an English-word test over the whole stored text
gives a false "translated" verdict, because the `## Notes` block is always
English. Compare only the body **before** `## Notes`.

### 2.2 Reproduced outside pha (direct API calls, same page, same prompt)

Prompt = `editors/latin-to-english-ocr.md` body; user message = prompt + the
OCR transcription of vol I p. 16; `model: deepseek-v4-flash`, `temperature: 0`.

| call | parameters | output | verdict |
|---|---|---|---|
| A | `thinking: {"type": "disabled"}` (exactly what pha sends) | 508 tokens, 1 946 chars | **not translated** (Latin body) |
| B | default (thinking on), `max_tokens: 8192` | `finish_reason=stop`, 8 192 tokens, **content empty** | reasoning consumed the whole budget |
| C | default (thinking on), `max_tokens: 32768` | 17 839 output tokens (69 145 chars of `reasoning_content`) | **correctly translated** — "To the Reader, greetings. More than once it has been recommended to us…" |
| D | `thinking: disabled` + translation clause moved to the top | 470 tokens | **correctly translated** |

Cost ratio between C and D is ~35× on output tokens.

## 3. Root cause

With reasoning disabled, the model follows the prompt's **immediate, concrete
list** — Step 1's seven clean-up rules, reinforced by "DO NOT modernise genuine
period orthography … stay verbatim" and "never add text that is not in the
source" — and stops. On a page that is *entirely* Latin there is no Portuguese
left to preserve, so "leave the text as it is" is a defensible reading of the
instructions, and Step 2 (which sits below a ~90-line clean-up section) is never
reached. Reasoning fixes it because the model then notices the contradiction;
moving the translation duty to the first line of the prompt fixes it without
reasoning.

## 4. Fixes

### 4.1 Prompt fix (applied in the archive, verified with call D)

`editors/latin-to-english-ocr.md` now opens with, immediately after the front
matter:

```
TRANSLATION IS THE FIRST DUTY OF THIS TASK. This edition is bilingual and you
are producing its ENGLISH reading. Whatever is written in LATIN — a whole page,
a preface, a dedication, a privilege, a letter, a paragraph quoted inside a
Portuguese passage, a footnote or the editorial apparatus — MUST come out in
English. A Latin page returned in Latin is a FAILED answer. Only Portuguese
(and other modern languages) stays verbatim. The clean-up rules below say how
to repair the OCR text; they are never a reason to leave Latin untranslated.
```

This is a per-collection rules file, so the blast radius of the edit is the
documents that use it. Anyone who shares this prompt should adopt the same
opening paragraph.

### 4.2 What pha could do

**Status of F2 + F4: FIXED** (2026-09, pha 0.34.0). A rules file
(`palaeographers/`, `editors/`, `encoders/<id>.md`) may now carry `thinking:
on|off`; it is a **stage-level override that beats the paired model sheet**,
because the model file states a capability while the rules file states what
this pass needs. Omit it to inherit the model — so an existing config is
unchanged. `pha editor <file>` and `pha palaeographer <file>` now print the
resolved `temperature`/`max_tokens`/`thinking`, and the `_sample.md` templates
document the new key. F1 (prompt wording) is a per-archive content change, and
F3 (page-range editors) and F5 (a language guard) remain open — see
`pha-enhancement-requests-INDEX.md`.

- **F1 — put the translation instruction where a non-reasoning model sees it.**
  Ship the clause above in the OCR + latin-to-english editor templates (the
  in-repo sample, and any doc that recommends `thinking: disabled` for editing).
- **F2 — `thinking` and `max_tokens` are split across two files.**
  `thinking` lives only on the *model sheet*, `max_tokens` only on the *rules
  file*. Choosing a reasoning editor for one collection therefore means a new
  model sheet **and** a new rules file. Allow `thinking:` on the rules file
  (overriding the sheet) so a collection can buy reasoning for a page class
  without duplicating the model.
- **F3 — per-page-class editor selection.** Encoders already accept `pages=`
  ranges (`encoders/apparatus.md` uses `pages=1-140,955-1011`). Editors have no
  equivalent, so "Latin prefaces and apparatus get the reasoning editor, the
  Portuguese body gets the fast one" cannot be expressed. **Partly addressed
  (2026-09-22):** `pha edit --path <doc> --page N --editor X --model Y` applies
  an editor/model to ONE page, records and pins it — the manual form of the same
  thing. Declaring ranges in `pha.yaml` (so a bulk pass does it) is still open.
- **F4 — surface the effective parameters.** `pha editor <file>` prints the
  rules and the model id but not whether reasoning is on; the fact that mattered
  most here was invisible.
- **F5 — a post-pass guard (weak but cheap).** After an editor pass, flag pages
  whose body still looks Latin when the configured editor promises a
  translation, and say so in `pha status` / the edit summary. Language detection
  is fuzzy, so this should warn, never block.

## 5. Cost

- Wasted work: ~1 300 pages edited with the defective configuration (~1.5 USD at
  the peak rate in force when the run started).
- Correcting it: re-edit only the pages with residual Latin in the body — 5
  (vol I) + 38 (vol II) measured, plus the equivalent pages of vols III-IV —
  i.e. tens of pages, well under 0.10 USD.

## 6. Method note

The defect is invisible to the usual checks (`status`, `error`, spot-reading a
Portuguese page). Two things made it visible, and both are reusable:

1. Compare the body **before** `## Notes` — never the whole stored text.
2. Reproduce the model call **outside pha** with the same prompt, the same
   parameters and `thinking` set exactly as pha sets it (`payload["thinking"] =
   {"type": "disabled"}`, `model_client.py:678`). Varying one parameter at a
   time then separates "the pipeline dropped it" from "the model never did it".

## 7. Addendum (2026-09-21) — comparing prompts on a non-deterministic model

The fix changes a prompt file, and pha marks every already-edited page of the
collection stale by **mtime** (`_edit_needed`, `ingest.py:1356`: a rules file
newer than `page_edits.updated_at` re-edits the page). So the question "do the
untouched Portuguese/Spanish pages have to be re-edited too?" is a money
question — here ~2 USD for 2 676 pages.

One Portuguese page (vol I p. 196, 3 176 OCR chars) was sent through the **old**
prompt and the **new** one, twice each, `temperature: 0`, thinking disabled:

| comparison | changed lines |
|---|---|
| old prompt, run 1 vs run 2 | 49 |
| new prompt, run 1 vs run 2 | 57 |
| old vs new, first runs | 63 |
| old vs new, second runs | 41 |

The model's own run-to-run variation is as large as the difference between the
two prompts — `temperature: 0` on this provider is **not** deterministic, and
the outputs differ in lineation and in where the printed margin numbers land.
Two conclusions: there is no evidence that the added paragraph degrades a
Portuguese page, and a single old-vs-new pair proves nothing in either
direction. Any claim of the form "this prompt change altered N pages" needs
repeats.

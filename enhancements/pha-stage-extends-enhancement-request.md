# Enhancement request — prompt composition for stage files via `extends` (base + delta)

**Status:** draft for discussion.
**Author/date:** archive work session (Documenta Indica collection).
**Relates to:** the encoder-tools request (`pha-encoder-tools-enhancement-request.md`).

## 1. Motivation

Stage rules files (palaeographers/, editors/, encoders/) are **content-only
prompts**, one file per rules id. When a rules variant differs from another
only by an added concern, the shared body is currently **duplicated**. The
concrete case is Documenta Indica:

- `editors/latin-to-english.md` — translate Latin → English (Portuguese
  verbatim), normalise names, emit the `## Notes` block; also carries the
  edition's margin **line-number** convention (`[l. N]`).
- `editors/latin-to-english-ocr.md` — must be the SAME base rules *plus* an
  **OCR cleanup** pre-pass (fix obvious OCR slips) and the line-number note.

Today that means pasting the whole `latin-to-english` body into the OCR file
and hand-keeping the two in sync — any change to one (e.g. the line-number
convention) must be re-applied to the other, and they drift.

A small **composition directive** lets a rules file be "a base rules file,
plus this delta", so shared rules live in exactly one place.

## 2. Proposed feature: `extends:` in a rules file's front matter

A stage file may declare an optional base:

```yaml
# editors/latin-to-english-ocr.md
---
description: "latin-to-english-ocr — OCR-aware latin-to-english"
extends: latin-to-english
temperature: 0.0
max_tokens: 8192
timeout_s: 300
---
BEFORE applying the translation rules below, run this OCR CLEAN-UP pass …
(the cleanup list + the OCR line-number detection note)
```

The directive name `extends` reads as "subclass": the base contract is
included first, then this file's body refines/extends it. (`include:` could
be accepted as an alias; the behaviour is identical.)

### 2.1 Resolution (load time)

Compose in the stage loader (e.g. `_editor_from_frontmatter` in `config.py`),
using the `compose_prompts` helper pha already uses for the palaeographer
base + document prompt and the encoder base + langextract prompt:

```python
base_id = fm.get("extends")
if base_id:
    base = registry[base_id]                     # same rules dir, resolved first
    body = compose_prompts(base.prompt_text, body)   # base first, then this delta
    # settings cascade: derived overrides base, else inherits base
    temperature = float(fm.get("temperature", base.temperature))
    max_tokens  = int(fm.get("max_tokens",  base.max_tokens))
    timeout_s   = int(fm.get("timeout_s",  base.timeout_s))
    # api_style / thinking likewise inherit from base when the derived file
    # does not set its own (usually they come from the paired MODEL, so they
    # are typically identical anyway).
```

Result: `latin-to-english-ocr.prompt_text == compose_prompts(latin-to-english.prompt_text, <ocr delta>)`.

### 2.2 Semantics to pin down

- **Ordering / precedence.** Base body first, then the delta, separated by
  `\n\n---\n\n` (as `compose_prompts` already does). Later text wins where the
  two conflict. A delta that must run BEFORE the base (OCR cleanup before
  translation) is phrased as a prelude inside the delta: *"Before applying
  the rules above, first …"* — this works because the base (translation)
  and the delta (cleanup) concern disjoint parts of the same page, so the
  order of the prose doesn't change the model's behaviour.
- **Settings cascade.** `temperature`/`max_tokens`/`timeout_s` (and
  `thinking`/`api_style` where a file sets them) come from the derived file
  when present, else from the base. Purely textual content never specifies
  these unless it wants to diverge.
- **Model is NOT inherited through `extends`.** A rules id selects a
  *model* via the per-document/collection `pha.yaml` pairing
  (`editor: {rules: latin-to-english-ocr, model: deepseek-v4-flash}`) or the
  file's legacy inline `model:`/`base_url:`/`api_key:`. `extends` affects
  only the prompt text and the sampling settings, so the derived rules id
  genuinely has its own model binding. Document this clearly.
- **Chains & safety.** Allow a shallow chain (A → B → C). A missing base → a
  warning and the file loads as-is. A cycle → an error at load time. Only
  ids inside the same stage directory (or the corresponding global stage
  registry) may be referenced — no arbitrary paths, no `include` of arbitrary
  files on disk (keep it a pure in-memory prompt composition).
- **Scope.** Editors first (the immediate need); the same directive should
  apply to `palaeographers/` and `encoders/` since they already share the
  `compose_prompts` pattern. For encoders, `extends` composes against another
  *encoder* base prompt before the existing `<name>.prompt.md` /
  `<name>.langextract.md` chain is applied.

### 2.3 Invalidation (important)

`_edit_needed` (and the equivalent re-run checks for palaeographers/encoders)
watch the mtime of the rules file (`editor.prompt_file`). When a document was
edited with a derived rules file and the BASE file changes, the derived
document would NOT re-edit. The stage object should carry the full list of
resolved prompt files (base chain + own file) and the change checks should
watch **all of them** — otherwise editing `latin-to-english.md` wouldn't
re-trigger `latin-to-english-ocr` documents.

### 2.4 Backward compatibility

Files without `extends` load exactly as today. The key is `extends`, which
the current front-matter loaders ignore, so existing archives are unaffected;
only an explicit new key changes behaviour.

## 3. Interim fallback (no repo change needed today)

Until the loader supports `extends`, the same "one source of truth" can be
got with a **build-time generator** that renders a self-contained derived
file by concatenating the base body + delta whenever the base changes. It
keeps the shipped file self-contained (pha needs nothing), but the expansion
must be re-run manually. Treat this purely as a stopgap; the runtime
`extends` is the real feature (no stale expansion, correct invalidation via
2.3).

## 4. Reference example (what the composition yields)

For the Documenta Indica case:

```
latin-to-english.prompt_text
  = <translation + names + line-number + ## Notes rules>

latin-to-english-ocr.prompt_text
  = latin-to-english.prompt_text
    + "\n\n---\n\n"
    + "BEFORE applying the rules above, run this OCR CLEAN-UP pass: …"
```

## 5. Tests to add

- loader composes base + delta in the right order (and settings cascade and
  inherit correctly);
- a derived editor resolves with the correct `prompt_text` and `model` from
  the `pha.yaml` pairing (model not inherited);
- a missing base warns and loads as-is; a cycle errors;
- `_edit_needed` re-triggers a document when the BASE file mtime changes
  (and not only the derived file);
- the documented example: `latin-to-english-ocr` is byte-identical in
  behaviour to a manually expanded copy.

## 6. Non-goals

- No arbitrary file `include`; `extends` is a prompt-composition directive
  against other rules ids only.
- No change to the JSON record output of the encoder stage.

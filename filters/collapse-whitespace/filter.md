---
name: collapse-whitespace
description: collapse justified-print space runs and index dotted leaders
accepts: text
returns: text
timeout_s: 60
params:
  collapse_leaders: true
  keep_blank_lines: true
---
Justified print comes out of OCR as runs of spaces between words, and an index
or table of contents uses dotted leaders:

    Malaca  . . . . . . . . . . . . . . 437

Both waste prompt budget and confuse the editor. This filter:

- collapses a run of 2+ spaces/tabs inside a line to ONE space;
- collapses a run of 3+ spaced dot groups (`. . . . .`, `· · ·`) to one space;
- **keeps `[3v]` / `[l. 10]` foliation and line references** — it never touches
  the inside of brackets;
- **keeps blank lines** (paragraph structure) and leading indentation is
  reduced only as part of a space run;
- **keeps a real ellipsis** (`...`, no spaces): only SPACED dot runs collapse.

Params: `collapse_leaders: false` keeps the dotted leaders (useful when the
leaders are themselves evidence), `keep_blank_lines: false` removes blank
lines entirely (paragraphs become single lines).

Run it BEFORE the model when the source is justified print and the editor is
easily distracted by spacing:

    editor:
      rules: latin-to-english
      model: deepseek-v4-flash
      pre: [collapse-whitespace]

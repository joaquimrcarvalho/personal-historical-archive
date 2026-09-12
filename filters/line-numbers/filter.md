---
name: line-numbers
description: turn MHSI margin line-numbers into [l. N] markers
accepts: text
returns: text
timeout_s: 60
params:
  side: head               # head | tail | both — which margin the numbers sit in
  max_gap: 8               # a plausible step to the next consecutive line number
  allow_non_sequential: false
---
Editions in the MHSI series number every fifth line in the margin. OCR puts the
digits inside the flow of text, where they read as content (folio numbers,
years, quantities) and leak into the editor's output as bare numbers:

    115 quem nao tiver ...

This filter replaces them with an explicit marker, so the text says what it is:

    [l. 115] quem nao tiver ...

How it decides, conservatively:

1. The line must START (or end, with `side: tail`) with a 1-3 digit number.
2. The number must be plausible as the next margin number: within `max_gap`
   (default 8) of the previous one this filter has seen, scanning forward.
   That is what keeps a year or a quantity from being wrapped.
3. `allow_non_sequential: true` skips rule 2 and wraps every leading number —
   use it for a clean edition whose margin numbers are irregular (some
   volumes restart per page); expect the occasional false positive.

The marker is `[l. N]`, which is also what the palaeographer/editor prompts
should refer to. `[3v]`-style foliation (a letter after the digits) never
matches, so it is left alone.

Line numbers are part of the EDITION's apparatus: apply it after the
palaeographer so the raw transcription records them as printed, and let the
editor ignore `[l. N]` tokens:

    palaeographer: {rules: ocr, model: liteparse, post: [line-numbers]}

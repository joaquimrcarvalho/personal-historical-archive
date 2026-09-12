---
name: footnote-marker-residue
description: remove stray superscript footnote-marker residue (*, **, °, º)
accepts: text
returns: text
timeout_s: 60
params:
  strip_trailing_markers: true
---
Print editions mark footnotes with symbols (`*`, `**`, `°`, `º`) as well as
digits. After transcription the symbol often survives with its note removed —
either alone on a line, or glued to the end of the word it followed:

    governador**
    *

This filter drops that residue. It deliberately keeps:

- **digit references** (`12`, `(3)`) — those are real footnote numbers and
  removing them would corrupt the apparatus;
- **footnote text blocks** — only a line consisting solely of marker
  characters is removed, so `* Cf. Sousa, Oriente Conquistado…` survives;
- emphasis a model might legitimately produce (`*word*`, `**word**`): the
  trailing rule needs two or more markers so a single `*` stays.

`strip_trailing_markers: false` limits it to marker-only lines.

Place it AFTER the palaeographer (it is source residue, not a model artefact),
or before the editor if you prefer the editor never to see it:

    palaeographer: {rules: ocr, model: liteparse, post: [footnote-marker-residue]}

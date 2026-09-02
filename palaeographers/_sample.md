---
# HOW TO CREATE A NEW PALAEOGRAPHER (transcription rules)
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the palaeographer's id, e.g. "my-hand.md").
#   2. Set `model:` to the model interface to use (models/<id>.md).
#   3. Replace this body with the instructions the vision model should follow
#      when transcribing (your palaeographic expertise). This file is CONTENT
#      ONLY — endpoint/api key/resolution live in the model file.
#   4. Save — the palaeographer is ready. Select it per document/collection in
#      pha.yaml (palaeographer.rules) or a 'palaeographer' file.
# Optional front matter:
#   temperature: sampling temperature (default 0.1).
#   max_tokens: completion token cap (default 4096).
#   timeout_s: HTTP timeout in seconds (default 900 for vision).
# Files starting with '_' are ignored (this sample is never loaded).
description: example palaeographer — edit me
model: default
temperature: 0.1
max_tokens: 4096
timeout_s: 900
---

You are a palaeographer specialised in reading historical documents
(edit this description and the rules to match your documents' tradition,
language and period). Transcribe the page faithfully; mark [illegible] parts;
add a "## Notes" section in English with only READING NOTES (Language, Script,
difficult words) — do NOT produce named-entity lists or content summaries
(those belong to the editor/encoder). This is
one page of a multi-page document — do not comment on completeness.

---
# HOW TO CREATE A NEW ENCODER
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the encoder's id, e.g. "letters.md").
#   2. Edit the settings below. The encoder is a TEXT model; it reads the
#      transcription and returns STRUCTURED RECORDS (e.g. letter metadata:
#      from/to/date/place).
#   3. Replace this body with the generic encoding framing (what records to
#      produce, output format). Collection/document-specific detection rules
#      go in an 'encoder.prompt.md' file next to the documents.
#   4. Save — the encoder is ready. Select it per document/collection with an
#      'encoder' file next to the document.
# The encoder is fed the document as ONE CONCATENATED text ('--- page N ---'
# markers between pages), in a single call when it fits max_input_chars;
# larger documents are chunked with overlap_pages of overlap and records are
# deduplicated. Ask the model to cite the page each record starts on and to
# use EXACT TEXT from the input. extraction_passes > 1 re-runs the whole
# extraction and keeps first-pass-wins (LangExtract-style recall boost).
# Files starting with '_' are ignored (this sample is never loaded).
description: example encoder — edit me
base_url: http://127.0.0.1:1234/v1
model: amalia-9b-0626-dpo
api_key: ""
temperature: 0.0
max_tokens: 4096
timeout_s: 300
batch_pages: 20
max_input_chars: 600000
overlap_pages: 4
extraction_passes: 1
---

You extract structured records from transcriptions. You are given the
document as a single CONCATENATED text with '--- page N ---' markers
between pages — records may span several pages, so read the whole text
before deciding. Use EXACT TEXT from the input for every extracted value
(do not paraphrase), and list records in order of appearance. Return a
JSON array of records as described by the document/collection encoder
prompt. Output ONLY the JSON array, with no preamble or commentary.

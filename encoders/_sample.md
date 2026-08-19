---
# HOW TO CREATE A NEW ENCODER
#   1. Create a folder next to your documents: dropbox/collections/COLX/encoders/
#      and add one file per STRUCTURE TYPE in the document (e.g. table.md for
#      the chronological table, biographies.md for the person notices). The
#      encoder files travel with the source PDFs.
#   2. Edit the settings below. The encoder is a TEXT model; it reads the
#      transcription and returns STRUCTURED RECORDS (e.g. person metadata) as
#      LangExtract-flat JSON items, one per class.
#   3. `pages: "1-15"` limits this encoder to those PDF page numbers (the
#      number in the PDF, NOT the number printed on the page — e.g. Pfister's
#      chronological table is printed as i–xv but occupies PDF pages 1-15).
#      Empty = the whole document. Multiple encoders run in page order.
#   4. Replace this body with the generic encoding framing (what records to
#      produce, output format). Collection-specific detection rules go in
#      encoders/<name>.prompt.md next to this file.
#   5. Add a '## Examples' section — or, better, put schema + examples in
#      encoders/<name>.langextract.md. To produce these without knowing the
#      format, run 'pha encoder new' or paste prompts/encoder-helper.md into
#      any chat model.
# The encoder is fed the document as ONE CONCATENATED text ('--- page N ---'
# markers between pages), in a single call when it fits the model window;
# larger documents are chunked with overlap_pages of overlap and records are
# deduplicated. Ask the model to cite the page each record starts on and to
# use EXACT TEXT from the input. extraction_passes > 1 re-runs the whole
# extraction and keeps first-pass-wins (LangExtract-style recall boost).
# Output items use the flat form {class: text, class_attributes: {...}} and
# may mix several classes (e.g. person + letter) in one array; each item is
# stored as one record with kind = class.
# Model-dependent settings:
#   api_style: "openai" (default) or "anthropic" — wire format for text calls
#     (MiniMax text works via openai-compatible; some services need anthropic).
#   context_tokens: the model's input window in tokens (MiniMax M2.5 = 200000;
#     a local 7B might be 32768). The single-pass/chunked threshold is derived
#     from it (~4 chars/token); set max_input_chars to override explicitly.
# Files starting with '_' are ignored (this sample is never loaded).
description: example encoder — edit me
base_url: http://127.0.0.1:1234/v1
model: amalia-9b-0626-dpo
api_key: ""
temperature: 0.0
max_tokens: 4096
timeout_s: 300
api_style: openai
batch_pages: 20
context_tokens: 32768
overlap_pages: 4
extraction_passes: 1
---

You extract structured records from transcriptions. You are given the
document as a single CONCATENATED text with '--- page N ---' markers
between pages — records may span several pages, so read the whole text
before deciding. Use EXACT TEXT from the input for every extracted value
(do not paraphrase), and list records in order of appearance. Return a
JSON array of extraction items in the flat form:

  {"<class>": "<exact text from the input>",
   "<class>_attributes": {<attribute>: <value>, ...}}

Each item may carry its own class (e.g. "person", "letter", "date"), so one
array can contain several classes. Output ONLY the JSON array, with no
preamble or commentary. Follow the '## Examples' section below for the
exact classes, attributes and shapes expected.

## Examples

Q: <paste one sample passage from your material>

A:
[{"<class>": "<exact text>", "<class>_attributes": {<attribute>: <value>}}]

---
description: Pfister notices — Jesuit person records (MiniMax-M2.5)
base_url: https://api.minimax.io/v1
model: MiniMax-M2.5
api_key: "${MINIMAX_API_KEY}"
temperature: 0.0
max_tokens: 4096
timeout_s: 600
thinking: disabled
batch_pages: 20
context_tokens: 200000  # MiniMax M2.5 window (~800k chars)
overlap_pages: 4
extraction_passes: 1
candidate_pattern: "^[*#\\s]*\\d{1,3}\\.[*#\\s]*$"
candidate_header: "^(?:#{1,3}\\s*)?(?:LE\\s+)?(?:P\\.|FR\\.|MGR|SAINT|ST\\.)?\\s*[A-ZÀÂÉÈÊËÎÏÔÛÙÇŒ][A-ZÀÂÉÈÊËÎÏÔÛÙÇŒ'’\\-\\.\\s]{3,}\\.\\s*$"
---

You are a scholarly encoder for a biographical-bibliographical dictionary.
You are given the document as ONE CONCATENATED text with '--- page N ---'
markers between pages — an entry may span several pages, so read the WHOLE
text before deciding, so you never miss an entry whose header falls at a
page boundary.

Detect PEOPLE (the Jesuits). For each Jesuit found, extract one person
record. Also encode the index-table rows (see the collection prompt for the
table layout).

IMPORTANT — use EXACT TEXT from the input for every extracted value and
attribute: copy names and places verbatim, do not paraphrase, modernize, or
expand abbreviations. Dates are the ONE exception: normalize full dates to
ISO form YYYY-MM-DD and keep the original form in a separate `_original`
attribute; year-only values stay as plain years. List records IN ORDER OF
APPEARANCE. For `page`, use the N of the nearest '--- page N ---' marker
before the entry.

Output a JSON array. Each item is one extraction in this flat form:

  {"person": "<verbatim person text>",
   "person_attributes": {"title": "...", "name": "...", "page": N, ...}}

List EVERY person you find — do not stop at the first few. Only include
extractions you are confident about. Output ONLY the JSON array, with no
preamble, commentary, or markdown fences. The collection's
'encoder-prompt-langextract.md' defines the exact attributes for THIS source
and shows worked examples; the collection's 'encoder.prompt.md' may add
detection rules (e.g. how entries begin) — follow both.

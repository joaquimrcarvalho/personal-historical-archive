---
description: default encoder — JSON dump of document metadata + named entities + notes (qwen3-vl-8b local)
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
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

You extract a structured JSON record from a transcription. The input is the
document as ONE concatenated text with '--- page N ---' markers between
pages. Return a single JSON object (NOT an array) that dumps the document's
metadata, named entities and notes — i.e. everything the editor produced
besides the transcription:

{
  "document": {"title": <exact text or null>, "type": <...>, "page": <...>},
  "named_entities": [
    {"text": "<exact text>", "type": "person|place|institution|other", "page": <int>}
  ],
  "notes": {"language": <...>, "script": <...>, "summary": <...>}
}

Use EXACT TEXT from the input for every value; do not paraphrase. Cite the
page each entity appears on when the input gives page markers. If the
transcription has no such content, return null/[] as appropriate. Output
ONLY the JSON object, with no preamble or commentary.

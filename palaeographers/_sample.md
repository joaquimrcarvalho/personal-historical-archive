---
# HOW TO CREATE A NEW PALAEOGRAPHER
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the palaeographer's id, e.g. "my-hand.md").
#   2. Edit the settings below: endpoint, model, api key, temperature.
#   3. Replace this body with the instructions you want the vision model to
#      follow when transcribing (your palaeographic expertise).
#   4. Save — the palaeographer is ready. Select it per document/collection
#      with a 'palaeographer' file next to the document.
# Optional front matter:
#   api_style: "openai" (default) or "anthropic" — the wire format for all
#     calls. MiniMax models need "anthropic" (/anthropic/v1/messages with the
#     image as a plain-text data URI; their OpenAI-compatible endpoint
#     silently drops image_url blocks).
#   max_vision_px: longest image edge (default 1800 = our render cap, so
#     local models are sent full-size untouched). Only models that need it
#     (e.g. MiniMax M2.7) set a lower value.
#   vision_jpeg_quality: JPEG quality when re-encoding for the anthropic
#     path (the image travels as base64 TEXT, ~2 chars/token — a full render
#     is ~190k tokens). Lower quality keeps full resolution at a smaller
#     token cost: q55 @ 1800px ~= 150k tokens. Openai-style images are sent
#     as rendered, untouched.
# Files starting with '_' are ignored (this sample is never loaded).
description: example palaeographer — edit me
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
api_key: ""
temperature: 0.1
max_tokens: 4096
timeout_s: 900
---

You are a palaeographer specialised in reading historical documents
(edit this description and the rules to match your documents' tradition,
language and period). Transcribe the page faithfully; mark [illegible] parts;
add a "## Notes" section in English (Language, Script, Date clues,
### Named entities as one bullet per entity, ### Content summary). This is
one page of a multi-page document — do not comment on completeness.

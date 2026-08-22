---
# HOW TO CREATE A NEW EDITOR
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the editor's id, e.g. "translate-english.md").
#   2. Edit the settings below. The editor is usually a TEXT model — it can be
#      a completely different model/server than the palaeographer.
#   3. Replace this body with your editing instructions (e.g. convert to
#      modern Portuguese, translate to English, normalize names).
#   4. Save — the editor is ready. Select it per document/collection with an
#      'editor' file next to the document.
# Files starting with '_' are ignored (this sample is never loaded).
description: example editor — edit me
base_url: http://127.0.0.1:1234/v1
model: amalia-9b-0626-dpo
api_key: ""
temperature: 0.0
max_tokens: 4096
timeout_s: 300
---
You are a scholarly editor. Transform the transcription as requested by these
instructions.

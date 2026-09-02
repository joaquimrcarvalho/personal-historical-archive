---
# HOW TO CREATE A NEW EDITOR (transform rules)
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the editor's id, e.g. "translate-english.md").
#   2. Set `model:` to the text model to use (models/<id>.md).
#   3. Replace this body with your editing instructions (e.g. convert to
#      modern Portuguese, translate to English, normalize names). CONTENT ONLY.
#   4. Save — the editor is ready. Select it per document/collection in
#      pha.yaml (editor.rules) or an 'editor' file.
# Optional front matter:
#   temperature: sampling temperature (default 0.1).
#   max_tokens: completion token cap (default 4096).
#   timeout_s: HTTP timeout in seconds (default 300 for text).
# Files starting with '_' are ignored (this sample is never loaded).
description: example editor — edit me
model: default
temperature: 0.0
max_tokens: 4096
timeout_s: 300
---

You are a scholarly editor. Transform the transcription as requested by these
instructions. Keep the content faithful: do not add, remove or reorder
information. Keep the document structure. Output only the edited text.

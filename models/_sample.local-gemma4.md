---
# SAMPLE local vision model — gemma-4 served by LM Studio — never loaded (name
# starts with '_'). To USE it: copy this file to models/gemma4-vision-local.md
# (drop the leading '_'), set the server-side model name (e.g.
# `google/gemma-4-e4b`, or the `-mlx` build on Apple Silicon), and select it per
# document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: gemma4-vision-local}
# This is the MODEL INTERFACE only (endpoint + limits); the transcription rules
# live in a content-only palaeographer file.
#   max_vision_px 3000 keeps dense printed pages legible (the page only reaches
#     that size when the collection's render setting allows it).
#   thinking: disabled avoids the `<|channel>thought` blocks gemma-4 can emit.
#   timeout_s is NOT a model field: set it in the stage rules file.
description: gemma-4 via LM Studio (local) — vision + text
base_url: http://127.0.0.1:1234/v1
model: google/gemma-4-e4b
api_key: ""
api_style: openai
max_vision_px: 3000
vision_jpeg_quality: 88
context_tokens: 32768
thinking: disabled
---



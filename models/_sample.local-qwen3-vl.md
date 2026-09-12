---
# SAMPLE local vision model — qwen3-vl served by LM Studio — never loaded (name
# starts with '_'). To USE it: copy this file to models/qwen3-vision-local.md
# (drop the leading '_'), set the server-side model name, and select it per
# document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: qwen3-vision-local}
# This is the MODEL INTERFACE only (endpoint + limits); the transcription rules
# live in a content-only palaeographer file.
#   context_tokens MUST match the model's real window — it drives encoder
#     chunking (the Model default, 200000, is far too large for a local model).
#   thinking: disabled skips reasoning blocks (some builds leak
#     `<|channel>thought` into the transcript) — faster and cleaner.
#   timeout_s is NOT a model field: the stage timeout lives in the
#     palaeographer/editor rules file front matter.
description: qwen3-vl via LM Studio (local) — vision + text
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
api_key: ""
api_style: openai
max_vision_px: 1800
vision_jpeg_quality: 88
context_tokens: 32768
thinking: disabled
---



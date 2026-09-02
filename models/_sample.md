---
# HOW TO CREATE A NEW MODEL (interface)
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the model id, e.g. "minimax-m3.md").
#   2. Edit the settings below: endpoint (base_url), server model name, api
#      key, wire format (api_style), and the limits (max_vision_px,
#      vision_jpeg_quality, context_tokens).
#   3. Reference it from a palaeographer/editor/encoder file with `model: <id>`,
#      or override it per document/collection in pha.yaml.
#   4. Save — the model is ready.
# Fields (all optional except base_url/model):
#   base_url: the API root (LM Studio/Ollama/vLLM/OpenAI/MiniMax/...).
#   model: the server-side model name (e.g. qwen/qwen3-vl-8b).
#   api_key: the API key, as ${ENV} or a literal (secrets stay in .env/keychain).
#   api_style: "openai" (default) or "anthropic" — wire format for ALL calls.
#   thinking: true/false — allow (or disable) reasoning-block models.
#   max_vision_px: longest image edge sent to a vision model (default 1800).
#   vision_jpeg_quality: JPEG quality when re-encoding for the vision path.
#   context_tokens: the model's input window in tokens (drives encoder chunking).
# Files starting with '_' are ignored (this sample is never loaded).
description: example model — edit me
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
api_key: ""
api_style: openai
max_vision_px: 1800
vision_jpeg_quality: 88
context_tokens: 32768
---


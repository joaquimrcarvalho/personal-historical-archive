---
# HOW TO CREATE A NEW MODEL (interface)
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the model id, e.g. "minimax-m3.md").
#   2. Edit the settings below: endpoint (base_url), server model name, api
#      key, wire format (api_style), and the limits (max_vision_px,
#      vision_jpeg_quality, context_tokens).
#   3. Reference it from a pha.yaml sidecar, e.g.
#      `palaeographer: {rules: <id>, model: <id>}`.
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
#
# NON-LLM ENGINES (no HTTP endpoint): set an `engine` to drive the palaeographer
# stage with a LOCAL OCR/parse tool instead of an LLM vision call. Engines have
# no base_url/model/api_key; set `engine` + the engine's settings instead:
#   engine: tesseract                # Tesseract OCR (needs `tesseract` on PATH)
#   tesseract_lang: por              # -l value ("por", "lat", "por+lat", ...; "" = tesseract default)
#   tesseract_psm: 6                 # optional --psm page-segmentation mode
#   engine: liteparse                # LiteParse `lit parse` (needs `lit` on PATH:
#                                    #   pip install liteparse | npm i -g @llamaindex/liteparse)
#   liteparse_lang: por              # --ocr-language (Tesseract format, e.g. "por", "fra")
#   liteparse_dpi: 300               # optional --dpi render resolution (default 150; 300 = quality)
#   liteparse_format: text           # output: "text" (default) | "markdown" | "json"
#                                    #   ("json" = text + per-item bboxes/confidence for a
#                                    #   later reasoning/encoder stage)
#   liteparse_ocr: fresh             # input: "fresh" (default) = OCR the rendered page
#                                    #   raster (ignores any embedded text layer; safe on
#                                    #   scans); "embedded" = parse the ORIGINAL source PDF
#                                    #   page, using its embedded/native text layer where
#                                    #   present (fast on typed PDFs; may surface an archive's
#                                    #   old low-quality layer); "prefer-embedded" = per page,
#                                    #   use the embedded layer only when it passes the quality
#                                    #   gate below, else OCR the raster. Non-PDF sources:
#                                    #   always fresh.
#   liteparse_embedded_min_chars: 200      # prefer-embedded gate: minimum non-whitespace
#                                          #   characters for the layer to be considered
#   liteparse_embedded_min_quality: 0.60   # prefer-embedded gate: ratio floor for "share of
#                                          #   characters that are letters" and "share of tokens
#                                          #   that look like words"
# Then select it per document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: <this-model-id>}
# (or inline `engine: tesseract`/`engine: liteparse` in the palaeographer's own
# front matter). Engines are a registry in model_client.PAGE_ENGINES — add a
# run_* helper + entry there (and the settings on Model/Palaeographer) for a new
# local tool.
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


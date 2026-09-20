---
# DeepSeek via api.deepseek.com — the REMOTE provider pha is usually paired
# with. Copy this file to `models/<id>.md` (drop the leading '_'), then select
# that id in a pha.yaml sidecar:
#     editor: {rules: <rules-id>, model: <this-model-id>}
#
# Store the API key ONCE per machine (reads it from stdin):
#     pha key --set PHA_ARCHIVIST
#
# `api_key: ${PHA_ARCHIVIST}` below is NOT the key: it is the NAME of the
# variable pha resolves — real environment first, then the archive's gitignored
# .env, then the OS secret store. Never paste a key into this file; model files
# are meant to be shareable. (`pha key` prints where each name resolves.)
#
# Two ids are provided below. The first is a TEXT model (editors, encoders);
# the second, commented, is the VISION model (palaeographer — it sends page
# images). Keep one file per id: copy this file twice and delete the half you
# do not need.
description: DeepSeek V4 Flash (text) via api.deepseek.com
base_url: https://api.deepseek.com/v1
api_key: ${PHA_ARCHIVIST}
model: deepseek-v4-flash
thinking: disabled
# context_tokens: the model's input window, in tokens. It is NOT the API limit;
# it drives pha's encoder chunking (max_input_chars ≈ 4 chars/token), so set it
# to the real window if you know it. Default 200000.
---
# ---------------------------------------------------------------------------
# VISION (palaeographer stage). A vision model is what reads page images
# directly; the text model above cannot. To use it, copy these lines into a
# SECOND model file (e.g. models/deepseek-vision.md), uncomment them, and point
# a collection's palaeographer at that id. They are shown here as a comment
# because a model file is one interface — the body of a model file is ignored
# on load.
#
# description: DeepSeek V4 Flash Vision (palaeographer) via api.deepseek.com
# base_url: https://api.deepseek.com/v1
# api_key: ${PHA_ARCHIVIST}
# model: deepseek-v4-flash-vision-exp
# api_style: openai
# thinking: enabled
# max_vision_px: 3000          # longest image edge sent (default 1800)
# vision_jpeg_quality: 55      # JPEG quality when re-encoding for the vision path

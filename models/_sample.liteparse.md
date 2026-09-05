---
# SAMPLE LiteParse model — never loaded (name starts with '_').
# To USE it: copy this file to models/liteparse.md (drop the leading '_'),
# tweak the settings, and select it per document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: liteparse}
# LiteParse is a LOCAL document/OCR parser, NOT an LLM: install the `lit` CLI
# (`pip install liteparse` or `npm i -g @llamaindex/liteparse`). It gives
# layout-preserved text and (with json) per-item bounding boxes/confidence a
# later reasoning/encoder stage can use.
# liteparse_ocr:
#   fresh (default) — OCR the rendered page raster, so LiteParse must OCR it
#     (no embedded text layer to fall back on; safe on historical scans).
#   embedded — parse the ORIGINAL source PDF page, using the PDF's embedded/
#     native text layer where present (faster on typed PDFs, but may surface
#     an archive's old low-quality layer). Non-PDF sources are always fresh.
# liteparse_format:
#   text (default) — layout-preserved plain text as the transcript.
#   markdown — structured markdown.  json — text + per-item bboxes/confidence.
description: LiteParse (local document parser / OCR)
engine: liteparse
liteparse_lang: por          # --ocr-language (Tesseract format: "por", "fra", ...)
liteparse_dpi: 300           # optional --dpi render resolution (default 150; 300 = quality)
liteparse_ocr: fresh         # fresh (default) | embedded
liteparse_format: text       # text (default) | markdown | json
---



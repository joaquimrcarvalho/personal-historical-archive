---
# SAMPLE LiteParse model configured for printed SPANISH (+ Latin) — never
# loaded (name starts with '_'). To USE it: copy this file to
# models/liteparse-spa.md (drop the leading '_') and pair it with a content-only
# palaeographer rules file in pha.yaml:
#   palaeographer: {rules: ocr, model: liteparse-spa}
# The `spa` (+ `lat`) Tesseract language data must be installed on the archive
# machine. See models/_sample.liteparse.md for liteparse_ocr / liteparse_format.
description: LiteParse (local OCR) — printed Spanish (+ Latin)
engine: liteparse
liteparse_lang: spa+lat       # --ocr-language (Tesseract: "spa+lat")
liteparse_dpi: 300            # optional --dpi render resolution (300 = quality)
liteparse_ocr: fresh          # OCR the rendered raster
liteparse_format: text        # layout-preserved plain text
---



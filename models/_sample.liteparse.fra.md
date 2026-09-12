---
# SAMPLE LiteParse model configured for printed FRENCH — never loaded (name
# starts with '_'). To USE it: copy this file to models/liteparse-fra.md (drop
# the leading '_') and pair it with a content-only palaeographer rules file in
# pha.yaml:  palaeographer: {rules: ocr, model: liteparse-fra}
# The `fra` Tesseract language data must be installed on the archive machine
# (mac: `brew install tesseract-lang`). See models/_sample.liteparse.md for the
# full explanation of liteparse_ocr / liteparse_format.
description: LiteParse (local OCR) — printed French
engine: liteparse
liteparse_lang: fra           # --ocr-language (Tesseract format: "fra")
liteparse_dpi: 300            # optional --dpi render resolution (300 = quality)
liteparse_ocr: fresh          # OCR the rendered raster ("embedded" only when
                              # the PDF's own text layer is good)
liteparse_format: text        # layout-preserved plain text
---



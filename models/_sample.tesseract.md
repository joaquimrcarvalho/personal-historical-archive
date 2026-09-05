---
# SAMPLE Tesseract OCR model — never loaded (name starts with '_').
# To USE it: copy this file to models/tesseract.md (drop the leading '_'),
# tweak the language, and select it per document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: tesseract}
# Tesseract is a LOCAL OCR engine, NOT an LLM: it needs the `tesseract`
# executable + its language data on PATH (mac: `brew install tesseract
# tesseract-lang`). It reads printed/typeset text well but is weak on dense
# handwriting — use a vision model for manuscripts.
description: Tesseract OCR (local) — printed/typeset text
engine: tesseract
tesseract_lang: por+lat      # -l value(s): "por", "lat", "por+lat", "fra", "eng" ("" = tesseract default)
tesseract_psm: 6             # optional --psm page-segmentation mode (3 = auto; 6 = single uniform block)
---



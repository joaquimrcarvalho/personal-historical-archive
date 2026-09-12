---
# SAMPLE LiteParse model — REUSE a good embedded PDF text layer, else OCR —
# never loaded (name starts with '_'). To USE it: copy this file to
# models/liteparse-embedded.md (drop the leading '_') and pair it with a
# content-only palaeographer rules file in pha.yaml:
#   palaeographer: {rules: ocr, model: liteparse-embedded}
# LiteParse is a LOCAL document/OCR parser, NOT an LLM (install the `lit` CLI:
# `pip install liteparse` or `npm i -g @llamaindex/liteparse`).
# Why this variant: `liteparse_ocr: embedded` ALWAYS parses the original PDF
# page — great when the PDF carries a real text layer, harmful when that layer
# is the residue of a bad OCR pass. `prefer-embedded` decides PER PAGE: pha
# reads the page's embedded text with pymupdf (no OCR, no subprocess) and
# reuses it only when it passes the quality gate (enough text, mostly letters,
# tokens that look like words); otherwise the page is OCR'd from the rendered
# raster exactly as with `fresh`. So one model covers a mixed collection —
# born-digital or well-OCR'd pages are not re-OCR'd, scans with a junk layer
# are. Non-PDF sources (single images / folders of images) always OCR.
# Gate tunables (defaults shown; lower them for sparse pages, raise them if a
# poor layer still slips through):
#   liteparse_embedded_min_chars: 200      # non-whitespace chars on the page
#   liteparse_embedded_min_quality: 0.60   # ratio floor for letter-share and
#                                          # word-likeness (0..1)
description: LiteParse (local OCR) — reuse a good embedded PDF text layer, else OCR
engine: liteparse
liteparse_lang: por           # --ocr-language (Tesseract format: "por", "fra", ...)
liteparse_dpi: 300            # render resolution used when the page must be OCR'd
liteparse_ocr: prefer-embedded   # fresh | embedded | prefer-embedded
liteparse_format: text        # layout-preserved plain text
liteparse_embedded_min_chars: 200
liteparse_embedded_min_quality: 0.60
---



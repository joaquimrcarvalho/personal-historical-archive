---
# SAMPLE OCR palaeographer — never loaded (name starts with '_').
# OCR engines (tesseract / liteparse) IGNORE the prompt body below — the OCR
# behaviour and its settings live in the MODEL the rules are paired with
# (models/tesseract.md: tesseract_lang/psm; models/liteparse.md:
# liteparse_lang/dpi/ocr/format). This content file only NAMES the reading pass
# and gives it a friendly description (shown by `pha palaeographer` and in the
# library headers).
# To USE it: copy this file to palaeographers/ocr.md (drop the leading '_') and
# pair it with an OCR model per document/collection in pha.yaml, e.g.:
#   palaeographer: {rules: ocr, model: tesseract}    # or: model: liteparse
# For a distinct id per engine (nicer library folder names), copy it twice as
# ocr-tesseract.md and ocr-liteparse.md — the content is the same either way.
description: OCR palaeographer (printed/typeset transcript via a local OCR engine; pair it with model tesseract or liteparse)
---
This page is transcribed by a LOCAL OCR engine (Tesseract or LiteParse), NOT a
vision model — no prompt is sent. The engine and its settings (language, dpi,
fresh/embedded OCR, output format) are defined by the model this rules file is
paired with; see the samples in models/_sample.tesseract.md and
models/_sample.liteparse.md. The engine's layout-preserved text becomes this
page's transcript, so the transcription is a faithful machine reading —
correct obvious OCR slips later in the editor pass or by review.

---
name: strip-ocr-page-separator
description: remove the OCR/parse engine's synthetic page-separator line
accepts: text
returns: text
timeout_s: 60
params:
  marker: '^\s*[-–—=*_]{0,4}\s*\[?\s*(page|pág|pag|p|pp)\b[^\n]{0,12}$'
  only_first_line: false
---
LiteParse (and some OCR paths) emit a synthetic separator per page, e.g.

    --- Page 7 ---

which is not part of the source and must not reach the editor, the encoder or
search. This filter removes that line.

- A marker as the FIRST line of the page text is always removed.
- A marker later in the text (a page file holding several source pages) is
  removed too, unless `params.only_first_line: true`, which keeps anything that
  merely looks like a marker mid-page.
- Nothing else is touched; no other whitespace is collapsed.

Adopt it where the engine inserts the marker, e.g. a LiteParse collection:

    palaeographer:
      rules: ocr
      model: liteparse
      post: [strip-ocr-page-separator]

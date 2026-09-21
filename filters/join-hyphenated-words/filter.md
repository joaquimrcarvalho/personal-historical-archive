---
name: join-hyphenated-words
description: re-join a word split across a line break, KEEPING the line breaks (gover-/nador -> governador, Dizer-/-vos -> Dizer-vos)
accepts: text
returns: text
timeout_s: 60
params:
  keep_hyphen_before_enclitic: true
  notsign_as_hyphen: true
---
A word broken at the end of a line comes back from OCR with the hyphen intact,
so the text carries a word that does not exist:

    ... o gover-
    nador mandou ...

This filter joins it **without merging the lines**: the continuation fragment is
pulled UP to close the word at the end of the current line, and the rest of the
next line stays on its own line. Attached punctuation travels with the fragment.

    ... & em seu cen-           ... & em seu centro,
    tro, todas as ...      ->   todas as ...

    ... em obsequio de-         ... em obsequio destes
    stes vosos Sanct.      ->   vosos Sanct.

The printed lineation is therefore preserved — what an edition that quotes the
page by line wants. (An earlier revision of this filter merged the two lines
into one long line; that is no longer its behaviour.)

Four cases, in order:

0. **`¬` (U+00AC NOT SIGN) instead of `-`** — some embedded PDF text layers
   (ABBYY-style; the Internet Archive scans of the `francisco-rodrigues-hcjap`
   collection) write the end-of-line hyphen as `¬`:

       o filho da Compa¬
       nhia «há de empenhar-se»   ->   o filho da Compa-
                                        nhia «há de ...»

   A `¬` at end of line is normalised to `-` first, so cases 1-3 below apply
   unchanged. Measured on those seven volumes: 761 of 761 `¬` sit immediately
   before a newline and none occur mid-line, which is why treating an
   end-of-line `¬` as a hyphen is safe. Set `notsign_as_hyphen: false` to
   leave `¬` alone (e.g. a text where it is a real NOT SIGN operator).

1. **Doubled hyphen** — the line ends with `-` AND the next starts with `-`:
   join keeping ONE hyphen, because the source really is hyphenated:

       Dizer- / -vos   ->  Dizer-vos
       del-   / -rei   ->  del-rei

2. **Enclitic pronoun** — the continuation is a short pronoun ending (`vos`,
   `me`, `se`, `lhe`, `o`, `a`, `nos`, ...): keep the hyphen, since Portuguese
   attaches it there:

       encarecer- / vos mandou  ->  encarecer-vos mandou

3. **Soft hyphen** — the line ends with `-` and the next starts with a
   lowercase word: drop the hyphen (it was only line-breaking):

       gover- / nador  ->  governador

The next line must start lowercase for cases 2-3, which is what stops a real
compound line-break (`Anti-` / `Cristo`) from being mangled.

`keep_hyphen_before_enclitic: false` turns off case 2 (useful for a text where
`-vos` on the next line is a genuine new word, e.g. a dialogue dash).

A break that has **reflowed into the MIDDLE of a line** (`nem pro¬ pinas`,
`sem o cui¬ dar`) has no line left to preserve, so it is joined in place
(`nem propinas`, `sem o cuidar`) — the shape produced by
`liteparse_format: markdown`. A mid-line `-` is kept only before an enclitic
(`encarecer- vos` -> `encarecer-vos`).

Adoption — after a model, so the model's own re-wrapping is caught too:

    editor:
      rules: franco-imagem-virtude
      model: deepseek-v4-flash
      post: [join-hyphenated-words]

    palaeographer:
      rules: ocr
      model: tesseract-psm3
      post:
        - clean-lit-output
        - {name: join-hyphenated-words, params: {notsign_as_hyphen: true}}

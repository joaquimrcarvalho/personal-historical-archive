---
name: join-hyphenated-words
description: re-join a word split across a line break (gover-/nador, Dizer-/-vos)
accepts: text
returns: text
timeout_s: 60
params:
  keep_hyphen_before_enclitic: true
---
A word broken at the end of a line comes back from OCR with the hyphen intact,
so the text carries a word that does not exist:

    ... o gover-
    nador mandou ...

This filter joins it. Three cases, in order:

1. **Doubled hyphen** — the line ends with `-` AND the next starts with `-`:
   join keeping ONE hyphen, because the source really is hyphenated:

       Dizer- / -vos   ->  Dizer-vos
       del-   / -rei   ->  del-rei

2. **Enclitic pronoun** — the line ends with `-` and the next starts with a
   short pronoun ending (`vos`, `me`, `se`, `lhe`, `o`, `a`, `nos`, ...): keep
   the hyphen, since Portuguese attaches it there:

       encarecer-vo- / s  ->  encarecer-vos

3. **Soft hyphen** — the line ends with `-` and the next starts with a
   lowercase word: drop the hyphen (it was only line-breaking):

       gover- / nador  ->  governador

The next line must start lowercase for cases 2-3, which is what stops a real
compound line-break (`Anti-` / `Cristo`) from being mangled.

`keep_hyphen_before_enclitic: false` turns off case 2 (useful for a text where
`-vos` on the next line is a genuine new word, e.g. a dialogue dash).

Applies to the model's OUTPUT as well — an editor that reformats and re-wraps
lines can introduce fresh breaks:

    editor:
      rules: latin-to-english
      model: deepseek-v4-flash
      post: [join-hyphenated-words]

"""Re-join words split across a line break, KEEPING the printed lineation.

The continuation fragment is pulled UP to complete the word at the end of the
current line (with any punctuation attached to it); the remainder of the next
line stays on its own line:

    ... & em seu cen-          ... & em seu centro,
    tro, todas as ...     ->   todas as ...

Line breaks are therefore preserved, exactly as an edition that quotes the
printed page wants them. See filter.md.
"""

import re

# enclitic/mesoclitic pronoun endings that legitimately carry a hyphen
_ENCLITICS = {
    "o", "a", "os", "as", "lo", "la", "los", "las",
    "me", "te", "se", "nos", "vos", "lhe", "lhes",
}

# a line ending in a hyphen
_END_HYPHEN = re.compile(r"(\S)-[ \t]*$")
# the next line: optional leading hyphen, its first token, then the remainder
_NEXT = re.compile(r"[ \t]*(-?)([^\s]+)(.*)$")
# Some embedded PDF text layers (ABBYY-style, e.g. the Internet Archive scans)
# encode the END-OF-LINE hyphen as U+00AC NOT SIGN rather than U+002D:
#     "o filho da Compa\u00ac\nnhia"   (line 1 ends "Compa¬")
# Measured on this archive's `francisco-rodrigues-hcjap` volumes: 761 of 761
# "¬" occur immediately before a newline and none mid-line, so a "¬" at EOL is
# always a line-break hyphen. Normalising it to "-" lets the cases below apply
# unchanged (dropped before a lowercase word, kept for an enclitic).
_NOT_SIGN_EOL = re.compile("\u00ac[ \t]*(?=\n)")

# A break that has reflowed into the MIDDLE of a line. This is the shape the
# text takes under `liteparse_format: markdown`, which re-wraps paragraphs (and
# reconstructs reading order), so the line break the hyphen belonged to is gone:
#     "nem pro\u00ac pinas"        -> "nem propinas"
#     "sem o cui\u00ac dar"        -> "sem o cuidar"
# Such a break has no line to preserve, so it is joined in place. Only a
# LOWERCASE continuation is joined, exactly as for the line-end cases, so a
# range ("389- -390"), an em-dash break (". - Aceitação") and a real compound
# ("greco- romano" would be joined) are not mistaken for a split word.
_MID_BREAK = re.compile(r"(\S)([\u00ac-])[ \t]+(?=[a-zà-ÿ])")


def _is_enclitic(fragment: str) -> bool:
    return fragment.lower().strip(".,;:!?)]}»\"'") in _ENCLITICS


def _mid_joiner(m: re.Match) -> str:
    """Join a mid-line break: drop it, unless a real '-' precedes an enclitic."""
    head, mark = m.group(1), m.group(2)
    if mark == "-":
        rest = m.string[m.end():]
        word = re.match(r"[a-zà-ÿ]+", rest)
        if word and _is_enclitic(word.group(0)):
            return head + "-"          # encarecer- vos -> encarecer-vos
    return head                          # pro¬ pinas -> propinas


def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


def run(value, ctx):
    """Join hyphen-split words, KEEPING the line breaks.

    (a) ``X-`` + lowercase continuation -> drop the hyphen and pull the
        continuation up (``gover-``/``nador`` -> ``governador`` at the end of
        the line), unless the continuation is a pronoun ending
        (``encarecer-``/``vos`` -> ``encarecer-vos``).
    (b) a hyphen at BOTH the end of one line and the start of the next
        (``X-``/``-Y``) -> one word keeping ONE internal hyphen
        (``Dizer-``/``-vos`` -> ``Dizer-vos``).

    In both cases the line break stays where it was in the print; only the
    split word moves up to close the line, and the rest of the next line stays
    on its own line. Attached punctuation travels with the fragment
    (``cen-``/``tro,`` -> ``centro,``).

    A ``¬`` (U+00AC) at end of line is first normalised to ``-`` when
    ``notsign_as_hyphen`` is on (default), so embedded PDF text layers that use
    NOT SIGN for the line-break hyphen are handled by the same cases.

    (c) a break reflowed into the MIDDLE of a line (``pro¬ pinas``,
        ``gover- nador``) -> joined in place; there is no line to preserve.
    """
    if not isinstance(value, str) or not value:
        return value
    params = ctx.get("params") or {}
    keep_enclitic = _truthy(params.get("keep_hyphen_before_enclitic", "true"))
    if _truthy(params.get("notsign_as_hyphen", "true")):
        value = _NOT_SIGN_EOL.sub("-", value)
    # (c) reflowed mid-line breaks, before the line-oriented (a)/(b) cases
    value = _MID_BREAK.sub(_mid_joiner, value)

    lines = value.split("\n")
    i = 0
    # A single forward pass suffices: a join modifies ONLY line i+1, which is
    # then processed on the next iteration — so a chain of consecutive hyphen
    # lines ("...Compa-/nhia" then "...maior uni-/versalidade") closes one after
    # the other.
    while i < len(lines) - 1:
        m_end = _END_HYPHEN.search(lines[i])
        if not m_end:
            i += 1
            continue
        m = _NEXT.match(lines[i + 1])
        if not m:
            i += 1
            continue
        dash, frag, rest = m.group(1), m.group(2), m.group(3)
        # only a LOWERCASE continuation is a split word: "Anti-" / "Cristo" is a
        # real compound break and must be left alone
        if not frag[:1].islower():
            i += 1
            continue
        joiner = "-" if (dash == "-" or (keep_enclitic and _is_enclitic(frag))) else ""
        lines[i] = lines[i][: m_end.start()] + m_end.group(1) + joiner + frag
        rest = rest.lstrip()
        if rest == "":
            del lines[i + 1]          # the next line was only the continuation
        else:
            lines[i + 1] = rest
        i += 1
    return "\n".join(lines)

"""Re-join words split across a line break. See filter.md."""

import re

# enclitic/mesoclitic pronoun endings that legitimately carry a hyphen
_ENCLITICS = {
    "o", "a", "os", "as", "lo", "la", "los", "las",
    "me", "te", "se", "nos", "vos", "lhe", "lhes",
}

# a line ending in a hyphen
_END_HYPHEN = re.compile(r"(\S)-[ \t]*$")
# a line starting with a hyphen (the OTHER half of a doubled split)
_START_HYPHEN = re.compile(r"^[ \t]*-(\S)")


def _is_enclitic(fragment: str) -> bool:
    return fragment.lower().strip(".,;:!?)]}»\"'") in _ENCLITICS


def run(value, ctx):
    """Join hyphen-split words.

    (a) ``X-`` + lowercase continuation -> drop the hyphen (``gover-``/``nador``
        -> ``governador``), unless the continuation is a pronoun ending
        (``encarecer-vo-``/``s`` -> ``encarecer-vos``).
    (b) a hyphen at BOTH the end of one line and the start of the next
        (``X-``/``-Y``) -> one word keeping ONE internal hyphen
        (``Dizer-``/``-vos`` -> ``Dizer-vos``).
    """
    if not isinstance(value, str) or not value:
        return value
    params = ctx.get("params") or {}
    verbose = str(params.get("keep_hyphen_before_enclitic", "true")).lower() in ("1", "true", "yes")

    lines = value.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            m_start = _START_HYPHEN.match(nxt)
            # (b) doubled hyphen: keep exactly one
            if _END_HYPHEN.search(line) and m_start:
                out.append(_END_HYPHEN.sub(r"\1-", line) + m_start.group(1) + nxt[m_start.end():])
                i += 2
                continue
            m_end = _END_HYPHEN.search(line)
            if m_end and nxt[:1].islower():
                head, cont = line[: m_end.start()], nxt.lstrip()
                first = re.match(r"[a-zà-ÿ]+", cont)
                frag = first.group(0) if first else cont
                keep = verbose and _is_enclitic(frag)
                joiner = "-" if keep else ""
                out.append((head + m_end.group(1) + joiner + cont).rstrip())
                i += 2
                continue
        out.append(line)
        i += 1
    return "\n".join(out)

"""Turn MHSI margin line-numbers into `[l. N]` markers. See filter.md."""

import re

# The number marker itself: 1-3 digits optionally in brackets/parens/with a dot.
_NUM = r"\d{1,3}"
_HEAD = re.compile(rf"^\s*[\[\u2016|]?\s*({_NUM})\s*[\].:]?\s+(?=\S)")
_TAIL = re.compile(rf"\s+[\[\u2016|]?\s*({_NUM})\s*[\].:]?\s*$")


def _fits_gap(prev: int | None, this: int, max_gap: int) -> bool:
    """Is `this` plausibly the next margin number after `prev`?"""
    if prev is None:
        return True
    return 0 < (this - prev) <= max_gap


def run(value, ctx):
    if not isinstance(value, str) or not value:
        return value
    params = ctx.get("params") or {}
    side = str(params.get("side", "head")).lower()
    try:
        max_gap = int(params.get("max_gap", 8))
    except (TypeError, ValueError):
        max_gap = 8
    allow_any = str(params.get("allow_non_sequential", "false")).lower() in ("1", "true", "yes")

    lines = value.split("\n")
    out: list[str] = []
    last: int | None = None
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        m = _HEAD.match(line) if side in ("head", "both") else None
        if m:
            n = int(m.group(1))
            if allow_any or _fits_gap(last, n, max_gap):
                last = n
                out.append(f"[l. {n}] " + line[m.end():].rstrip())
                continue
        m = _TAIL.search(line) if side in ("tail", "both") else None
        if m:
            n = int(m.group(1))
            if allow_any or _fits_gap(last, n, max_gap):
                last = n
                out.append(line[: m.start()].rstrip() + f" [l. {n}]")
                continue
        out.append(line)
    return "\n".join(out)

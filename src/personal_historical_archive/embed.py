from __future__ import annotations

from typing import Sequence

import numpy as np

DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


def prefixed(model: str, text: str, kind: str) -> str:
    """nomic-embed-text v1.5 was trained with these prefixes; harmless for others."""
    if "nomic" not in model.lower():
        return text
    return (DOC_PREFIX if kind == "doc" else QUERY_PREFIX) + text


def pack(v: Sequence[float]) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def unpack(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# --------------------------------------------------------------------- sizing
#
# The /embeddings endpoint says nothing when it truncates: an input longer than
# the model's context is cut silently and the vector describes only its
# beginning. What fits therefore has to be decided HERE, before the request.

# Characters per token. The same heuristic the encoders use for their prompts
# (~4 for European prose). Too generous for token-dense scripts (CJK is ~1-2
# chars/token), which is why `embeddings.max_chars` can lower the budget.
CHARS_PER_TOKEN = 4


def max_input_chars(cfg, client=None) -> int:
    """Characters that safely fit the embedding model's context window.

    Sources, in order:
      1. the window the SERVER advertises for the configured model (LM Studio
         `/api/v0/models`, Ollama `/api/show`), read through
         ``client.embedding_window`` — so switching to another embedding model
         needs no configuration;
      2. ``embeddings.context_tokens`` (default 2048) when the server is silent
         or unreachable;
      3. ``embeddings.max_chars``, an explicit cap (token-dense scripts).

    Discovery can only make the budget SMALLER than the configured one, never
    silently larger: a smaller-window model must never inherit a chunk size
    that would be truncated.
    """
    configured = int(getattr(cfg, "embed_max_chars", 0) or 0)
    if configured <= 0:
        configured = (max(1, int(getattr(cfg, "embed_context_tokens", 2048) or 2048))
                      * CHARS_PER_TOKEN)
    tokens = None
    probe = getattr(client, "embedding_window", None)
    model = (getattr(cfg, "embed_model", "") or "").strip()
    if probe is not None and model:
        try:
            tokens = probe(model)
        except Exception:  # noqa: BLE001 - sizing must never break a pass
            tokens = None
    if tokens and int(tokens) > 0:
        configured = min(configured, int(tokens) * CHARS_PER_TOKEN)
    return max(64, configured)


def chunk_size(cfg, client=None) -> int:
    """The chunk size for page and note text: `extraction.chunk_chars`, capped
    by `max_input_chars` so a chunk cannot exceed the model's window."""
    size = None
    configured = getattr(cfg, "embed_chunk_chars", None)
    if callable(configured):
        try:
            size = int(configured())
        except Exception:  # noqa: BLE001 - a lightweight cfg (tests) may differ
            size = None
    if not size:
        size = int(getattr(cfg, "chunk_chars", 2000) or 2000)
    return max(64, min(size, max_input_chars(cfg, client)))

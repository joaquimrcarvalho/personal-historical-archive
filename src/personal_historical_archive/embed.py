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

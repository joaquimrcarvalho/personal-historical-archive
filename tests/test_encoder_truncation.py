"""Encoder output-cap handling: detect length, split the window, report losses."""
from __future__ import annotations

from types import SimpleNamespace

from personal_historical_archive.ingest import (
    _extract_encoder_window,
    _split_encoder_window,
)


class _FakeClient:
    """Return scripted (text, finish_reason) pairs and record the prompts."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def chat_text_ex(self, model, prompt, temperature, max_tokens, thinking=True):
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("unexpected extra model call")
        return self.replies.pop(0)


def _encoder(**kwargs):
    values = {
        "model": "m",
        "temperature": 0.0,
        "max_tokens": 8192,
        "thinking": True,
        "overlap_pages": 1,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def _chunk(n=4):
    return [(i, f"text {i}") for i in range(1, n + 1)]


def test_truncated_window_is_split_and_both_halves_are_used():
    client = _FakeClient([
        ('```json\n[{"a": 1}', "length"),
        ('[{"a": 1}]', "stop"),
        ('[{"b": 2}]', "stop"),
    ])
    records, lost = _extract_encoder_window(
        client, _encoder(), "BASE", {"filename": "d.pdf"}, _chunk(4), "", None, False)

    assert [r["a"] if "a" in r else r["b"] for r in records] == [1, 2]
    assert lost == []
    assert len(client.prompts) == 3  # one truncated window, no identical retry, then halves
    assert client.prompts[1] != client.prompts[0]


def test_single_page_that_hits_the_cap_is_reported_lost():
    client = _FakeClient([('{"partial": ', "length")])

    records, lost = _extract_encoder_window(
        client, _encoder(), "BASE", {"filename": "d.pdf"}, _chunk(1), "", None, False)

    assert records == []
    assert len(lost) == 1
    assert lost[0]["reason"].startswith("truncated")
    assert len(client.prompts) == 1  # no identical retry


def test_unparseable_reply_gets_one_differentiated_retry():
    client = _FakeClient([
        ("I could not do that.", "stop"),
        ('[{"a": 1}]', "stop"),
    ])

    records, lost = _extract_encoder_window(
        client, _encoder(), "BASE", {"filename": "d.pdf"}, _chunk(2), "", None, False)

    assert records == [{"a": 1}]
    assert lost == []
    assert len(client.prompts) == 2
    assert client.prompts[1] != client.prompts[0]


def test_empty_reply_gets_one_differentiated_retry():
    client = _FakeClient([("", "stop"), ('[{"a": 1}]', "stop")])

    records, lost = _extract_encoder_window(
        client, _encoder(), "BASE", {"filename": "d.pdf"}, _chunk(2), "", None, False)

    assert records == [{"a": 1}]
    assert lost == []


def test_split_window_keeps_overlap_and_always_shrinks():
    left, right = _split_encoder_window(_chunk(20), 6)
    assert len(left) < 20 and len(right) < 20
    assert len(left) + len(right) > 20  # the overlap is kept
    assert set(p for p, _ in left) & set(p for p, _ in right)  # overlap kept
    assert left[-1][0] >= right[0][0]  # no gap at the split point


def test_split_window_single_page_returns_none():
    assert _split_encoder_window(_chunk(1), 0) is None

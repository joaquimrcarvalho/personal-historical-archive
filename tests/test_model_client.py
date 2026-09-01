from __future__ import annotations

from personal_historical_archive.model_client import ModelClient, _clean_html_entities, _strip_think


def test_clean_html_entities():
    assert _clean_html_entities("a&nbsp;b&nbsp;c") == "a b c"
    assert _clean_html_entities("x &amp; y") == "x & y"
    assert _clean_html_entities("a &lt;b&gt; c") == "a <b> c"
    assert _clean_html_entities('&quot;hi&quot; &#39;x&#39;') == '"hi" \'x\''
    assert _clean_html_entities("plain text") == "plain text"
    assert _clean_html_entities("") == ""


def test_strip_think_cleans_entities():
    out = _strip_think("<think>reasoning</think>Result&nbsp;with&nbsp;spaces")
    assert out == "Result with spaces"


def test_strip_think_no_think_still_cleans():
    out = _strip_think("a&nbsp;b")
    assert out == "a b"


def test_embed_batches_large_input(monkeypatch):
    """embed() must split a large input into batch_size-sized /embeddings
    requests instead of one giant request (pha reindex can produce thousands
    of chunks, and endpoints reject an input array over their limit)."""
    client = ModelClient("http://example/v1")
    seen: list[list[str]] = []

    def fake_post(path: str, payload: dict):
        assert path == "/embeddings"
        batch = payload["input"]
        seen.append(list(batch))
        return {"data": [{"embedding": [0.0, 0.0]} for _ in batch]}

    monkeypatch.setattr(client, "_post", fake_post)
    texts = [f"chunk {i}" for i in range(5)]
    out = client.embed("m", texts, batch_size=2)
    # one request per slice, preserving input order and count
    assert [len(b) for b in seen] == [2, 2, 1]
    assert [t for b in seen for t in b] == texts
    assert len(out) == 5
    client.close()


def test_embed_single_request_when_no_batch_size(monkeypatch):
    """batch_size <= 0/None keeps the previous one-request-for-all behavior."""
    client = ModelClient("http://example/v1")
    seen: list[list[str]] = []

    def fake_post(path: str, payload: dict):
        batch = payload["input"]
        seen.append(list(batch))
        return {"data": [{"embedding": [0.0, 0.0]} for _ in batch]}

    monkeypatch.setattr(client, "_post", fake_post)
    out = client.embed("m", ["a", "b", "c"])
    assert [len(b) for b in seen] == [3]
    assert len(out) == 3
    client.close()

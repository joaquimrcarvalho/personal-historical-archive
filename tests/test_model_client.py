from __future__ import annotations

from pathlib import Path

import subprocess

import pytest

from personal_historical_archive.model_client import (
    ModelClient,
    ModelError,
    _clean_html_entities,
    _strip_think,
    run_liteparse,
    run_tesseract,
)


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


# --------------------------------------------------------------------------- tesseract engine

def test_run_tesseract_builds_command_and_returns_stdout(monkeypatch):
    """run_tesseract invokes the tesseract executable and returns stdout."""
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "SIGILLVM\n"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = run_tesseract("/tmp/page.jpg", lang="por+lat", psm=6)
    assert out == "SIGILLVM"
    assert calls[0][:4] == ["tesseract", "/tmp/page.jpg", "stdout", "-l"]
    assert calls[0][4] == "por+lat"
    assert calls[0][5:] == ["--psm", "6"]


def test_run_tesseract_defaults_to_no_lang_and_no_psm(monkeypatch):
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "text"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_tesseract("/tmp/page.jpg") == "text"
    assert calls[0] == ["tesseract", "/tmp/page.jpg", "stdout"]


def test_run_tesseract_raises_when_not_installed(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        raise FileNotFoundError("no tesseract")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ModelError, match="not installed"):
        run_tesseract("/tmp/page.jpg")


def test_run_tesseract_raises_on_nonzero_returncode(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "tesseract: unreadable image"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(ModelError, match="unreadable image"):
        run_tesseract("/tmp/page.jpg")


# --------------------------------------------------------------------------- liteparse engine

def test_run_liteparse_builds_command_and_returns_stdout(monkeypatch):
    """run_liteparse invokes `lit parse` with --ocr-language/--dpi and returns stdout."""
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "parsed text\n"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = run_liteparse("/tmp/page.jpg", lang="por", dpi=300)
    assert out == "parsed text"
    assert calls[0] == ["lit", "parse", "/tmp/page.jpg", "--ocr-language", "por", "--dpi", "300"]


def test_run_liteparse_defaults_to_no_lang_and_no_dpi(monkeypatch):
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "text"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_liteparse("/tmp/page.jpg") == "text"
    assert calls[0] == ["lit", "parse", "/tmp/page.jpg"]


def test_run_liteparse_raises_when_not_installed(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        raise FileNotFoundError("no lit")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ModelError, match="not installed"):
        run_liteparse("/tmp/page.jpg")


def test_run_liteparse_raises_on_nonzero_returncode(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "lit: failed to parse"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(ModelError, match="failed to parse"):
        run_liteparse("/tmp/page.jpg")

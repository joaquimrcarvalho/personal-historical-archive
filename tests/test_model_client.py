from __future__ import annotations

from personal_historical_archive.model_client import _clean_html_entities, _strip_think


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

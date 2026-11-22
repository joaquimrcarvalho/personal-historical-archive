"""Unit tests for the served viewer's small Markdown renderer."""
from __future__ import annotations

from personal_historical_archive.markdown_lite import render_markdown


def test_pipe_table_becomes_a_real_table():
    out = render_markdown(
        "| Nomen | Patria |\n"
        "|---|---|\n"
        "| Amaya | Lusitana |\n"
        "| Cabral | Lusitana |\n")
    assert '<table class="md-table">' in out
    assert "<th>Nomen</th>" in out and "<th>Patria</th>" in out
    assert "<td>Amaya</td>" in out and "<td>Lusitana</td>" in out
    assert out.count("<tr>") == 3          # header + two body rows
    assert "|---|" not in out


def test_short_table_rows_are_padded_and_long_ones_trimmed():
    out = render_markdown("| a | b |\n|---|---|\n| 1 |\n| 1 | 2 | 3 |\n")
    assert "<td>1</td><td></td>" in out
    assert "<td>1</td><td>2</td>" in out
    assert "3" not in out                  # extra cells would break the grid


def test_headings_lists_quotes_and_inline_markup():
    out = render_markdown(
        "## Notes\n\n"
        "- one\n- two\n\n"
        "1. first\n2. second\n\n"
        "> quoted\n\n"
        "**bold** *em* `code` ~~gone~~")
    assert "<h2>Notes</h2>" in out
    assert "<ul><li>one</li><li>two</li></ul>" in out
    assert "<ol><li>first</li><li>second</li></ol>" in out
    assert "<blockquote><p>quoted</p></blockquote>" in out
    assert "<strong>bold</strong>" in out
    assert "<em>em</em>" in out
    assert "<code>code</code>" in out
    assert "<del>gone</del>" in out


def test_code_spans_keep_emphasis_characters_literal():
    out = render_markdown("`**not bold**` and **bold**")
    assert "<code>**not bold**</code>" in out
    assert "<strong>bold</strong>" in out


def test_single_newlines_inside_a_paragraph_are_kept():
    out = render_markdown("Language: Latin\nScript: printed Roman\nPage: 1068")
    assert out == "<p>Language: Latin<br>Script: printed Roman<br>Page: 1068</p>"


def test_blank_lines_still_separate_paragraphs():
    out = render_markdown("one\n\ntwo")
    assert out == "<p>one</p><p>two</p>"


def test_html_is_always_escaped():
    out = render_markdown('<script>alert("x")</script> & <b>not markup</b>')
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; " in out
    assert "<script>" not in out
    assert "<b>" not in out


def test_dangerous_link_schemes_are_neutralised():
    out = render_markdown("[bad](javascript:alert(1)) [data](data:text/html,x)")
    assert "javascript:" not in out
    assert "data:text" not in out
    assert 'href="#"' in out


def test_safe_links_survive():
    out = render_markdown("[site](https://example.org/a?b=1&c=2) [mail](mailto:a@b.c)")
    assert 'href="https://example.org/a?b=1&amp;c=2"' in out
    assert 'href="mailto:a@b.c"' in out


def test_fenced_code_is_escaped():
    out = render_markdown("```\n<b>x</b>\n```")
    assert '<pre class="md-pre"><code>&lt;b&gt;x&lt;/b&gt;</code></pre>' in out


def test_footnote_reference_and_definition():
    out = render_markdown("text[^1]\n\n[^1]: the note\n")
    assert '<sup class="md-fn-ref"><a href="#fn-1">1</a></sup>' in out
    assert 'id="fn-1"' in out and "the note" in out

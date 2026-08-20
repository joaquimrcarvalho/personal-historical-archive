from __future__ import annotations

from types import SimpleNamespace

from personal_historical_archive.ingest import (
    _expand_records,
    _page_filter,
    _parse_json_array,
    _record_key,
    _record_similar,
    _regex_candidates,
    chunk_text,
)


def test_chunk_text_small():
    assert chunk_text("short text", 2000, 200) == ["short text"]


def test_chunk_text_empty():
    assert chunk_text("   ", 2000, 200) == []


def test_chunk_text_no_gaps_and_overlap():
    text = " ".join(f"word{n}" for n in range(300))
    chunks = chunk_text(text, 150, 40)
    assert len(chunks) > 1
    assert all(len(c) <= 150 for c in chunks)
    for w in text.split():
        assert any(w in c for c in chunks)
    for a, b in zip(chunks, chunks[1:]):
        assert b[0] in a


def test_parse_json_array_plain():
    assert _parse_json_array('[{"a": 1}]') == [{"a": 1}]


def test_parse_json_array_object_wrapper():
    assert _parse_json_array('{"records": [{"a": 1}]}') == [{"a": 1}]


def test_parse_json_array_nested_strings():
    assert _parse_json_array('[{"name": "a;b, [c]"}]') == [{"name": "a;b, [c]"}]


def test_parse_json_array_prose_prefix():
    assert _parse_json_array('here is the answer:\n[1, 2, 3]\nthat\'s all') == [1, 2, 3]


def test_parse_json_array_empty():
    assert _parse_json_array("no json here") == []


def test_expand_records_multi_class():
    parsed = [{
        "person": "Padre Mestre S. Francisco Xavier",
        "person_attributes": {"title": "Padre Mestre S.", "name": "Francisco Xavier"},
        "letter": "0 Padre ao ...",
        "letter_attributes": {"date": "1545-01-27", "place": "Cochim"},
    }]
    recs = _expand_records(parsed)
    assert {r["kind"] for r in recs} == {"person", "letter"}
    person = next(r for r in recs if r["kind"] == "person")
    assert person["name"] == "Francisco Xavier"
    letter = next(r for r in recs if r["kind"] == "letter")
    assert letter["place"] == "Cochim"


def test_expand_records_passthrough_plain():
    assert _expand_records([{"kind": "letter", "text": "x"}]) == [{"kind": "letter", "text": "x"}]


def test_record_key_normalizes():
    a = {"kind": "letter", "text": "Carta  ao  mosteiro"}
    b = {"kind": "letter", "text": "carta ao mosteiro"}
    assert _record_key(a) == _record_key(b)
    c = {"kind": "person", "text": "Carta ao mosteiro"}
    assert _record_key(a) != _record_key(c)


def test_record_similar_kind_gate():
    a = {"kind": "letter", "text": "x"}
    b = {"kind": "letter", "text": "x"}
    c = {"kind": "person", "text": "x"}
    assert _record_similar(a, b) == 1.0
    assert _record_similar(a, c) == 0.0


def test_page_filter():
    assert _page_filter(SimpleNamespace(pages="1-15")) == set(range(1, 16))
    assert _page_filter(SimpleNamespace(pages="1-15,40")) == set(range(1, 16)) | {40}
    assert _page_filter(SimpleNamespace(pages="all")) is None
    assert _page_filter(SimpleNamespace(pages="")) is None


def test_regex_candidates():
    enc = SimpleNamespace(
        candidate_pattern=r"^\s*[ivxlcdm]+\s*$",
        candidate_header=r"^[A-ZÀ-Ú]",
    )
    texts = [(1, "plain text"), (2, "xii\nD. João ao Padre"), (3, "more text")]
    assert _regex_candidates(texts, enc) == [2]

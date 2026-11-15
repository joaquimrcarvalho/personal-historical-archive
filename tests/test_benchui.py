"""`pha bench serve` — the workbench: rendering, save contract, HTTP surface."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml

from personal_historical_archive import bench, benchui

HEADER = """# Benchmark: {page} — {label}

Source: book.pdf (pha page 1)
Model: {label} via api (model `{model}`); pha palaeographer rules, editor pass OFF

## Transcription

```
{body}
```
"""

COMPARISON = """# 001 — Comparative readings (1. Model A · 2. Model B)

Context: two entries.

## Entry-by-entry comparison

```
[1] = all readings: P. Manuel Rodrigues, professo.

[2] 1. P. Juan Correa, Ministro
    2. P. Juan Correa, Praepositus
```

## Key differences on this page

- [2] role differs
"""


def write_reading(root: Path, reader: str, page: str, body: str) -> Path:
    d = root / reader
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{page}.md"
    p.write_text(HEADER.format(page=page, label=f"Model {reader}", model=f"m-{reader}",
                               body=body), encoding="utf-8")
    return p


def make_corpus(cfg, name="c", comparison=True):
    root = cfg.bench_root / "collections" / name
    root.mkdir(parents=True, exist_ok=True)
    for r in ("A", "B"):
        (root / r).mkdir(exist_ok=True)
    (root / "bench.yaml").write_text(yaml.safe_dump({
        "title": "Test corpus",
        "palaeographer": "rules",
        "document": {"path": ""},
        "readings": [{"dir": "A", "label": "Model A", "model": "m-A"},
                     {"dir": "B", "label": "Model B", "model": "m-B"}],
        "gold": {"reviewer": "Reviewer"},
    }, sort_keys=False), encoding="utf-8")
    write_reading(root, "A", "001", "1. one\n2. two")
    write_reading(root, "B", "001", "1. one\n2. TWO")
    if comparison:
        (root / "comparison").mkdir(exist_ok=True)
        (root / "comparison" / "001.md").write_text(COMPARISON, encoding="utf-8")
    return bench.load_corpus(root)


def rows_fields(wb, corpus, page="001"):
    data = wb.page_data(corpus, page)
    fields = {"c": str(corpus.root.relative_to(wb.root)), "p": page,
              "nums": ",".join(str(r["num"]) for r in data["rows"]),
              "base": data["base_sha"], "reviewer": "Reviewer"}
    for row in data["rows"]:
        fields[f"e{row['num']}"] = f"{row['text']} EDITED"
    return fields, data


# --------------------------------------------------------------------------- rendering


def test_index_and_corpus_render(cfg):
    corpus = make_corpus(cfg)
    (corpus.root / "images").mkdir()
    (corpus.root / "images" / "001.jpg").write_bytes(b"jpeg")
    wb = benchui.Workbench(cfg)

    index = benchui.render_index(wb)
    assert b"Test corpus" in index
    assert b"/corpus?c=" in index
    assert b"/img?c=" in benchui.render_corpus(wb, corpus)

    page = benchui.render_page(wb, corpus, "001")
    assert b'name="e1"' in page and b'name="e2"' in page
    assert b"data-put=\"2\"" in page
    assert b"Save gold" in page
    assert b"readings-data" in page


def test_render_page_escapes_model_text(cfg):
    corpus = make_corpus(cfg)
    corpus.comparison_path("001").write_text(
        COMPARISON.replace("P. Juan Correa, Ministro",
                           "<script>alert(1)</script>")
                  .replace("P. Juan Correa, Praepositus", "a & b"),
        encoding="utf-8")
    wb = benchui.Workbench(cfg)
    page = benchui.render_page(wb, corpus, "001").decode()
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "a &amp; b" in page


def test_page_without_comparison_offers_build(cfg):
    corpus = make_corpus(cfg, comparison=False)
    wb = benchui.Workbench(cfg)
    page = benchui.render_page(wb, corpus, "001").decode()
    assert "build comparison" in page
    assert 'name="e1"' not in page


def test_page_data_starts_from_merge_when_no_gold(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    data = wb.page_data(corpus, "001")
    assert data["rows"][0]["text"].startswith("P. Manuel Rodrigues")
    assert data["rows"][0]["source"] == "merge"
    assert data["base_sha"] == ""


# --------------------------------------------------------------------------- save contract


def test_save_writes_gold(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    fields, _ = rows_fields(wb, corpus)
    reviewer = benchui.save_gold(wb, corpus, "001", fields, wb.token)

    assert reviewer == "Reviewer"
    gold = corpus.gold_path("001").read_text(encoding="utf-8")
    meta, body_text = bench.split_front_matter(gold)
    assert meta["status"] == bench.STATUS_GOLD
    assert meta["reviewed_by"] == "Reviewer"
    assert meta["readings_hash"] == corpus.readings_hash("001")
    assert "## Corrected text" in body_text
    assert "[1] P. Manuel Rodrigues, professo. EDITED" in body_text
    assert "[2] " in body_text and "EDITED" in body_text
    assert bench.verify_gold_text(gold, corpus.readings_hash("001"))[0] == []
    assert corpus.is_gold("001")
    assert not corpus.is_stale("001")

    # the review log records the save, without the text
    log = (corpus.root / "review-log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(log[-1])
    assert entry["reviewer"] == "Reviewer" and entry["entries"] == 2
    assert "text" not in entry


def test_save_refuses_stale_base(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    fields, _ = rows_fields(wb, corpus)
    benchui.save_gold(wb, corpus, "001", fields, wb.token)
    fields["base"] = ""            # the form was loaded before the first save
    with pytest.raises(benchui.SaveError) as exc:
        benchui.save_gold(wb, corpus, "001", fields, wb.token)
    assert exc.value.status == 409
    assert "changed on disk" in str(exc.value)


def test_save_refuses_missing_reviewer_empty_entry_and_bad_token(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    fields, _ = rows_fields(wb, corpus)
    fields["reviewer"] = ""
    corpus.gold_reviewer = ""
    with pytest.raises(benchui.SaveError) as exc:
        benchui.save_gold(wb, corpus, "001", fields, wb.token)
    assert exc.value.status == 400
    assert "reviewer" in str(exc.value)

    fields, _ = rows_fields(wb, corpus)
    fields["e2"] = ""
    with pytest.raises(benchui.SaveError) as exc:
        benchui.save_gold(wb, corpus, "001", fields, wb.token)
    assert "[2] is empty" in str(exc.value)

    fields, _ = rows_fields(wb, corpus)
    with pytest.raises(benchui.SaveError) as exc:
        benchui.save_gold(wb, corpus, "001", fields, "not-the-token")
    assert exc.value.status == 409


def test_save_preserves_rules_and_notes(cfg):
    corpus = make_corpus(cfg)
    gold = corpus.gold_path("001")
    gold.parent.mkdir(parents=True, exist_ok=True)
    gold.write_text("""---
page: "001"
status: human-corrected
reviewed_by: Earlier
reviewed_at: 2026-01-01
editing_rules:
- "u -> v"
readings_hash: sha256:old
---

# 001
Context: c

## Corrected text

```
[1] old
[2] old
```

## Notes

An older note.
""", encoding="utf-8")
    wb = benchui.Workbench(cfg)
    fields, data = rows_fields(wb, corpus)
    assert data["base_sha"]
    benchui.save_gold(wb, corpus, "001", fields, wb.token)
    text = gold.read_text(encoding="utf-8")
    assert "u -> v" in text                      # editing rules survive
    assert "An older note." not in text          # the note is regenerated
    assert "EDITED" in text


def test_save_refuses_non_contiguous_entries(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    fields, _ = rows_fields(wb, corpus)
    fields["nums"] = "1,3"
    with pytest.raises(benchui.SaveError):
        benchui.save_gold(wb, corpus, "001", fields, wb.token)


# --------------------------------------------------------------------------- images


def test_image_resolution(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    assert benchui.image_path(wb, corpus, "001") is None

    (corpus.root / "images").mkdir()
    img = corpus.root / "images" / "001.jpg"
    img.write_bytes(b"jpeg")
    assert benchui.image_path(wb, corpus, "001") == img

    # a directory-of-images document is read in place
    (corpus.root / "images" / "001.jpg").unlink()
    src = cfg.data / "dropbox" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "001.jpg").write_bytes(b"jpeg2")
    corpus.document = "dropbox/src"
    assert benchui.image_path(wb, corpus, "001") == src / "001.jpg"


# --------------------------------------------------------------------------- http


def test_http_roundtrip(cfg):
    corpus = make_corpus(cfg)
    (corpus.root / "images").mkdir()
    (corpus.root / "images" / "001.jpg").write_bytes(b"jpeg")
    server = benchui.make_server(cfg, port=0, quiet=True)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    try:
        assert urllib.request.urlopen(base + "/").read().find(b"Test corpus") >= 0
        rel = str(corpus.root.relative_to(cfg.bench_root))
        q = urllib.parse.urlencode({"c": rel, "p": "001"})
        page = urllib.request.urlopen(base + "/page?" + q).read()
        assert b'name="e1"' in page
        img = urllib.request.urlopen(base + "/img?" + q).read()
        assert img == b"jpeg"

        wb = server.wb
        fields, _ = rows_fields(wb, corpus)
        data = urllib.parse.urlencode({**fields, "token": wb.token}).encode()
        req = urllib.request.Request(base + "/save", data=data, method="POST")
        saved = urllib.request.urlopen(req).read()
        assert b"Saved. The gold page is written and verified." in saved
        assert corpus.is_gold("001")

        # a save with no token is refused
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(base + "/save", data=data, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 409
    finally:
        server.shutdown()
        server.server_close()


def test_pending_page_starts_from_reference_and_says_so(cfg):
    corpus = make_corpus(cfg)
    corpus.editing_rules = ["u -> v"]
    gold = corpus.gold_path("001")
    gold.parent.mkdir(parents=True, exist_ok=True)
    gold.write_text("---\npage: '001'\nstatus: pending\nreadings_hash: ''\n---\n\n"
                    "## Reference text\n\n```\n[1] OLD COPY\n[2] OLD\n```\n",
                    encoding="utf-8")
    ref = corpus.reference_path("001")
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("---\npage: '001'\nstatus: machine-merged\n---\n\n"
                   "## Reference text (variants in slashes)\n\n```\n"
                   "[1] REF ONE\n[2] REF TWO\n```\n", encoding="utf-8")
    wb = benchui.Workbench(cfg)
    data = wb.page_data(corpus, "001")
    assert data["pending_copy"] is True and data["is_gold"] is False
    assert [r["text"] for r in data["rows"]] == ["REF ONE", "REF TWO"]
    assert {r["source"] for r in data["rows"]} == {"reference"}
    page = benchui.render_page(wb, corpus, "001").decode()
    assert "pending copy" in page

    fields, _ = rows_fields(wb, corpus)
    benchui.save_gold(wb, corpus, "001", fields, wb.token)
    meta, _ = bench.split_front_matter(corpus.gold_path("001").read_text())
    assert meta["status"] == bench.STATUS_GOLD
    assert meta["editing_rules"] == ["u -> v"]


def test_flagged_entries_and_their_count(cfg):
    corpus = make_corpus(cfg)
    corpus.comparison_path("001").write_text(
        "# 001 — Comparative readings (1. Model A · 2. Model B)\n\n"
        "Context: flags.\n\n## Entry-by-entry comparison\n\n```\n"
        "[1] 1. P. Juan Correa, Ministro 1568\n"
        "    2. P. Juan Correa, Ministro 1569\n\n"
        "[2] 1. P. Manuel Rodrigues\n"
        "    2. P. Manuel Rodriguez\n\n"
        "[3] 1. P. Luis [?]\n"
        "    2. P. Luis\n"
        "```\n\n## Key differences on this page\n\n- flags\n", encoding="utf-8")
    wb = benchui.Workbench(cfg)
    data = wb.page_data(corpus, "001")
    flags = [r["flags"] for r in data["rows"]]
    assert flags == [["number"], ["name"], ["illegible"]]
    assert data["flagged"] == 3
    page = benchui.render_page(wb, corpus, "001").decode()
    assert "check image: number" in page
    assert "check image: name" in page
    assert "check image: illegible" in page
    assert "3 of 3 entries are flagged" in page


def test_bench_css_is_light_and_scopes_the_shared_form_rule():
    """Regression: a global form rule from the shared viewer laid the entries
    out in one row, and dark-mode text landed on the hardcoded white cards."""
    from personal_historical_archive.viewer import CSS as shared_css

    import re

    assert ".inline-form" in shared_css
    assert not re.search(r"(^|\})\s*form\{display:inline-flex", shared_css)
    assert ":root{color-scheme:light}" in benchui.BENCH_CSS
    assert ".bench-main form{display:block" in benchui.BENCH_CSS
    for rule in ("body{background:#faf9f7;color:#1b1b1b}",
                 ".reading-line .txt{color:#1b1b1b}",
                 ".entry textarea,.savebar input[type=text]{background:#fff;color:#1b1b1b}"):
        assert rule in benchui.BENCH_CSS, rule


def test_workbench_has_a_draggable_divider(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    page = benchui.render_page(wb, corpus, "001").decode()
    assert 'class="divider"' in page
    assert 'role="separator"' in page and 'aria-orientation="vertical"' in page
    assert 'class="editpane"' in page
    assert "pha-bench-media" in page          # the persisted width key
    assert "--media-w" in benchui.BENCH_CSS
    assert ".divider{" in benchui.BENCH_CSS
    # the image pane fills the pane width and scrolls on its own
    assert "max-height:calc(100vh" in benchui.BENCH_CSS
    assert "overflow:auto" in benchui.BENCH_CSS
    assert "scrollbar-gutter:stable" in benchui.BENCH_CSS
    # the workbench uses the full window width (no centred 1400px column)
    assert "max-width:1400px" not in benchui.BENCH_CSS
    assert ".bench-main{display:block;padding:.75rem 1rem;max-width:none" in benchui.BENCH_CSS
    # the readings stack vertically: one entry block per comparison entry
    assert page.count('class="entry"') == 2


def test_save_draft_allows_a_partial_page_and_is_not_gold(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    fields, _ = rows_fields(wb, corpus)
    fields["e2"] = ""                       # stopped mid-page
    benchui.save_gold(wb, corpus, "001", fields, wb.token,
                      status=bench.STATUS_DRAFT)
    meta, _ = bench.split_front_matter(corpus.gold_path("001").read_text())
    assert meta["status"] == bench.STATUS_DRAFT
    assert corpus.is_draft("001") and not corpus.is_gold("001")
    assert bench.score_page(corpus, "001")["reason"].startswith("draft")
    assert "Draft saved by" in corpus.gold_path("001").read_text()

    data = wb.page_data(corpus, "001")
    assert data["is_draft"] is True and data["pending_copy"] is False
    assert data["rows"][1]["text"] == ""
    assert data["rows"][0]["source"] == "draft"
    page = benchui.render_page(wb, corpus, "001").decode()
    assert "saved part-way" in page and "Save draft" in page

    # resume: the draft is the starting point, and Save gold completes it
    fields, _ = rows_fields(wb, corpus)
    fields["e2"] = "P. Juan Correa, Ministro"
    benchui.save_gold(wb, corpus, "001", fields, wb.token)
    assert corpus.is_gold("001") and not corpus.is_draft("001")
    assert corpus.gold_meta("001")["status"] == bench.STATUS_GOLD


def test_gold_page_warns_before_demotion_to_draft(cfg):
    corpus = make_corpus(cfg)
    write_gold = corpus.gold_path("001")
    write_gold.parent.mkdir(parents=True, exist_ok=True)
    write_gold.write_text("---\npage: '001'\nstatus: human-corrected\n"
                          "reviewed_by: R\nreadings_hash: ''\n---\n\n"
                          "## Corrected text\n\n```\n[1] a\n[2] b\n```\n",
                          encoding="utf-8")
    wb = benchui.Workbench(cfg)
    page = benchui.render_page(wb, corpus, "001").decode()
    assert "already validated as gold" in page


def test_rule_explanations_are_shown_open_on_the_page(cfg):
    corpus = make_corpus(cfg)
    corpus.editing_rules = ["u -> v"]
    corpus.editing_rule_notes = [{"rule": "u -> v",
                                  "explain": "write v when it is the consonant",
                                  "example": "votos, but Manuel"}]
    wb = benchui.Workbench(cfg)
    page = benchui.render_page(wb, corpus, "001").decode()
    assert "editing rules — how to apply them" in page
    assert "write v when it is the consonant" in page
    assert "votos, but Manuel" in page
    assert "<details open" in page


def test_save_status_aliases_are_accepted(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    fields, _ = rows_fields(wb, corpus)
    benchui.save_gold(wb, corpus, "001", fields, wb.token, status="gold")
    assert corpus.is_gold("001")


def test_http_save_gold_accepts_the_form_status(cfg):
    corpus = make_corpus(cfg)
    server = benchui.make_server(cfg, port=0, quiet=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        wb = server.wb
        fields, _ = rows_fields(wb, corpus)
        payload = urllib.parse.urlencode(
            {**fields, "status": "human-corrected", "token": wb.token}).encode()
        req = urllib.request.Request(base + "/save", data=payload, method="POST")
        page = urllib.request.urlopen(req).read()
        assert b"Saved. The gold page is written and verified." in page
        assert corpus.is_gold("001")
        rel = str(corpus.root.relative_to(cfg.bench_root))
        check = urllib.request.urlopen(
            base + "/page?" + urllib.parse.urlencode({"c": rel, "p": "001"})).read()
        assert b'class="badge2 gold">gold' in check
    finally:
        server.shutdown()
        server.server_close()


def test_corpus_page_has_a_score_button_and_it_scores(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    page = benchui.render_corpus(wb, corpus).decode()
    assert 'value="score"' in page and "score now" in page

    server = benchui.make_server(cfg, port=0, quiet=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wb = server.wb
        rel = str(corpus.root.relative_to(cfg.bench_root))
        payload = urllib.parse.urlencode(
            {"c": rel, "what": "score", "token": wb.token}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/build", data=payload, method="POST")
        urllib.request.urlopen(req).read()
        assert (corpus.root / "scores" / "results.json").is_file()
    finally:
        server.shutdown()
        server.server_close()


def test_each_entry_has_a_review_checkbox(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    page = benchui.render_page(wb, corpus, "001").decode()
    assert 'data-done="1"' in page and 'data-done="2"' in page
    assert 'id="donecount"' in page
    assert 'autocomplete="off"' in page
    assert "pageshow" in page
    assert "localStorage" in page                  # ticks persist between sessions
    assert "data-review-key=" in page and "data-saved=" in page
    assert '<input type="checkbox" data-done="1" checked' not in page


def test_http_save_draft_keeps_ticks_message(cfg):
    corpus = make_corpus(cfg)
    server = benchui.make_server(cfg, port=0, quiet=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wb = server.wb
        fields, _ = rows_fields(wb, corpus)
        payload = urllib.parse.urlencode(
            {**fields, "status": "draft", "token": wb.token}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/save", data=payload, method="POST")
        page = urllib.request.urlopen(req).read()
        assert "Draft saved — your ticks are kept".encode() in page
        assert corpus.is_draft("001")
    finally:
        server.shutdown()
        server.server_close()


def test_review_sidecar_roundtrip_and_stale_hash(cfg):
    corpus = make_corpus(cfg)
    benchui.write_review(corpus, "001", [2, 1, 2])
    assert benchui.read_review(corpus, "001") == [1, 2]
    path = benchui.review_path(corpus, "001")
    data = json.loads(path.read_text())
    assert data["readings_hash"] == corpus.readings_hash("001")
    assert data["reviewed"] == [1, 2]

    data["readings_hash"] = "sha256:old"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert benchui.read_review(corpus, "001") == []


def test_http_review_endpoint_and_page_embedding(cfg):
    corpus = make_corpus(cfg)
    server = benchui.make_server(cfg, port=0, quiet=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wb = server.wb
        rel = str(corpus.root.relative_to(cfg.bench_root))
        payload = urllib.parse.urlencode({
            "c": rel, "p": "001", "token": wb.token,
            "reviewed": json.dumps([2, 1])}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/review", data=payload, method="POST")
        assert urllib.request.urlopen(req).read() == b'{"ok":true}'
        assert benchui.read_review(corpus, "001") == [1, 2]
        page = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/page?" + urllib.parse.urlencode(
                {"c": rel, "p": "001"})).read()
        assert b'data-reviewed="1,2"' in page
    finally:
        server.shutdown()
        server.server_close()


def test_save_gold_clears_sidecar_but_draft_keeps_it(cfg):
    corpus = make_corpus(cfg)
    wb = benchui.Workbench(cfg)
    benchui.write_review(corpus, "001", [1])
    fields, _ = rows_fields(wb, corpus)
    benchui.save_gold(wb, corpus, "001", fields, wb.token,
                      status=bench.STATUS_DRAFT)
    assert benchui.review_path(corpus, "001").is_file()

    benchui.write_review(corpus, "001", [1, 2])
    fields, _ = rows_fields(wb, corpus)
    benchui.save_gold(wb, corpus, "001", fields, wb.token)
    assert not benchui.review_path(corpus, "001").exists()

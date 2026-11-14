"""`pha bench` — corpus parsing, status, verification, comparison, reference."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from personal_historical_archive import bench, cli


# --------------------------------------------------------------------------- helpers

READING_HEADER = """# Benchmark: {page} — {label}

Source: book.pdf (pha page {n})
Model: {label} via {access} (model `{model}`); pha palaeographer {rules}, editor pass OFF
Palaeographer rules: palaeographers/{rules}.md

## Transcription

```
{body}
```
"""


def write_reading(root: Path, reader: str, page: str, body: str, *,
                  label: str = "Model", model: str = "m", access: str = "api",
                  rules: str = "rules") -> Path:
    d = root / reader
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{page}.md"
    p.write_text(READING_HEADER.format(page=page, label=label, n=1, access=access,
                                       model=model, rules=rules, body=body),
                 encoding="utf-8")
    return p


def make_corpus(cfg, name: str = "c", readers=("A", "B")) -> bench.Corpus:
    root = cfg.bench_root / "collections" / name
    root.mkdir(parents=True, exist_ok=True)
    for r in readers:
        (root / r).mkdir(exist_ok=True)
    yml = root / "bench.yaml"
    data = {
        "title": "Test corpus",
        "palaeographer": "rules",
        "document": {"path": "dropbox/book.pdf", "pages": {"1": "001"}},
        "images": {"mode": "render"},
        "readings": [{"dir": r, "label": f"Model {r}", "model": f"model-{r}",
                      "access": "api"} for r in readers],
        "gold": {"reviewer": "Reviewer"},
    }
    yml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return bench.load_corpus(root)


COMPARISON_2 = """# 001 — Comparative readings (1. Model A · 2. Model B)

Context: one page.

## Entry-by-entry comparison

```
[1] = all readings: P. Manuel Rodrigues, professo.

[2] 1. P. Juan Correa, Ministro
    2. P. Juan Correa, Praepositus

[3] = all readings: P. Luis de Vasconcellos.
```

## Key differences on this page

- [2] role differs
"""

GOLD_OK = """---
page: "001"
status: human-corrected
reviewed_by: Reviewer
reviewed_at: 2026-11-14
readings: [A, B]
readings_hash: {hash}
---

# 001 — Reference text

Context: one page.

## Corrected text

```
[1] P. Manuel Rodrigues, professo.
[2] P. Juan Correa, Ministro
[3] P. Luis de Vasconcellos.
```

## Notes
Reviewed.
"""


# --------------------------------------------------------------------------- parsing


def test_extract_transcription_shapes():
    driver = READING_HEADER.format(page="001", label="M", n=1, access="api",
                                   model="m", rules="r", body="line one\nline two")
    assert bench.extract_transcription(driver) == "line one\nline two"

    library = """---
source: x.pdf
page: 2
---

## Transcription

raw body
## Notes

notes here
"""
    assert bench.extract_transcription(library) == "raw body"

    bare = "# Title\n\nbody before notes\n\n## Notes\n\nn\n"
    assert bench.extract_transcription(bare) == "body before notes"

    gold = "---\npage: 1\n---\n\n## Corrected text\n\n```\nfixed text\n```\n"
    assert bench.extract_transcription(gold) == "fixed text"

    ref = "## Reference text (variants in slashes)\n\n```\n[1] a/b\n```\n"
    assert bench.extract_transcription(ref) == "[1] a/b"


def test_error_block_and_empty_are_not_readings():
    assert not bench.is_valid_reading("")
    assert not bench.is_valid_reading("ERROR: model call failed")
    assert not bench.is_valid_reading("   ERROR:  finish_reason length")
    assert bench.is_valid_reading("P. Manuel.")


def test_parse_comparison_entries_and_counts():
    entries = bench.parse_comparison_entries(COMPARISON_2)
    assert [e.num for e in entries] == [1, 2, 3]
    assert entries[0].kind == "all" and entries[0].text.startswith("P. Manuel")
    assert entries[1].kind == "diff"
    assert len(entries[1].readings) == 2
    assert entries[1].readings[1].endswith("Praepositus")
    assert bench.reading_counts(COMPARISON_2) == {1: 0, 2: 2, 3: 0}

    # a continuation line that begins "3." is not a third reading
    text = """## Entry-by-entry comparison

```
[1] 1. P. Juan
    3. Nouicos.
    2. P[adr]e Juan
```

## Key differences on this page
"""
    e = bench.parse_comparison_entries(text)[0]
    assert len(e.readings) == 2
    assert "Nouicos." in e.readings[0]


# --------------------------------------------------------------------------- status


def test_status_and_staleness(cfg):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "one")
    write_reading(corpus.root, "B", "001", "two")
    st = bench.corpus_status(corpus)
    assert st["pages"] == 1
    assert st["readings_complete"] == 1
    assert st["compared"] == 0 and st["gold"] == 0 and st["stale"] == 0

    corpus.comparison_path("001").parent.mkdir(parents=True, exist_ok=True)
    corpus.comparison_path("001").write_text(COMPARISON_2, encoding="utf-8")
    st = bench.corpus_status(corpus)
    assert st["compared"] == 1

    h = corpus.readings_hash("001")
    corpus.gold_path("001").parent.mkdir(parents=True, exist_ok=True)
    corpus.gold_path("001").write_text(GOLD_OK.format(hash=h), encoding="utf-8")
    st = bench.corpus_status(corpus)
    assert st["gold"] == 1 and st["stale"] == 0

    write_reading(corpus.root, "A", "001", "one, changed")
    st = bench.corpus_status(corpus)
    assert st["gold"] == 1 and st["stale"] == 1


def test_missing_reading_listed(cfg):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "one")
    st = bench.corpus_status(corpus)
    assert st["page_status"][0]["missing"] == ["B"]
    assert st["readings_complete"] == 0


# --------------------------------------------------------------------------- verify


def test_verify_comparison_text():
    assert bench.verify_comparison_text(COMPARISON_2, readings=2) == []

    two_fences_missing = COMPARISON_2.replace("```\n", "", 1)
    assert any("fence" in p for p in bench.verify_comparison_text(two_fences_missing))

    wrong_count = COMPARISON_2.replace(
        "[1] = all readings: P. Manuel Rodrigues, professo.",
        "[1] 1. P. Manuel Rodrigues, professo.")
    assert any("reading count" in p for p in
               bench.verify_comparison_text(wrong_count, readings=2))

    assert any("continuous" in p for p in bench.verify_comparison_text(
        COMPARISON_2.replace("[2]", "[5]"), readings=2))


def test_verify_gold():
    h = "sha256:abc"
    probs, warns = bench.verify_gold_text(GOLD_OK.format(hash=h), h, [1, 2, 3])
    assert probs == []
    assert warns == []

    no_meta = GOLD_OK.format(hash=h).split("---", 2)[2]
    probs, warns = bench.verify_gold_text(no_meta, None, None)
    assert any("front matter" in w for w in warns)
    assert probs == []

    stale, _ = bench.verify_gold_text(GOLD_OK.format(hash="sha256:old"),
                                      "sha256:new", [1, 2, 3])
    assert any("STALE" in p for p in stale)

    stacked = GOLD_OK.format(hash=h).replace(
        "[2] P. Juan Correa, Ministro",
        "[2] 1. P. Juan Correa, Ministro\n    2. P. Juan Correa, Praepositus")
    probs, _ = bench.verify_gold_text(stacked, h, [1, 2, 3])
    assert any("stacked" in p for p in probs)

    # a deliberate split/merge changes the entry count: a note, not a failure
    probs, warns = bench.verify_gold_text(GOLD_OK.format(hash=h), h, [1, 2])
    assert probs == []
    assert any("entries" in w for w in warns)


def test_verify_corpus_end_to_end(cfg):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "1. one\n2. two\n3. three")
    write_reading(corpus.root, "B", "001", "1. one\n2. two\n3. THREE")
    corpus.comparison_path("001").parent.mkdir(parents=True, exist_ok=True)
    corpus.comparison_path("001").write_text(COMPARISON_2, encoding="utf-8")
    h = corpus.readings_hash("001")
    corpus.gold_path("001").parent.mkdir(parents=True, exist_ok=True)
    corpus.gold_path("001").write_text(GOLD_OK.format(hash=h), encoding="utf-8")

    reports = bench.verify_corpus(corpus, readings=2)
    assert all(r.ok for r in reports), [r.problems for r in reports]
    # images/ is absent: warning, not a failure
    assert any("no images/" in w for r in reports for w in r.warnings)
    assert any(not r.ok for r in bench.verify_corpus(corpus, readings=2,
                                                    require_images=True))


# --------------------------------------------------------------------------- compare + reference


def test_build_comparison_tabular_and_reference(cfg):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001",
                  "1. P. Manuel Rodrigues, professo\n2. P. Luis, companheiro\n"
                  "3. P. Juan Correa, Ministro")
    write_reading(corpus.root, "B", "001",
                  "1. P. Manuel Rodrigues, professo\n2. P. Luis, companheiro\n"
                  "3. P. Juan Correa, Praepositus")

    res = bench.build_comparison(corpus, profile="auto")
    assert res["pages"] == 1 and res["stats"][0]["profile"] == "tabular"
    text = corpus.comparison_path("001").read_text(encoding="utf-8")
    assert "= all readings: P. Manuel Rodrigues, professo" in text
    assert "1. P. Juan Correa, Ministro" in text
    assert "2. P. Juan Correa, Praepositus" in text
    assert bench.verify_comparison_text(text, readings=2) == []

    res = bench.build_reference(corpus)
    ref = corpus.reference_path("001").read_text(encoding="utf-8")
    meta, body = bench.split_front_matter(ref)
    assert meta["status"] == bench.STATUS_MERGED
    assert meta["readings"] == ["A", "B"]
    assert meta["readings_hash"].startswith("sha256:")
    assert "[3] P. Juan Correa, Ministro/Praepositus" in body
    assert "[1] P. Manuel Rodrigues, professo" in body
    assert res["stats"][0]["divergent"] is False


def test_build_comparison_lines_profile(cfg):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "alpha beta\ngamma delta")
    write_reading(corpus.root, "B", "001", "alpha beta\ngamma epsilon")
    res = bench.build_comparison(corpus, profile="lines")
    text = corpus.comparison_path("001").read_text(encoding="utf-8")
    assert res["stats"][0]["profile"] == "lines"
    assert "= all readings: alpha beta" in text
    assert "1. gamma delta" in text and "2. gamma epsilon" in text


def test_build_comparison_divergent_readings(cfg):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "1. one\n2. two\n3. three")
    write_reading(corpus.root, "B", "001", "[blank page]")
    bench.build_comparison(corpus, profile="auto")
    text = corpus.comparison_path("001").read_text(encoding="utf-8")
    assert "[1] 1. one" in text            # row numbering is stripped in the full dump
    assert "[2] 1. [blank page]" in text
    bench.build_reference(corpus)
    ref = corpus.reference_path("001").read_text(encoding="utf-8")
    assert "diverge fundamentally" in ref


def test_reference_refuses_more_readings_than_declared(cfg):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "x")
    write_reading(corpus.root, "B", "001", "y")
    corpus.comparison_path("001").parent.mkdir(parents=True, exist_ok=True)
    three = COMPARISON_2.replace(
        "[2] 1. P. Juan Correa, Ministro\n    2. P. Juan Correa, Praepositus",
        "[2] 1. a\n    2. b\n    3. c")
    corpus.comparison_path("001").write_text(three, encoding="utf-8")
    with pytest.raises(SystemExit):
        bench.build_reference(corpus, readings=2)
    assert not corpus.reference_path("001").exists()


# --------------------------------------------------------------------------- init / resolve


def test_init_infers_readers_and_models(cfg):
    root = cfg.bench_root / "collections" / "fresh"
    write_reading(root, "M3", "001", "body", label="MiniMax M3",
                  model="minimax-m3", access="direct MiniMax API", rules="r1")
    write_reading(root, "QV", "001", "body", label="Qwen3-VL", model="qwen3-vl",
                  access="OpenRouter", rules="r1")
    corpus = bench.init_corpus(root)
    assert corpus.configured
    assert [r.dir for r in corpus.readers] == ["M3", "QV"]
    assert corpus.readers[0].model == "minimax-m3"
    assert corpus.readers[0].label == "MiniMax M3"
    assert corpus.readers[0].access == "direct MiniMax API"
    assert corpus.palaeographer == "r1"

    reloaded = bench.load_corpus(root)
    assert [r.model for r in reloaded.readers] == ["minimax-m3", "qwen3-vl"]
    with pytest.raises(SystemExit):
        bench.init_corpus(root)          # refuses to overwrite


def test_resolve_corpus_and_bench_root(cfg):
    corpus = make_corpus(cfg, name="c")
    assert bench.resolve_corpus(cfg, "c").root == corpus.root
    assert bench.resolve_corpus(cfg, "collections/c").root == corpus.root
    assert bench.resolve_corpus(cfg, str(corpus.root)).root == corpus.root
    assert cfg.bench_root == cfg.data / "benchmarks"
    with pytest.raises(SystemExit):
        bench.resolve_corpus(cfg, "nope")


def test_history_layout_found(cfg):
    """<volume>/palaeographers/ is the historical corpus root (1577, ANTT)."""
    volume = cfg.bench_root / "collections" / "vol"
    pal = volume / "palaeographers"
    write_reading(pal, "A", "001", "x")
    assert bench._corpus_dir(volume) == pal
    assert bench.resolve_corpus(cfg, "vol").root == pal


# --------------------------------------------------------------------------- harvest planning


def test_pha_page_for_and_harvest_dry_run(cfg):
    corpus = make_corpus(cfg)
    doc = cfg.data / "dropbox" / "book.pdf"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(b"%PDF")
    assert bench.pha_page_for(corpus, "001", cfg.data) == 1
    corpus.document_pages = {7: "507v"}
    assert bench.pha_page_for(corpus, "507v", cfg.data) == 7

    images = cfg.data / "dropbox" / "imgs"
    images.mkdir(parents=True, exist_ok=True)
    for n in ("001", "002", "002v"):
        (images / f"{n}.jpg").write_bytes(b"x")
    corpus.document = "dropbox/imgs"
    assert bench.pha_page_for(corpus, "002v", cfg.data) == 3

    corpus.document = "dropbox/book.pdf"
    res = bench.harvest(corpus, cfg, pages=["001"], dry_run=True)
    assert res["planned"] == 2          # two readers, one page


def test_harvest_without_document_errors(cfg):
    corpus = make_corpus(cfg)
    corpus.document = ""
    with pytest.raises(SystemExit):
        bench.harvest(corpus, cfg, dry_run=True)


# --------------------------------------------------------------------------- CLI


def test_cli_bench_list_and_status(cfg, monkeypatch, capsys):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "one")
    write_reading(corpus.root, "B", "001", "two")
    monkeypatch.setenv("PHA_HOME", str(cfg.root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)

    cli.main(["bench", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["corpora"][0]["pages"] == 1
    assert out["corpora"][0]["readers"][0]["dir"] == "A"

    cli.main(["bench", "status", str(corpus.root), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["readings_complete"] == 1
    assert out["page_status"][0]["present"] == ["A", "B"]

    cli.main(["bench", "status", str(corpus.root), "--all"])
    text = capsys.readouterr().out
    assert "001" in text and "readings complete: 1" in text


def test_cli_bench_verify_exit_code(cfg, monkeypatch, capsys):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "1. one")
    write_reading(corpus.root, "B", "001", "1. one")
    corpus.comparison_path("001").parent.mkdir(parents=True, exist_ok=True)
    corpus.comparison_path("001").write_text(
        COMPARISON_2.replace("```\n", "", 1), encoding="utf-8")
    monkeypatch.setenv("PHA_HOME", str(cfg.root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["bench", "verify", str(corpus.root), "--no-gold"])
    assert exc.value.code == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_bench_page_json(cfg, monkeypatch, capsys):
    corpus = make_corpus(cfg)
    write_reading(corpus.root, "A", "001", "one")
    write_reading(corpus.root, "B", "001", "two")
    monkeypatch.setenv("PHA_HOME", str(cfg.root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)
    cli.main(["bench", "page", str(corpus.root), "001", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["page"] == "001"
    assert out["readings"][0]["text"] == "one"
    assert out["readings_hash"].startswith("sha256:")

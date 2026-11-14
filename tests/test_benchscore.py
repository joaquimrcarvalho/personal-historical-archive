"""P4 scoring and P5 export."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from personal_historical_archive import bench, benchexport, benchui

HEADER = """# Benchmark: {page} — Model {r}

Source: book.pdf (pha page 1)
Model: Model {r} via api (model `m-{r}`); pha palaeographer rules, editor pass OFF

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

GOLD_BODY = """# 001 — Corrected text

Context: two entries.

## Corrected text

```
[1] P. Manuel Rodrigues, professo.
[2] P. Juan Correa, Ministro
```

## Notes

Reviewed.
"""


def make_corpus(cfg, name="c"):
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
    (root / "A" / "001.md").write_text(HEADER.format(
        page="001", r="A", body="P. Manuel Rodrigues, professo.\n"
                                "P. Juan Correa, Ministro"), encoding="utf-8")
    (root / "B" / "001.md").write_text(HEADER.format(
        page="001", r="B", body="P. Manuel Rodrigues, professo.\n"
                                "P. Juan Correa, Praepositus"), encoding="utf-8")
    (root / "comparison").mkdir(exist_ok=True)
    (root / "comparison" / "001.md").write_text(COMPARISON, encoding="utf-8")
    return bench.load_corpus(root)


def write_gold(corpus, page="001", status=bench.STATUS_GOLD, hash_=None):
    meta = {
        "page": page,
        "status": status,
        "reviewed_by": "Reviewer",
        "reviewed_at": "2026-11-14",
        "readings": [r.dir for r in corpus.readers],
        "readings_hash": hash_ if hash_ is not None else corpus.readings_hash(page),
    }
    p = corpus.gold_path(page)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n"
                 + GOLD_BODY, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- profiles


def test_normalisation_profiles():
    assert bench.norm_diplomatic("P. Gonçalo  DEL Rego") == "p. gonçalo del rego"
    modern = bench.norm_modern("Gonçalo uſque")
    assert modern == "goncalo vsqve"        # diacritics stripped, long-s folded, u→v
    assert "ç" in bench.norm_diplomatic("Gonçalo")
    assert "ç" not in bench.norm_modern("Gonçalo")


def test_cer():
    assert bench.cer("abc def", "abc def", "diplomatic") == 0.0
    assert bench.cer("abc", "abc", "modern") == 0.0
    assert 0 < bench.cer("abcdef", "abcxef", "diplomatic") < 1
    assert bench.cer("", "abc", "diplomatic") is None
    # an insertion is an error, so a long addition can push CER above 1
    assert bench.cer("a", "a" + "b" * 10, "diplomatic") > 1


def test_name_tokens():
    names = bench.name_tokens("P. Manuel Rodrigues professo de Coimbra. Do Estado.")
    assert "Manuel" in names and "Rodrigues" in names and "Coimbra" in names
    assert "professo" not in names
    assert "Estado" not in names            # stop list
    assert all(not n.isupper() for n in bench.name_tokens("IESVS TEXTUS"))


# --------------------------------------------------------------------------- page scores


def test_score_page(cfg):
    corpus = make_corpus(cfg)
    write_gold(corpus)
    row = bench.score_page(corpus, "001")
    assert row["scored"] is True
    a = row["readers"]["A"]
    b = row["readers"]["B"]
    assert a["cer_diplomatic"] == 0.0 and a["cer_modern"] == 0.0
    assert b["cer_diplomatic"] > 0
    assert a["entry_exact"] == [2, 2]
    assert b["entry_exact"] == [1, 2]
    assert a["names"][1] >= 3
    assert 0 <= a["names"][0] <= a["names"][1]


def test_score_page_excludes_stale_and_pending(cfg):
    corpus = make_corpus(cfg)
    write_gold(corpus)
    # change a reading → the recorded hash no longer matches
    (corpus.root / "A" / "001.md").write_text(HEADER.format(
        page="001", r="A", body="totally different"), encoding="utf-8")
    assert corpus.is_stale("001")
    row = bench.score_page(corpus, "001")
    assert row["scored"] is False and "stale" in row["reason"]

    write_gold(corpus, status=bench.STATUS_PENDING, hash_="")
    row = bench.score_page(corpus, "001")
    assert row["scored"] is False and "pending" in row["reason"]


def test_score_corpus_writes_artefacts_and_caveats(cfg):
    corpus = make_corpus(cfg)
    write_gold(corpus)
    res = bench.score_corpus(corpus, write=True)
    assert res["schema"] == bench.SCORE_VERSION
    assert len(res["pages"]) == 1
    assert "reference" not in res["aggregates"]      # no reference/ built
    assert any("n_pages=1" in c for c in res["caveats"])
    files = corpus.root / "scores"
    assert (files / "results.json").is_file()
    assert (files / "results.csv").is_file()
    loaded = json.loads((files / "results.json").read_text())
    assert loaded["aggregates"]["A"]["pages"] == 1
    assert loaded["aggregates"]["A"]["entry_exact"] == 1.0
    csv_text = (files / "results.csv").read_text()
    assert csv_text.splitlines()[0].startswith("page,reader,cer_diplomatic")
    assert "001,A," in csv_text


def test_scores_render_in_the_workbench(cfg):
    corpus = make_corpus(cfg)
    write_gold(corpus)
    bench.score_corpus(corpus, write=True)
    wb = benchui.Workbench(cfg)
    html_text = benchui.render_corpus(wb, corpus).decode()
    assert "Scores" in html_text and "CER dipl." in html_text
    assert "Model A" in html_text or "A" in html_text
    assert "screen for failure" in html_text


# --------------------------------------------------------------------------- export


def _export_corpus(cfg, with_image=True, with_gold=True):
    corpus = make_corpus(cfg)
    if with_gold:
        write_gold(corpus)
    if with_image:
        (corpus.root / "images").mkdir()
        (corpus.root / "images" / "001.jpg").write_bytes(b"jpeg-bytes")
    return corpus


def test_export_site(cfg, tmp_path):
    corpus = _export_corpus(cfg)
    out = tmp_path / "site"
    res = benchexport.export_site(corpus, cfg, out=out)
    assert res["pages"] == 1 and res["images"] == 1 and res["scores"] is True
    for name in ("index.html", "style.css", "manifest.json",
                 "pages/001.html", "images/001.jpg",
                 "data/results.json", "data/results.csv"):
        assert (out / name).is_file(), name
    page_html = (out / "pages" / "001.html").read_text()
    assert "P. Manuel Rodrigues, professo." in page_html
    assert "../images/001.jpg" in page_html
    assert "human-corrected" in page_html
    assert str(cfg.data) not in page_html
    index = (out / "index.html").read_text()
    assert "Test corpus" in index and "CER dipl." in index
    assert "data/results.json" in index
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["pages"] == ["001"] and manifest["scores"] is True
    assert str(cfg.data) not in json.dumps(manifest)


def test_export_refuses_nonempty_without_force(cfg, tmp_path):
    corpus = _export_corpus(cfg)
    out = tmp_path / "site"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(SystemExit):
        benchexport.export_site(corpus, cfg, out=out)
    assert (out / "keep.txt").exists()
    benchexport.export_site(corpus, cfg, out=out, force=True)
    assert not (out / "keep.txt").exists()
    assert (out / "index.html").is_file()


def test_export_without_gold_labels_machine_merge(cfg, tmp_path):
    corpus = _export_corpus(cfg, with_image=False, with_gold=False)
    # a machine merge exists, no gold
    (corpus.root / "reference").mkdir()
    (corpus.root / "reference" / "001.md").write_text(
        "---\npage: '001'\nstatus: machine-merged\n---\n\n# 001\n\n"
        "## Reference text (variants in slashes)\n\n```\n[1] merge one\n```\n",
        encoding="utf-8")
    res = benchexport.export_site(corpus, cfg, out=tmp_path / "site")
    assert res["scores"] is False
    html_text = (tmp_path / "site" / "pages" / "001.html").read_text()
    assert "Machine merge (not reviewed)" in html_text
    assert "no page has a valid gold" in " ".join(res["warnings"])


def test_export_text_only(cfg, tmp_path):
    corpus = _export_corpus(cfg)
    res = benchexport.export_site(corpus, cfg, out=tmp_path / "site", images=False)
    assert res["images"] == 0
    assert any("text-only" in w for w in res["warnings"])
    assert not any((tmp_path / "site" / "images").iterdir())

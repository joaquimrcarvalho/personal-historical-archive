"""``pha bench`` — benchmark corpora of palaeographer readings.

A **benchmark corpus** is a directory under the archive's ``benchmarks/`` tree::

    <corpus>/
      bench.yaml                # the corpus declaration (reader order, document)
      <reader>/<page>.md        # one reading per reader per page
      comparison/<page>.md      # aligned stacked readings (1. .. N.)
      reference/<page>.md       # machine merge: variants joined with '/'
      human/<page>.md           # the human-corrected gold
      images/<page>.<ext>       # optional; else resolved from the render cache

``bench.yaml`` is authoritative for the **reader order**, because that order is
the numbering ``1. .. N.`` used in every comparison and reference file. The
readings themselves are produced by ``pha bench run`` (each one a no-write
``pha test`` run: the archive is never touched) or imported from an existing
corpus.

Two formats matter, and they are the same file shape:

* a **reading** — front matter (optional) + ``## Transcription`` + a fenced
  block (or, older corpora, the body before ``## Notes``);
* the **gold** — front matter + the corrected text, still ``[n]``-numbered but
  with the stacked readings resolved: ``## Corrected text`` for a reviewed page,
  ``## Reference text`` for an unedited copy of the machine merge.

The gold front matter is the provenance record (``status``, ``reviewed_by``,
``reviewed_at``, ``readings_hash``); ``readings_hash`` is what makes a gold page
**stale** when the readings beneath it change. See BENCHMARK_TOOL_PLAN.md for
the design and for what is deliberately not here yet (the workbench UI, P3, and
scoring, P4).
"""
from __future__ import annotations

import difflib
import hashlib
from collections import Counter
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from .config import Config

# --------------------------------------------------------------------------- constants

RESERVED_DIRS = {
    "comparison", "reference", "human", "images", "readings",
    "prompts-sent", "prompts",
}
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")
PDF_EXTS = (".pdf",)

NON_PAGE = {"overview.md", "README.md"}

M1 = "## Entry-by-entry comparison"
M2 = "## Key differences on this page"
GOLD_SECTION = "## Corrected text"
REF_SECTION = "## Reference text"
REF_SECTION_LONG = "## Reference text (variants in slashes)"
STATUS_GOLD = "human-corrected"
STATUS_MERGED = "machine-merged"
STATUS_PENDING = "pending"
STATUS_DRAFT = "draft"
GOLD_STATUSES = (STATUS_GOLD, STATUS_MERGED, STATUS_PENDING, STATUS_DRAFT)

ANNOT = re.compile(r"\s*\([^)]*(?:reading|renders|is written|not transcribed|omitted)[^)]*\)")
ALL_RE = re.compile(r"^=\s*all\b[^:]*:\s*(.*)$", re.I)
READ_RE = re.compile(r"^(\d+)\.\s+(.*)$")
ENTRY_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")
ROW_RE = re.compile(r"^(\d+)\s*[|.)]\s*(.*)$")
# A numbered row that is really a table row: a separator, then whitespace, then
# text. Stricter than ROW_RE so "1.º de henero" is not mistaken for row 1.
STRICT_ROW_RE = re.compile(r"^(\d+)\s*[|.)]\s+(\S.*)$", re.M)
# markdown pipe-table row: "| 3 P. Petrus Gómez | Hispanus ... |"
PIPE_ROW_RE = re.compile(r"^\|\s*(\d+)\s+(.*)$")
CAPTION_RE = re.compile(r"^#\s*Benchmark:\s*(.+?)\s+—\s+(.+)$", re.M)
MODEL_RE = re.compile(r"model `([^`]+)`")
RULES_RE = re.compile(r"pha palaeographer ([^,\s]+)")
ACCESS_RE = re.compile(r"via (.+?) \(model")
PAGE_HEADER_RE = re.compile(
    r"^#\s*Page\s+\d+\s*—\s*(?:raw transcription|edited)\s*\n+"
    r"|^---\s*Page\s+\d+\s*---\s*\n+",
    re.M,
)

CONN = {"--", "-", "/"}


# --------------------------------------------------------------------------- model


@dataclass
class Reader:
    """One palaeographer/model reading a corpus, in the corpus's fixed order."""

    dir: str
    label: str = ""
    model: str = ""
    access: str = ""
    rules: str = ""

    @property
    def name(self) -> str:
        return self.label or self.dir


@dataclass
class Corpus:
    root: Path
    title: str
    readers: list[Reader] = field(default_factory=list)
    palaeographer: str = ""
    document: str = ""
    document_pages: dict[int, str] = field(default_factory=dict)  # pha page -> page id
    images_mode: str = "render"
    gold_reviewer: str = ""
    editing_rules: list[str] = field(default_factory=list)
    editing_rule_notes: list[dict] = field(default_factory=list)
    notes: str = ""
    rights: str = ""
    configured: bool = False

    # -- paths ------------------------------------------------------------
    def reader_path(self, reader: Reader, page: str) -> Path:
        return self.root / reader.dir / f"{page}.md"

    def comparison_path(self, page: str) -> Path:
        return self.root / "comparison" / f"{page}.md"

    def reference_path(self, page: str) -> Path:
        return self.root / "reference" / f"{page}.md"

    def gold_path(self, page: str) -> Path:
        return self.root / "human" / f"{page}.md"

    @property
    def images_dir(self) -> Path | None:
        """The corpus ``images/`` folder, looked for at the three levels the
        skill's ``verify_comparison.py`` accepts."""
        for cand in (self.root / "images", self.root.parent / "images",
                     self.root.parent.parent / "images"):
            if cand.is_dir():
                return cand
        return None

    # -- pages ------------------------------------------------------------
    def page_ids(self) -> list[str]:
        found: set[str] = set()
        for reader in self.readers:
            d = self.root / reader.dir
            if not d.is_dir():
                continue
            found.update(p.stem for p in d.glob("*.md") if p.name not in NON_PAGE)
        for sub in ("comparison", "reference", "human"):
            d = self.root / sub
            if d.is_dir():
                found.update(p.stem for p in d.glob("*.md") if p.name not in NON_PAGE)
        return sorted(found, key=natural_key)

    def reader(self, name: str) -> Reader | None:
        for r in self.readers:
            if r.dir == name or r.name == name:
                return r
        return None

    # -- readings ---------------------------------------------------------
    def reading(self, reader: Reader, page: str) -> str | None:
        path = self.reader_path(reader, page)
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        body = extract_transcription(text)
        return body if is_valid_reading(body) else None

    def readings(self, page: str) -> list[tuple[Reader, str | None]]:
        return [(r, self.reading(r, page)) for r in self.readers]

    def readings_hash(self, page: str) -> str:
        """A stable hash of every reading on the page, in corpus order.

        The gold records it; when a reading is replaced (re-read with a new
        model, or after a prompt edit) the hash changes and the gold is stale.
        """
        h = hashlib.sha256()
        for r in self.readers:
            h.update(r.dir.encode("utf-8"))
            h.update(b"\0")
            h.update((self.reading(r, page) or "").encode("utf-8"))
            h.update(b"\0")
        return "sha256:" + h.hexdigest()

    # -- gold -------------------------------------------------------------
    def gold_meta(self, page: str) -> dict:
        path = self.gold_path(page)
        if not path.is_file():
            return {}
        try:
            meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
        except OSError:
            return {}
        return meta

    def is_gold(self, page: str) -> bool:
        return str(self.gold_meta(page).get("status") or "") == STATUS_GOLD

    def is_draft(self, page: str) -> bool:
        """A part-reviewed page: kept and resumed, but never scored as gold."""
        return str(self.gold_meta(page).get("status") or "") == STATUS_DRAFT

    def is_stale(self, page: str) -> bool:
        meta = self.gold_meta(page)
        recorded = str(meta.get("readings_hash") or "")
        status = str(meta.get("status") or "")
        return (bool(recorded) and status in (STATUS_GOLD, STATUS_DRAFT)
                and recorded != self.readings_hash(page))


# --------------------------------------------------------------------------- text helpers


def natural_key(stem: str) -> tuple:
    """Reading order for page ids: ``507``, ``507v``, ``508`` …"""
    m = re.match(r"^(\d+)\s*([vV]?)(.*)$", stem)
    if not m:
        return (10**9, 0, stem.lower())
    return (int(m.group(1)), 1 if m.group(2).lower() == "v" else 0, m.group(3).lower())


def split_front_matter(text: str) -> tuple[dict, str]:
    """Split a leading YAML front-matter block from the body (never raises)."""
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*\n?", text, re.S)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[m.end():]


def first_fenced(region: str) -> str | None:
    """The content of the first fenced ``` block in ``region``, or None."""
    start = None
    lines = region.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if start is None:
            if s.startswith("```"):
                start = i
            continue
        if s.startswith("```"):
            return "\n".join(lines[start + 1:i])
    if start is not None:
        # an unclosed fence: many hand-made readings open ``` and never close
        # it before the next heading. Treat the rest as the block rather than
        # leaking the fence into the transcription.
        return "\n".join(lines[start + 1:])
    return None


READING_HEADINGS = (
    "## Transcription",
    "## Corrected text",
    "## Corrected transcription",
    "## Reference text",
    "## Reference reading",
)


def extract_transcription(text: str, heading: str | None = None) -> str:
    """The transcription body of a page file, whatever shape it has.

    Handles, in order: an explicit ``heading``; ``## Transcription``;
    ``## Corrected text`` / ``## Reference text`` / ``## Reference reading``;
    then a body before ``## Notes`` (the older reading-file shape). A fenced
    block wins when present; otherwise the section's text is returned with any
    leading ``# title`` line dropped.
    """
    _meta, body = split_front_matter(text)
    region = body
    headings = ([heading] if heading else []) + list(READING_HEADINGS)
    for h in headings:
        if h and h in region:
            region = region.split(h, 1)[1]
            break
    m = re.search(r"^## ", region, re.M)
    if m:
        region = region[:m.start()]
    fenced = first_fenced(region)
    if fenced is not None:
        return fenced.strip("\n")
    lines = [ln.rstrip() for ln in region.splitlines() if not ln.startswith("# ")]
    return "\n".join(lines).strip("\n")


def is_valid_reading(text: str | None) -> bool:
    """A non-empty body that is not a model/engine ``ERROR:`` block."""
    if not text:
        return False
    stripped = text.strip()
    return bool(stripped) and not " ".join(stripped.split()).startswith("ERROR:")


def norm_entry(text: str) -> str:
    """Case/accent/punctuation-insensitive form used for entry equality."""
    s = text.replace("|", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip(" .,;:")


# --------------------------------------------------------------------------- corpus loading


def _reader_from_raw(raw) -> Reader | None:
    if isinstance(raw, str):
        return Reader(dir=raw, label=raw)
    if not isinstance(raw, dict) or not raw.get("dir"):
        return None
    d = str(raw["dir"])
    return Reader(dir=d, label=str(raw.get("label") or d),
                  model=str(raw.get("model") or ""),
                  access=str(raw.get("access") or ""),
                  rules=str(raw.get("rules") or ""))


def infer_readers(root: Path, include_extra: bool = False) -> list[Reader]:
    """Reader folders of an unconfigured corpus: every subdir holding .md files,
    except the reserved artefact folders."""
    readers: list[Reader] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in RESERVED_DIRS or d.name.startswith("."):
            continue
        files = [p for p in d.glob("*.md") if p.name not in NON_PAGE]
        if not files:
            continue
        reader = Reader(dir=d.name, label=d.name)
        try:
            head = files[0].read_text(encoding="utf-8")[:2000]
        except OSError:
            head = ""
        m = CAPTION_RE.search(head)
        if m:
            reader.label = m.group(2).strip() or reader.label
        mm = MODEL_RE.search(head)
        if mm:
            reader.model = mm.group(1)
        ma = ACCESS_RE.search(head)
        if ma:
            reader.access = ma.group(1).strip()
        mr = RULES_RE.search(head)
        if mr:
            reader.rules = mr.group(1)
        readers.append(reader)
    return _order_by_comparison(root, readers, include_extra=include_extra)


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _map_reader(part: str, readers: list[Reader]) -> Reader | None:
    key = _norm_name(part)
    for r in readers:
        if part == r.dir or part == r.label:
            return r
        if key in (_norm_name(r.dir), _norm_name(r.label)):
            return r
    return None


def _order_by_comparison(root: Path, readers: list[Reader],
                         include_extra: bool = False) -> list[Reader]:
    """If the corpus already has comparison files, take the reader order from
    the comparison title — that order *is* the 1..N numbering the files use.

    Falls back to the discovered order when any name is unmapped, so a stale or
    hand-written title never silently drops a reader.
    """
    comp = root / "comparison"
    if not comp.is_dir() or not readers:
        return readers
    pages = sorted((p for p in comp.glob("*.md") if p.name not in NON_PAGE),
                   key=lambda p: natural_key(p.stem))
    if not pages:
        return readers
    try:
        head = pages[0].read_text(encoding="utf-8")
    except OSError:
        return readers
    m = re.search(r"^# .*?\((.+)\)\s*$", head, re.M)
    if not m:
        return readers
    parts = [re.sub(r"^\s*\d+\.\s*", "", x).strip()
             for x in m.group(1).split("·")]
    mapped = [_map_reader(x, readers) for x in parts if x]
    if len(mapped) < 2 or any(r is None for r in mapped):
        return readers
    ordered = [r for r in mapped if r is not None]
    if not include_extra:
        return ordered
    order = {r.dir for r in ordered}
    return ordered + [r for r in readers if r.dir not in order]


def infer_palaeographer(readers: Iterable[Reader]) -> str:
    for r in readers:
        if r.rules:
            return r.rules
    return ""


def load_corpus(root: Path) -> Corpus:
    """Load a corpus from ``bench.yaml`` (or infer one when it has none)."""
    root = Path(root)
    yml = root / "bench.yaml"
    raw: dict = {}
    configured = yml.is_file()
    if configured:
        try:
            raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:  # noqa: BLE001 - report, do not crash
            print(f"warning: cannot read {yml}: {e}", file=sys.stderr)
            raw = {}
    doc = raw.get("document") or {}
    if isinstance(doc, str):
        doc = {"path": doc}
    pages: dict[int, str] = {}
    for k, v in (doc.get("pages") or {}).items():
        try:
            pages[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    readers = [r for r in (_reader_from_raw(x) for x in (raw.get("readings") or [])) if r]
    if not readers:
        readers = infer_readers(root) if root.is_dir() else []
    images = raw.get("images") or {}
    gold = raw.get("gold") or {}
    rules_raw = gold.get("editing_rules") or raw.get("editing_rules") or []
    if isinstance(rules_raw, str):
        rules_raw = [rules_raw]
    return Corpus(
        root=root,
        title=str(raw.get("title") or root.name),
        readers=readers,
        palaeographer=str(raw.get("palaeographer") or infer_palaeographer(readers)),
        document=str(doc.get("path") or ""),
        document_pages=pages,
        images_mode=str(images.get("mode") or "render"),
        gold_reviewer=str(gold.get("reviewer") or ""),
        editing_rules=[n["rule"] for n in _rule_notes(rules_raw)],
        editing_rule_notes=_rule_notes(rules_raw),
        notes=str(raw.get("notes") or ""),
        rights=str(raw.get("rights") or ""),
        configured=configured,
    )


def looks_like_corpus(d: Path) -> bool:
    if (d / "bench.yaml").is_file():
        return False
    subs = [x for x in d.iterdir() if x.is_dir() and x.name not in RESERVED_DIRS
            and not x.name.startswith(".")]
    md_dirs = [x for x in subs if any(x.glob("*.md"))]
    return len(md_dirs) >= 2 or (d / "comparison").is_dir()


def discover(root: Path) -> list[Corpus]:
    """Every configured corpus under ``root`` (dirs holding ``bench.yaml``)."""
    root = Path(root)
    if not root.is_dir():
        return []
    return [load_corpus(p.parent) for p in sorted(root.rglob("bench.yaml"))
            if ".git" not in p.parts]


def candidates(root: Path) -> list[Path]:
    """Corpus-shaped directories under ``root`` that have no ``bench.yaml`` yet."""
    root = Path(root)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for d in sorted(root.rglob("*")):
        if not d.is_dir() or ".git" in d.parts:
            continue
        if any(part in RESERVED_DIRS or part.startswith(".")
               for part in d.relative_to(root).parts):
            continue
        if looks_like_corpus(d):
            out.append(d)
    return out


def bench_root(cfg: Config, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("PHA_BENCH_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return cfg.bench_root


def _corpus_dir(d: Path) -> Path:
    """The folder that actually holds the corpus artefact folders.

    A corpus normally *is* the folder with comparison/reference/human; historical
    layouts bury it one level down (``<volume>/palaeographers/``), so accept the
    parent too instead of silently finding zero pages.
    """
    if (d / "bench.yaml").is_file() or (d / "comparison").is_dir():
        return d
    child = d / "palaeographers"
    if child.is_dir() and ((child / "bench.yaml").is_file()
                           or (child / "comparison").is_dir()):
        return child
    if child.is_dir() and not any(
            x.is_dir() and x.name != "palaeographers" and x.name not in RESERVED_DIRS
            for x in d.iterdir()):
        return child
    return d


def resolve_corpus(cfg: Config, arg: str, root_override: str | None = None) -> Corpus:
    """Resolve a corpus argument: a path, or a path suffix under the bench root.

    Accepts ``1577``, ``jesuit-cat-type4/portugal/1577``, an absolute path, or
    the historical layout where the corpus lives in ``<volume>/palaeographers``.
    """
    root = bench_root(cfg, root_override)
    direct = root / arg
    if direct.is_dir():
        return load_corpus(_corpus_dir(direct))
    p = Path(arg).expanduser()
    if p.is_dir():
        return load_corpus(_corpus_dir(p))
    if root.is_dir():
        arg_slash = arg.strip("/")
        hits = []
        for d in sorted(root.rglob("*")):
            if not d.is_dir() or ".git" in d.parts:
                continue
            rel = str(d.relative_to(root))
            if rel == arg_slash or rel.endswith("/" + arg_slash):
                hits.append(d)
        if len(hits) == 1:
            return load_corpus(_corpus_dir(hits[0]))
        if len(hits) > 1:
            names = "\n  ".join(str(c) for c in hits)
            raise SystemExit(f"ambiguous corpus {arg!r}; matches:\n  {names}")
    raise SystemExit(f"unknown corpus {arg!r} under {root} (try `pha bench list`)")


# --------------------------------------------------------------------------- bench.yaml


def init_corpus(root: Path, title: str | None = None,
                palaeographer: str | None = None, force: bool = False,
                all_readers: bool = False) -> Corpus:
    """Write ``bench.yaml`` for an existing corpus, inferring readers + models."""
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    yml = root / "bench.yaml"
    if yml.exists() and not force:
        raise SystemExit(f"{yml} already exists (use --force to rewrite it)")
    readers = infer_readers(root, include_extra=all_readers)
    if not readers:
        raise SystemExit(
            f"no reader folders under {root}: every subdirectory with .md files "
            f"is a reader (comparison/reference/human/images are reserved)")
    default_title = root.parent.name if root.name == "palaeographers" else root.name
    corpus = Corpus(root=root, title=title or default_title, readers=readers,
                    palaeographer=palaeographer or infer_palaeographer(readers))
    data = {
        "title": corpus.title,
        "palaeographer": corpus.palaeographer,
        "document": {"path": "", "pages": {}},
        "images": {"mode": "copy" if (root / "images").is_dir() else "render"},
        "readings": [
            {"dir": r.dir, "label": r.label, "model": r.model, "access": r.access}
            for r in readers
        ],
        "gold": {"reviewer": corpus.gold_reviewer,
                 "editing_rules": _rules_out(corpus)},
    }
    yml.write_text(
        "# Benchmark corpus (pha bench). Reader order is the 1..N numbering used in\n"
        "# comparison/ and reference/. Fill in `document` so `pha bench run` knows\n"
        "# which archive document the pages come from.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    corpus.configured = True
    return corpus


def _rule_notes(raw_rules) -> list[dict]:
    """Normalise `gold.editing_rules` entries.

    An entry is either the rule string (what the gold front matter records) or a
    mapping ``{rule, explain, example}``: the workbench shows the explanation
    and the example beside the rule, while the artefact keeps the short line.
    """
    notes: list[dict] = []
    for item in raw_rules:
        if isinstance(item, dict):
            rule = str(item.get("rule") or item.get("text") or "").strip()
            if not rule:
                continue
            notes.append({"rule": rule,
                          "explain": str(item.get("explain") or "").strip(),
                          "example": str(item.get("example") or "").strip()})
        else:
            rule = str(item).strip()
            if rule:
                notes.append({"rule": rule, "explain": "", "example": ""})
    return notes


def _rules_out(corpus: Corpus) -> list:
    notes = {n["rule"]: n for n in corpus.editing_rule_notes}
    out: list = []
    for rule in corpus.editing_rules:
        n = notes.get(rule) or {}
        if n.get("explain") or n.get("example"):
            item = {"rule": rule}
            if n.get("explain"):
                item["explain"] = n["explain"]
            if n.get("example"):
                item["example"] = n["example"]
            out.append(item)
        else:
            out.append(rule)
    return out


def save_corpus_yaml(corpus: Corpus) -> None:
    """Rewrite bench.yaml keeping its reader order (used by future passes)."""
    data = {
        "title": corpus.title,
        "palaeographer": corpus.palaeographer,
        "document": {"path": corpus.document,
                     "pages": {str(k): v for k, v in sorted(corpus.document_pages.items())}},
        "images": {"mode": corpus.images_mode},
        "readings": [
            {"dir": r.dir, "label": r.label, "model": r.model, "access": r.access}
            for r in corpus.readers
        ],
        "gold": {"reviewer": corpus.gold_reviewer,
                 "editing_rules": _rules_out(corpus)},
    }
    (corpus.root / "bench.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# --------------------------------------------------------------------------- entries


@dataclass
class Entry:
    num: int
    kind: str              # "all" | "diff"
    readings: list[str] = field(default_factory=list)   # kind == diff
    text: str = ""                                      # kind == all


def _dehyphenate(lines: Iterable[str]) -> str:
    out = ""
    for ln in lines:
        s = ANNOT.sub("", ln.strip())
        if not s:
            continue
        if out and out[-1] in "-=" and s[0] not in "-=":
            out = out[:-1] + s
        elif out:
            out = out + " " + s
        else:
            out = s
    return out


def parse_comparison_entries(text: str) -> list[Entry]:
    """Parse the ``[n]`` entries of a comparison page file.

    Reading markers are recognised only in sequence (1., 2., 3., …), so a
    continuation line that starts with a number ("3. Nouicos.") is not mistaken
    for a new reading. ``= all readings:`` entries carry no readings.
    """
    if M1 not in text or M2 not in text:
        return []
    i = text.index(M1)
    j = text.index(M2)
    block = text[i + len(M1):j].splitlines()
    raw: list[dict] = []
    cur = None
    for ln in block:
        s = ln.strip()
        if s == "" or s.startswith("```"):
            continue
        m = ENTRY_RE.match(s)
        if m:
            if cur:
                raw.append(cur)
            cur = {"num": int(m.group(1)), "rest": m.group(2), "lines": []}
        elif cur is not None:
            cur["lines"].append(ln)
    if cur:
        raw.append(cur)

    entries: list[Entry] = []
    for e in raw:
        rest = e["rest"].strip()
        m = ALL_RE.match(rest)
        if m:
            entries.append(Entry(e["num"], "all",
                                 text=_dehyphenate([m.group(1)] + e["lines"])))
            continue
        reads: dict[int, list[str]] = {}
        cur_n = None
        expected = 1
        for ln in [rest] + e["lines"]:
            s = ln.strip()
            m = READ_RE.match(s)
            if m and int(m.group(1)) == expected:
                cur_n = int(m.group(1))
                reads[cur_n] = [m.group(2)]
                expected += 1
            elif cur_n is not None:
                reads[cur_n].append(s)
        if not reads:
            reads[1] = [rest] + [ln.strip() for ln in e["lines"]]
        entries.append(Entry(e["num"], "diff",
                             readings=[_dehyphenate(reads[k]) for k in sorted(reads)]))
    return entries


def reading_counts(text: str) -> dict[int, int]:
    """Per-entry reading count (0 for an ``= all …`` entry)."""
    return {e.num: (0 if e.kind == "all" else len(e.readings))
            for e in parse_comparison_entries(text)}


def entry_numbers(text: str, section: str = "") -> list[int]:
    """The ``[n]`` numbers inside the first fenced block (or a named section)."""
    region = text
    if section and section in region:
        region = region.split(section, 1)[1]
    fenced = first_fenced(region)
    body = fenced if fenced is not None else region
    return [int(m.group(1)) for m in
            (ENTRY_RE.match(ln.strip()) for ln in body.splitlines()) if m]


# --------------------------------------------------------------------------- verification


@dataclass
class FileReport:
    path: Path
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_comparison_text(text: str, readings: int | None = None) -> list[str]:
    """Structural checks of one comparison page file (the skill's contract)."""
    problems: list[str] = []
    fences = [ln for ln in text.splitlines() if ln.strip() == "```"]
    if len(fences) != 2:
        problems.append(f"expected 2 fence lines, found {len(fences)}")
    sections = re.findall(r"^## (.+)$", text, flags=re.M)
    if sections != ["Entry-by-entry comparison", "Key differences on this page"]:
        problems.append(f"sections are {sections!r}")
    if M1 in text and M2 in text:
        i = text.index(M1)
        j = text.index(M2)
        middle = text[i + len(M1):j]
        if not middle.startswith("\n\n```"):
            problems.append("no blank line before the opening fence")
        if not middle.rstrip().endswith("```"):
            problems.append("entry block not closed by a fence")
        nums = entry_numbers(middle)
        if not nums:
            problems.append("no [n] entry markers found")
        elif nums != list(range(1, len(nums) + 1)):
            problems.append(f"entry numbers not continuous: {nums[:8]}…")
        if readings is not None:
            counts = reading_counts(text)
            nonzero = [c for c in counts.values() if c]
            divergent = bool(nonzero) and all(c == 1 for c in nonzero)
            if not divergent:
                bad = {n: c for n, c in counts.items() if c not in (0, readings)}
                if bad:
                    preview = ", ".join(f"[{n}]={c}" for n, c in sorted(bad.items())[:6])
                    problems.append(f"entries whose reading count != {readings}: {preview}")
    return problems


def verify_gold_text(text: str, readings_hash: str | None = None,
                     comparison_numbers: list[int] | None = None) -> tuple[list[str], list[str]]:
    """Checks of one gold page. Returns (problems, warnings)."""
    problems: list[str] = []
    warnings: list[str] = []
    meta, body = split_front_matter(text)
    status = str(meta.get("status") or "")
    if not meta:
        warnings.append("no front matter (status/reviewer/readings_hash unknown)")
    elif status not in GOLD_STATUSES:
        problems.append(f"unknown status {status!r} (expected one of {GOLD_STATUSES})")
    if GOLD_SECTION not in body and REF_SECTION not in body:
        problems.append(f"no {GOLD_SECTION!r} or {REF_SECTION!r} section")
    fences = [ln for ln in body.splitlines() if ln.strip() == "```"]
    if len(fences) != 2:
        problems.append(f"expected 2 fence lines in the gold body, found {len(fences)}")
    nums = entry_numbers(body)
    if not nums:
        problems.append("no [n] entry markers found")
    elif nums != list(range(1, len(nums) + 1)):
        problems.append(f"gold entry numbers not continuous: {nums[:8]}…")
    if comparison_numbers is not None and nums and nums != comparison_numbers:
        # A human may split or merge entries deliberately; scoring aligns them,
        # so this is a note, not a structural failure.
        warnings.append(
            f"gold has {len(nums)} entries, the comparison {len(comparison_numbers)}"
            " — scoring will align them; check the difference is deliberate")
    # a gold page must be resolved text, not stacked readings
    fenced = first_fenced(body) or ""
    stacked = [ln.strip() for ln in fenced.splitlines()
               if re.match(r"^\s*\d+\.\s+", ln) and ln.startswith(" ")]
    if stacked:
        problems.append(f"gold still contains stacked reading markers: {stacked[:3]}")
    if readings_hash and meta.get("readings_hash") and str(meta["readings_hash"]) != readings_hash:
        problems.append("gold is STALE: its readings_hash no longer matches the readings")
    return problems, warnings


def verify_corpus(corpus: Corpus, readings: int | None = None,
                  require_images: bool = False,
                  include_gold: bool = True) -> list[FileReport]:
    """Verify every comparison file (and, optionally, every gold page)."""
    reports: list[FileReport] = []
    images = corpus.images_dir
    if images is None:
        msg = ("no images/ folder for the corpus (looked in "
               f"{corpus.root}/images, {corpus.root.parent}/images, "
               f"{corpus.root.parent.parent}/images)")
        rep = FileReport(corpus.root / "images")
        (rep.problems if require_images else rep.warnings).append(msg)
        reports.append(rep)
    for page in corpus.page_ids():
        comp = corpus.comparison_path(page)
        if not comp.is_file():
            continue
        text = comp.read_text(encoding="utf-8")
        rep = FileReport(comp, verify_comparison_text(text, readings))
        if images is not None and not any(
                (images / (page + e)).exists() for e in IMAGE_EXTS):
            (rep.problems if require_images else rep.warnings).append(
                f"no image for {page} in {images}")
        reports.append(rep)
        if include_gold:
            gold = corpus.gold_path(page)
            if gold.is_file():
                gtext = gold.read_text(encoding="utf-8")
                comp_nums = entry_numbers(comp.read_text(encoding="utf-8"))
                probs, warns = verify_gold_text(
                    gtext, corpus.readings_hash(page), comp_nums)
                reports.append(FileReport(gold, probs, warns))
    return reports


# --------------------------------------------------------------------------- status


@dataclass
class PageStatus:
    page: str
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    compared: bool = False
    gold: bool = False
    draft: bool = False
    gold_status: str = ""
    stale: bool = False
    entries: int = 0


def page_status(corpus: Corpus, page: str) -> PageStatus:
    st = PageStatus(page=page)
    newest_reading = 0.0
    for reader in corpus.readers:
        path = corpus.reader_path(reader, page)
        text = corpus.reading(reader, page)
        if text is None:
            st.missing.append(reader.dir)
        else:
            st.present.append(reader.dir)
            try:
                newest_reading = max(newest_reading, path.stat().st_mtime)
            except OSError:
                pass
    comp = corpus.comparison_path(page)
    if comp.is_file():
        try:
            st.compared = comp.stat().st_mtime >= newest_reading
        except OSError:
            st.compared = False
        st.entries = len(entry_numbers(comp.read_text(encoding="utf-8")))
    meta = corpus.gold_meta(page)
    st.gold_status = str(meta.get("status") or "")
    st.gold = st.gold_status == STATUS_GOLD
    st.draft = st.gold_status == STATUS_DRAFT
    st.stale = corpus.is_stale(page)
    return st


def corpus_status(corpus: Corpus) -> dict:
    pages = [page_status(corpus, p) for p in corpus.page_ids()]
    return {
        "corpus": str(corpus.root),
        "title": corpus.title,
        "configured": corpus.configured,
        "readers": [{"dir": r.dir, "label": r.name, "model": r.model} for r in corpus.readers],
        "pages": len(pages),
        "readings_complete": sum(1 for p in pages if not p.missing),
        "compared": sum(1 for p in pages if p.compared),
        "gold": sum(1 for p in pages if p.gold),
        "drafts": sum(1 for p in pages if p.draft),
        "stale": sum(1 for p in pages if p.stale),
        "page_status": [
            {"page": p.page, "present": p.present, "missing": p.missing,
             "compared": p.compared, "gold": p.gold, "draft": p.draft,
             "status": p.gold_status,
             "stale": p.stale, "entries": p.entries}
            for p in pages
        ],
    }


# --------------------------------------------------------------------------- comparison


def _tabular_rows(text: str) -> tuple[list[str], dict[int, str]]:
    """Numbered rows of a tabular transcription (``1. …`` / ``1| …`` / ``1) …``)."""
    preface: list[str] = []
    rows: dict[int, str] = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = ROW_RE.match(s)
        pm = PIPE_ROW_RE.match(s) if not m else None
        if m:
            cur = int(m.group(1))
            rows[cur] = m.group(2).strip()
        elif pm:
            cur = int(pm.group(1))
            rows[cur] = pm.group(2).strip()
        elif cur is not None:
            rows[cur] += " " + s
        else:
            preface.append(s)
    return preface, rows


def _lines_of(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def looks_tabular(text: str | None, min_rows: int = 3) -> bool:
    """Whether a transcription reads as a numbered table (catalogue rows)."""
    text = text or ""
    return (len(STRICT_ROW_RE.findall(text))
            + sum(1 for ln in text.splitlines() if PIPE_ROW_RE.match(ln.strip()))
            ) >= min_rows


def _reader_title(corpus: Corpus) -> str:
    return " · ".join(f"{i}. {r.name}" for i, r in enumerate(corpus.readers, 1))


def build_comparison(corpus: Corpus, profile: str = "auto",
                     readings: int | None = None,
                     write: bool = True) -> dict:
    """Build ``comparison/`` (and ``overview.md``) from the readings.

    ``profile``: ``tabular`` (numbered catalogue rows, aligned by row number),
    ``lines`` (positional line alignment, a mechanical draft for free text), or
    ``auto`` (tabular when the readings look tabular). The output follows the
    ``palaeographers-compare`` skeleton so the skill's verifier accepts it.
    """
    out = corpus.root / "comparison"
    if write:
        out.mkdir(parents=True, exist_ok=True)
    pages = corpus.page_ids()
    if not pages:
        raise SystemExit(f"no page files under {corpus.root} (readers: "
                         f"{[r.dir for r in corpus.readers]})")
    stats: list[dict] = []
    for page in pages:
        texts = [corpus.reading(r, page) for r in corpus.readers]
        if all(t is None for t in texts):
            continue
        use = profile
        if profile == "auto":
            tabular_readers = sum(1 for t in texts if looks_tabular(t))
            # two tabular readers, or a page where a table meets a blank/verso
            # leaf: the tabular profile's divergent branch keeps each in full
            mixed = any((t or "").strip() and not looks_tabular(t) for t in texts)
            use = "tabular" if tabular_readers >= 2 or (tabular_readers and mixed) else "lines"
        if use == "tabular":
            lines, ctx, n_total, n_same, n_diff = _comparison_tabular(corpus, page, texts)
        else:
            lines, ctx, n_total, n_same, n_diff = _comparison_lines(corpus, page, texts)
        body = [
            f"# {page} — Comparative readings ({_reader_title(corpus)})",
            "",
            f"Context: {ctx}",
            "",
            M1,
            "",
            "```",
            "\n\n".join(lines),
            "```",
            "",
            M2,
            "",
            *([] if n_diff else ["- none: all readings agree on every entry"]),
        ]
        if n_diff:
            body.extend(_diff_bullets(lines))
        text = "\n".join(body) + "\n"
        problems = verify_comparison_text(text, readings or len(corpus.readers))
        if write:
            (out / f"{page}.md").write_text(text, encoding="utf-8")
        stats.append({"page": page, "profile": use, "entries": n_total,
                      "same": n_same, "diff": n_diff,
                      "problems": problems})
    if write:
        (out / "overview.md").write_text(
            _overview(corpus, pages, stats, profile), encoding="utf-8")
    return {"pages": len(stats), "stats": stats, "out": str(out)}


def _comparison_tabular(corpus: Corpus, page: str,
                        texts: list[str | None]) -> tuple[list[str], str, int, int, int]:
    pre: list[str] = []
    maps: list[dict[int, str]] = []
    full: list[str] = []
    for t in texts:
        if t is None:
            pre.append("")
            maps.append({})
            full.append("[missing]")
            continue
        p, rows = _tabular_rows(t)
        pre.append(" ".join(p[:2]))
        maps.append(rows)
        full.append(t)
    nums = sorted(set().union(*[set(m) for m in maps])) if maps else []
    divergent = any(not m for m in maps) and any(m for m in maps)
    lines: list[str] = []
    same = diff = 0
    if divergent:
        def _prep(x: str) -> str:
            x = re.sub(r"(?m)^\s*\d+\s*[|.)]\s*", "", x)
            return x.replace("\n", "\n      ")
        # the skill's convention for a fundamentally divergent page: each
        # reading is given in full, as its own [n] entry (with one reading)
        lines = [f"[{k}] 1. {_prep(v) or '[missing]'}"
                 for k, v in enumerate(full, 1)]
        diff = len(lines)
    else:
        for i, n in enumerate(nums, 1):
            vals = [m.get(n, "") for m in maps]
            if all(vals) and len({norm_entry(v) for v in vals}) == 1:
                lines.append(f"[{i}] = all readings: {vals[0]}")
                same += 1
            else:
                block = [f"[{i}] 1. {vals[0] or '[missing]'}"]
                for k, v in enumerate(vals[1:], 2):
                    block.append(f"    {k}. {v or '[missing]'}")
                lines.append("\n".join(block))
                diff += 1
    ctx = (pre[0][:220] if pre and pre[0] else
           f"{len(nums)} numbered entries")
    return lines, f"{ctx} — {len(nums)} numbered entries.", len(nums), same, diff


def _comparison_lines(corpus: Corpus, page: str,
                      texts: list[str | None]) -> tuple[list[str], str, int, int, int]:
    arrays = [_lines_of(t or "") for t in texts]
    if any(not a for a in arrays) and any(arrays):
        # fundamental divergence: each reading in full, as its own [n] entry
        blocks = [f"[{k}] 1. {' '.join(arr) or '[missing]'}"
                  for k, arr in enumerate(arrays, 1)]
        return (blocks,
                "the readings diverge fundamentally; each is given in full.",
                len(blocks), len(blocks), len(blocks))
    longest = max((len(a) for a in arrays), default=0)
    lines: list[str] = []
    same = diff = 0
    for i in range(longest):
        vals = [a[i] if i < len(a) else "" for a in arrays]
        if all(vals) and len({norm_entry(v) for v in vals}) == 1:
            lines.append(f"[{i + 1}] = all readings: {vals[0]}")
            same += 1
        else:
            block = [f"[{i + 1}] 1. {vals[0] or '[missing]'}"]
            for k, v in enumerate(vals[1:], 2):
                block.append(f"    {k}. {v or '[missing]'}")
            lines.append("\n".join(block))
            diff += 1
    ctx = f"{longest} lines aligned positionally from {len(arrays)} readings"
    return lines, ctx + ".", longest, same, diff


def _diff_bullets(lines: list[str], limit: int = 6) -> list[str]:
    """Key-difference bullets: the first differing entries, first words only."""
    bullets: list[str] = []
    for ln in lines:
        if ln.startswith("["):
            first = ln.splitlines()[0]
            if "= all readings" in first:
                continue
            bullets.append(f"- {first[:160]}")
        if len(bullets) >= limit:
            break
    if not bullets:
        bullets = ["- none: all readings agree on every entry"]
    return bullets


def _overview(corpus: Corpus, pages: list[str], stats: list[dict],
              profile: str) -> str:
    n_pages = len(stats)
    n_diff = sum(1 for s in stats if s["diff"])
    notes = corpus.notes.strip() or (
        f"Corpus: {corpus.title}." +
        (f" Document: `{corpus.document}`." if corpus.document else ""))
    rows = [
        "# Comparative readings — " + corpus.title,
        "",
        "## Corpus",
        "",
        notes,
        "",
        "## The palaeographers",
        "",
        *[f"{i}. **{r.name}** — reader dir `{r.dir}`"
          + (f", model `{r.model}`" if r.model else "") + "."
          for i, r in enumerate(corpus.readers, 1)],
        "",
        "## Overall picture",
        "",
        f"- {n_pages} pages, {len(corpus.readers)} readings"
        + (f" (profile `{profile}`)" if profile and profile != "auto" else ""),
        f"- Pages with at least one disagreement: **{n_diff}**; "
        f"fully agreeing: **{n_pages - n_diff}**.",
        "- Generated by `pha bench compare`; where readings agree the reading is "
        "probable but still a first pass — check the image for any name used in a "
        "lexicon. The comparison is a triage tool, not an edition.",
        "",
        "## Page-by-page highlights",
        "",
        "| Page | Entries | Differing entries |",
        "|---|---:|---:|",
        *[f"| {s['page']} | {s['entries']} | {s['diff']} |" for s in stats],
        "",
        "## Files",
        "",
        "- `<page>.md` — stacked readings, 1…N.",
        "- `overview.md` — this file.",
    ]
    return "\n".join(rows) + "\n"


# --------------------------------------------------------------------------- reference (merge)


def _tok_eq(a: str, b: str) -> bool:
    return a.rstrip(".,") == b.rstrip(".,") or (a in CONN and b in CONN)


def _align(a: list[str], b: list[str]) -> list[tuple[str | None, str | None]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = 0 if _tok_eq(a[i - 1], b[j - 1]) else 1
            dp[i][j] = min(dp[i - 1][j - 1] + c, dp[i - 1][j] + 1, dp[i][j - 1] + 1)
    i, j = n, m
    pairs: list[tuple[str | None, str | None]] = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (
                0 if _tok_eq(a[i - 1], b[j - 1]) else 1):
            pairs.append((a[i - 1], b[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            pairs.append((a[i - 1], None))
            i -= 1
        else:
            pairs.append((None, b[j - 1]))
            j -= 1
    pairs.reverse()
    return pairs


def merge_readings(readings: list[str]) -> str:
    """Consolidate N readings into one line: common words once, variants ``a/b``."""
    lists = [[t for t in r.split() if t not in ("/", "-")] for r in readings]
    lists = [l for l in lists if l]
    if not lists:
        return ""
    if len(lists) == 1:
        return " ".join(lists[0])

    base = lists[0]
    cols: list[dict[int, str | None]] = [
        {0: t0, 1: t1} for t0, t1 in _align(base, lists[1])]
    for idx in range(2, len(lists)):
        col = 0
        for t0, ti in _align(base, lists[idx]):
            if t0 is not None:
                while col < len(cols) and (cols[col].get(0) is None or cols[col][0] != t0):
                    col += 1
                if col < len(cols):
                    cols[col][idx] = ti
                    col += 1
            else:
                nxt = col
                while nxt < len(cols) and cols[nxt].get(0) is None:
                    nxt += 1
                if nxt > 0 and cols[nxt - 1].get(0) is None and cols[nxt - 1].get(idx) is None:
                    cols[nxt - 1][idx] = ti
                else:
                    cols.insert(nxt, {0: None, idx: ti})

    def norm(tok: str) -> str:
        return tok.rstrip(".,;:")

    parts: list[str] = []
    for k in cols:
        vals = [k.get(i) for i in range(len(lists))]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        vals = ["--" if v in CONN else v for v in vals]
        if all(norm(v) == norm(vals[0]) for v in vals):
            parts.append(vals[0])
            continue
        uniq = [vals[0]]
        for v in vals[1:]:
            if norm(v) not in [norm(u) for u in uniq]:
                uniq.append(v)
        rendered = uniq[0]
        if len(uniq) > 1:
            rendered += "/" + "/".join(norm(u) for u in uniq[1:])
        parts.append(rendered)
    return " ".join(parts)


def reference_text(entries: list[Entry]) -> str:
    lines = []
    for e in entries:
        if e.kind == "all":
            lines.append(f"[{e.num}] {e.text}")
        else:
            lines.append(f"[{e.num}] {merge_readings(e.readings)}")
    return "\n".join(lines)


def _page_meta(text: str) -> tuple[str, str, str]:
    m = re.search(r"^# (.+)$", text, re.M)
    raw_title = m.group(1) if m else "Reference text"
    title = "# " + raw_title.replace("Comparative readings", "Reference text with variants")
    order = re.search(r"\((.*)\)\s*$", raw_title)
    ctx = re.search(r"^Context: .*$", text, re.M)
    return title, (order.group(1) if order else "reading order"), (
        ctx.group(0) if ctx else "Context: (see comparison file)")


def build_reference(corpus: Corpus, readings: int | None = None,
                    divergent: Iterable[str] = (), write: bool = True) -> dict:
    """Build ``reference/`` from ``comparison/`` (machine merge, slashed variants)."""
    comp_dir = corpus.root / "comparison"
    if not comp_dir.is_dir():
        raise SystemExit(f"no comparison folder at {comp_dir}; run `pha bench compare` first")
    files = [p for p in sorted(comp_dir.glob("*.md"), key=lambda p: natural_key(p.stem))
             if p.name not in NON_PAGE]
    if not files:
        raise SystemExit(f"no page files in {comp_dir}")
    if readings is not None and readings < 2:
        raise SystemExit("--readings must be at least 2")

    # validate before writing anything (a wrong --readings must not write bad files)
    if readings is not None:
        too_many: list[tuple[str, int]] = []
        too_few: list[tuple[str, int, int]] = []
        for p in files:
            entries = parse_comparison_entries(p.read_text(encoding="utf-8"))
            if entries:
                mx = max((len(e.readings) for e in entries if e.kind == "diff"), default=0)
                if mx > readings:
                    too_many.append((p.name, mx))
                diffs = [e for e in entries if e.kind == "diff"]
                if diffs and all(len(e.readings) == 1 for e in diffs):
                    continue
                for e in diffs:
                    if len(e.readings) < readings:
                        too_few.append((p.name, e.num, len(e.readings)))
                        break
        if too_many:
            print(f"error: --readings {readings} declared, but these pages have MORE:",
                  file=sys.stderr)
            for name, mx in too_many:
                print(f"  - {name}: reading {mx} found", file=sys.stderr)
            raise SystemExit(1)
        for name, num, n in too_few[:10]:
            print(f"warning: {name} entry [{num}] has {n} reading(s), "
                  f"expected {readings}", file=sys.stderr)

    out = corpus.root / "reference"
    if write:
        out.mkdir(parents=True, exist_ok=True)
    forced = {str(x) for x in divergent}
    stats = []
    for src in files:
        text = src.read_text(encoding="utf-8")
        title, order, context = _page_meta(text)
        entries = parse_comparison_entries(text)
        diffs = [e for e in entries if e.kind == "diff"]
        is_divergent = src.stem in forced or (
            bool(diffs) and all(len(e.readings) == 1 for e in diffs))
        if is_divergent:
            block = first_fenced(text) or ""
            section = (
                "No consolidation possible — the readings for this page diverge "
                f"fundamentally (see comparison/{src.name}). Each reading is given in full:"
                f"\n\n```\n{block}\n```")
            notes = [
                "- The readings for this page cannot be aligned; every reading is kept in full.",
                f"- See comparison/{src.name} for the full discussion.",
            ]
        else:
            section = "```\n" + reference_text(entries) + "\n```"
            notes = [
                f"- Convention: common words are written once; where the readings differ "
                f"the variants are joined with '/' in reading order ({order}).",
                "- Trailing periods/commas and line-end hyphenation are treated as padding; "
                "the name<->role connector ('—', '/', '-') is rendered as '—'. Entries "
                "identical in all readings are given as-is.",
                f"- Generated from comparison/{src.name} — see that file for the full "
                "stacked readings and the key differences.",
            ]
        meta = {
            "page": src.stem,
            "status": STATUS_MERGED,
            "generated_by": "pha bench reference",
            "generated_from": f"comparison/{src.name}",
            "readings": [r.dir for r in corpus.readers],
            "readings_hash": corpus.readings_hash(src.stem),
        }
        front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
        content = (
            f"---\n{front}---\n\n"
            + title + "\n\n" + context + "\n\n"
            + REF_SECTION_LONG + "\n\n" + section + "\n\n"
            + "## Notes\n\n" + "\n".join(notes) + "\n"
        )
        if write:
            (out / src.name).write_text(content, encoding="utf-8")
        stats.append({"page": src.stem, "entries": len(entries),
                      "divergent": is_divergent})
    return {"pages": len(stats), "stats": stats, "out": str(out)}


# --------------------------------------------------------------------------- harvest


def _reading_header(reader: Reader, label: str, source: str, pha_page: int,
                    rules: str) -> str:
    return (
        f"# Benchmark: {label} — {reader.name}\n\n"
        f"Source: {source} (pha page {pha_page})\n"
        f"Model: {reader.name} via {reader.access or 'unknown access'} "
        f"(model `{reader.model or '?'}`); pha palaeographer {rules or 'default'}, "
        f"editor pass OFF\n"
        f"Palaeographer rules: palaeographers/{rules or 'default'}.md\n\n"
        "## Transcription\n\n"
    )


def _write_reading(corpus: Corpus, reader: Reader, page: str, body: str,
                   source: str, pha_page: int) -> Path:
    dest = corpus.reader_path(reader, page)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fence = "```"
    while fence in body:
        fence += "`"
    text = (_reading_header(reader, page, source, pha_page, corpus.palaeographer)
            + f"{fence}\n{body}\n{fence}\n")
    dest.write_text(text, encoding="utf-8")
    return dest


def pha_page_for(corpus: Corpus, page: str, archive: Path) -> int | None:
    """Map a corpus page id to the pha page number `pha test --page N` takes."""
    for num, label in corpus.document_pages.items():
        if label == page:
            return num
    doc = corpus.document
    if not doc:
        return None
    p = Path(doc)
    if not p.is_absolute():
        p = archive / p
    if p.is_dir():
        files = sorted(x for x in p.rglob("*") if x.suffix.lower() in IMAGE_EXTS)
        for i, f in enumerate(files, 1):
            if f.stem == page:
                return i
        return None
    if p.suffix.lower() in PDF_EXTS:
        m = re.match(r"^p?(\d+)$", page)
        return int(m.group(1)) if m else None
    return None


def harvest(corpus: Corpus, cfg: Config, pages: Iterable[str] | None = None,
            readers: Iterable[str] | None = None, force: bool = False,
            dry_run: bool = False, max_tokens: int = 32768) -> dict:
    """Produce readings with `pha test` (no-write), one model run per page.

    The model runs happen sequentially: `pha test` locks every model server for
    the call, and the benchmark must never parallelise across a shared server.
    """
    if not corpus.readers:
        raise SystemExit("no readers: write bench.yaml with `pha bench init`")
    chosen = list(corpus.readers)
    if readers:
        wanted = {x.strip() for x in readers}
        chosen = [r for r in corpus.readers if r.dir in wanted or r.name in wanted]
        unknown = wanted - {r.dir for r in chosen} - {r.name for r in chosen}
        if unknown:
            raise SystemExit(f"unknown reader(s): {sorted(unknown)}")
    if not corpus.document:
        raise SystemExit(
            "bench.yaml has no document: path — set it to the archive document "
            "(e.g. dropbox/collections/.../book.pdf) or a directory of images")
    target = Path(corpus.document)
    if not target.is_absolute():
        target = cfg.data / target
    if not target.exists():
        raise SystemExit(f"document not found: {target}")

    if not dry_run:
        missing_models = [r.dir for r in chosen if not r.model]
        if missing_models:
            raise SystemExit(
                "bench.yaml has no model for reader(s) "
                + ", ".join(missing_models)
                + " — set `model:` for each in bench.yaml (or `model: default` "
                  "to use the archive's default model). A benchmark that runs an "
                  "unrecorded model is not a benchmark.")

    page_ids = list(pages) if pages else corpus.page_ids()
    if not page_ids:
        raise SystemExit("no pages: pass --pages or put page files in the corpus")
    plan = []
    for page in page_ids:
        n = pha_page_for(corpus, page, cfg.data)
        if n is None:
            plan.append((page, None))
            continue
        plan.append((page, n))

    todo = []
    for page, n in plan:
        if n is None:
            print(f"  [skip] {page}: cannot map to a pha page number — add it to "
                  f"document.pages in bench.yaml", file=sys.stderr)
            continue
        for r in chosen:
            if not force and corpus.reading(r, page) is not None:
                continue
            todo.append((page, n, r))
    print(f"corpus : {corpus.root}")
    print(f"document: {target}")
    print(f"readers: {', '.join(r.dir for r in chosen)}")
    print(f"pages  : {len(page_ids)}; runs needed: {len(todo)}"
          + (" (dry run)" if dry_run else ""))
    if dry_run:
        for page, n, r in todo:
            print(f"  would run {r.dir} p{n} ({page})")
        return {"planned": len(todo), "pages": len(page_ids)}

    from . import testrun

    source = f"{target.name}"
    if target.suffix.lower() in PDF_EXTS:
        source += " (PDF)"
    failures: list[dict] = []
    made = 0
    for page, n, r in todo:
        try:
            res = testrun.run_test(
                cfg, str(target), pages=1, palaeographer=corpus.palaeographer or None,
                editor="none",
                model=None if r.model in ("", "default") else r.model,
                max_tokens=max_tokens,
                verbose=False, page=n,
            )
        except KeyError as e:
            failures.append({"page": page, "reader": r.dir, "error": str(e)})
            print(f"  [FAIL] {r.dir} p{n} ({page}): {e}", file=sys.stderr, flush=True)
            continue
        if res.get("skipped"):
            print(f"\nstopped: {res.get('reason')}", file=sys.stderr)
            failures.append({"page": page, "reader": r.dir,
                             "error": str(res.get("reason"))})
            break
        hits = list(Path(res["scratch"]).glob(f"*/page-{n}-transcription.md"))
        if not hits:
            failures.append({"page": page, "reader": r.dir,
                             "error": "no transcription file produced"})
            print(f"  [FAIL] {r.dir} p{n} ({page}): no transcription file",
                  file=sys.stderr, flush=True)
            continue
        text = hits[0].read_text(encoding="utf-8")
        text = PAGE_HEADER_RE.sub("", text).strip("\n")
        if not is_valid_reading(text):
            note = " ".join(text.split())[:120] or "empty transcription"
            failures.append({"page": page, "reader": r.dir, "error": note})
            print(f"  [FAIL] {r.dir} p{n} ({page}): {note}", file=sys.stderr, flush=True)
            continue
        _write_reading(corpus, r, page, text, source, n)
        made += 1
        print(f"  [ok]   {r.dir:28s} p{n} {page} ({len(text)} chars)", flush=True)
    print(f"\n{made} reading(s) written, {len(failures)} failed")
    return {"written": made, "failures": failures, "planned": len(todo)}


# --------------------------------------------------------------------------- read a page


def page_view(corpus: Corpus, page: str) -> dict:
    """Everything known about one page, for `pha bench page` and the agent."""
    readings = []
    for r in corpus.readers:
        readings.append({"reader": r.dir, "label": r.name,
                         "text": corpus.reading(r, page)})
    comp = corpus.comparison_path(page)
    gold = corpus.gold_path(page)
    meta = corpus.gold_meta(page)
    return {
        "corpus": str(corpus.root),
        "page": page,
        "readings": readings,
        "comparison": comp.read_text(encoding="utf-8") if comp.is_file() else None,
        "gold": gold.read_text(encoding="utf-8") if gold.is_file() else None,
        "gold_status": meta.get("status"),
        "draft": corpus.is_draft(page),
        "readings_hash": corpus.readings_hash(page),
        "stale": corpus.is_stale(page),
    }


# --------------------------------------------------------------------------- scoring

SCORE_VERSION = 1

# A capitalised token that is a name, not a sentence-opening function word.
NAME_RE = re.compile(r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÿ'’\-]{2,}")
NAME_STOP = {
    "anna", "annos", "ano", "aos", "aquela", "aquele", "as", "ate", "bispo",
    "caso", "casa", "casos", "cidade", "colegio", "com", "como", "companhia",
    "confessa", "da", "das", "de", "dei", "dela", "dele", "do", "dos", "e",
    "em", "entrada", "esse", "esta", "estado", "este", "foi", "grao", "ha", "huma",
    "hum", "igreja", "irmaos", "item", "la", "mais", "mas", "mes", "ministro",
    "missao", "na", "nas", "nem", "no", "nos", "nossa", "nosso", "o", "os",
    "outra", "outro", "padre", "padres", "para", "pela", "pelo", "por",
    "predicador", "preposito", "professo", "provincia", "que", "se", "sem",
    "seu", "sua", "suas", "seus", "sob", "tem", "theologia", "todos", "um",
    "uma", "vice", "votos",
}


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def norm_diplomatic(text: str) -> str:
    """Case-folded, whitespace-collapsed — diacritics and punctuation kept."""
    return _ws(text.casefold())


def norm_modern(text: str) -> str:
    """A modern-Spanish/Portuguese comparison form.

    Folds long-s, strips diacritics, treats u/v as one letter, drops
    punctuation, case-folds. It is deliberately a *comparison* form: the gold
    text itself is never normalised in place.
    """
    t = text.replace("ſ", "s").replace("ƒ", "s")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.casefold().replace("u", "v")
    t = re.sub(r"[^0-9a-z\s]", " ", t)
    return _ws(t)


PROFILES = {"diplomatic": norm_diplomatic, "modern": norm_modern}


def cer(gold: str, reading: str, profile: str) -> float | None:
    """Character error rate of ``reading`` against ``gold`` (0 = identical).

    Uses difflib's character alignment (no dependency); a replacement counts as
    the longer of the two spans, so CER can exceed 1 when a reader adds text.
    """
    norm = PROFILES[profile]
    g = norm(gold or "")
    r = norm(reading or "")
    if not g:
        return None
    sm = difflib.SequenceMatcher(a=g, b=r, autojunk=False)
    errors = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes()
                 if tag != "equal")
    return round(errors / len(g), 6)


def name_tokens(text: str) -> list[str]:
    """Capitalised tokens that plausibly are names (heuristic, documented)."""
    out: list[str] = []
    for m in NAME_RE.finditer(text or ""):
        tok = m.group(0)
        if tok.casefold() in NAME_STOP or len(tok) < 3:
            continue
        if tok.isupper():
            continue
        out.append(tok)
    return out


def _page_text_from_entries(entries: dict[int, str]) -> str:
    return "\n".join(entries[n] for n in sorted(entries))


ILLEGIBLE_RE = re.compile(r"\[\?\]|\[illegible\]", re.I)


def _flag_form(text: str) -> str:
    """The looser form used only to decide what needs the page image.

    ``norm_modern`` plus i/j folded (the same period orthography problem as
    u/v), so "Iuan"/"Juan" is orthographic noise rather than an image question.
    Scoring keeps the stricter profile.
    """
    return norm_modern(text).replace("j", "i")


def _has_majority(sets: list[frozenset]) -> bool:
    if not sets:
        return True
    return Counter(sets).most_common(1)[0][1] >= 2


def entry_flags(readings: list[str]) -> list[str]:
    """Why an entry deserves the page image: a disputed name, a differing
    number, or an illegible mark.

    - **number**: any disagreement — a year or an age is never skimmable.
    - **name**: no two readings agree on the name tokens (a majority is not
      flagged; i/j and u/v variants count as agreement).
    - **illegible**: any ``[?]`` / ``[illegible]`` mark.
    Orthographic noise in lower-case words never flags.
    """
    texts = [r for r in readings if r]
    if len(texts) < 2:
        return []
    flags: list[str] = []
    if any(ILLEGIBLE_RE.search(t) for t in texts):
        flags.append("illegible")
    folded = [_flag_form(t) for t in texts]
    digits = [frozenset(re.findall(r"\d+", t)) for t in folded]
    if any(d != digits[0] for d in digits[1:]):
        flags.append("number")
    # presence of each candidate name in each reading's token stream, so a
    # capitalisation-only difference is agreement
    tokens = [set(t.split()) for t in folded]
    candidates = set().union(*[{_flag_form(x) for x in name_tokens(t)} for t in texts])
    name_sets = [frozenset(candidates & tok) for tok in tokens]
    if not _has_majority(name_sets):
        flags.append("name")
    return flags


def entry_texts(text: str) -> dict[int, str]:
    """The ``[n]`` entries of a gold/reference page as ``{n: text}``.

    The body of a gold entry can span several lines; the number is the
    identity, so the returned mapping is what scoring and the workbench use.
    """
    body = extract_transcription(text)
    out: dict[int, list[str]] = {}
    cur: int | None = None
    for line in body.splitlines():
        m = ENTRY_RE.match(line.strip())
        if m:
            cur = int(m.group(1))
            out[cur] = [m.group(2)]
        elif cur is not None:
            out[cur].append(line)
    return {n: "\n".join(lines).strip() for n, lines in out.items()}


def resolve_first_variant(text: str) -> str:
    """The reference merge with each ``a/b`` variant resolved to its first form.

    ``reference/`` is a variant *digest* (common words once, variants joined by
    ``/``), not a reading; scoring the digest would count every slash as an
    error. Taking the first variant gives the machine's baseline reading, which
    is what is compared to the gold.
    """
    out = []
    for tok in (text or "").split():
        out.append(tok.split("/")[0])
    return " ".join(out)


def _reading_texts(corpus: "Corpus", page: str) -> dict[str, str | None]:
    """The full text of every candidate reading, keyed like the aggregate."""
    out: dict[str, str | None] = {r.dir: corpus.reading(r, page) for r in corpus.readers}
    ref = corpus.reference_path(page)
    if ref.is_file():
        text = ref.read_text(encoding="utf-8")
        body = extract_transcription(text)
        entries = entry_texts(text)
        if entries:
            # entry markers are not part of the reading; join the resolved entries
            out["reference"] = "\n".join(
                resolve_first_variant(v) for _, v in sorted(entries.items()))
        else:
            out["reference"] = (resolve_first_variant(body)
                                if is_valid_reading(body) else None)
    return out


def score_page(corpus: "Corpus", page: str) -> dict:
    """Score one page; returns ``{scored: bool, reason?, readers?}``."""
    if not corpus.is_gold(page):
        if corpus.is_draft(page):
            return {"scored": False, "reason": "draft (not validated)"}
        return {"scored": False, "reason": "no human-corrected gold (pending)"}
    if corpus.is_stale(page):
        return {"scored": False,
                "reason": "gold is stale: the readings changed since the review"}
    gold_path = corpus.gold_path(page)
    gold_entries = entry_texts(gold_path.read_text(encoding="utf-8"))
    if not gold_entries:
        return {"scored": False, "reason": "gold has no [n] entries"}
    gold_text = _page_text_from_entries(gold_entries)

    comp = corpus.comparison_path(page)
    comp_entries = []
    if comp.is_file():
        comp_entries = parse_comparison_entries(comp.read_text(encoding="utf-8"))
    divergent = bool(comp_entries) and all(
        e.kind == "diff" and len(e.readings) == 1 for e in comp_entries)

    texts = _reading_texts(corpus, page)
    scores: dict[str, dict] = {}
    for key, reading in texts.items():
        if reading is None:
            continue
        if key == "reference" and divergent:
            continue
        row: dict = {
            "cer_diplomatic": cer(gold_text, reading, "diplomatic"),
            "cer_modern": cer(gold_text, reading, "modern"),
        }
        # entry-exact: only where the comparison and the gold share the number
        exact = total = 0
        if comp_entries:
            reader_dirs = [r.dir for r in corpus.readers]
            k = reader_dirs.index(key) + 1 if key in reader_dirs else None
            ref_entries = {}
            if key == "reference":
                ref = corpus.reference_path(page)
                if ref.is_file():
                    ref_entries = entry_texts(ref.read_text(encoding="utf-8"))
            for e in comp_entries:
                gold_entry = gold_entries.get(e.num)
                if gold_entry is None:
                    continue
                if e.kind == "all":
                    reading_entry = e.text
                elif key == "reference":
                    if e.num not in ref_entries:
                        continue
                    reading_entry = resolve_first_variant(ref_entries[e.num])
                elif k is not None and k - 1 < len(e.readings):
                    reading_entry = e.readings[k - 1]
                else:
                    continue
                total += 1
                if norm_modern(gold_entry) == norm_modern(reading_entry):
                    exact += 1
        row["entry_exact"] = [exact, total]
        tokens = name_tokens(gold_text)
        gold_norm = norm_modern(gold_text)
        found = 0
        for tok in tokens:
            if norm_modern(tok) and norm_modern(tok) in norm_modern(reading):
                found += 1
        row["names"] = [found, len(tokens)]
        row["chars"] = len(reading)
        scores[key] = row
    if not scores:
        return {"scored": False, "reason": "no readings and no reference"}
    return {"scored": True, "divergent": divergent, "readers": scores,
            "gold_entries": len(gold_entries)}


def score_corpus(corpus: "Corpus", write: bool = True,
                 corpus_id: str | None = None) -> dict:
    """Score every page with a valid gold; returns the citable results dict."""
    pages = corpus.page_ids()
    page_rows: list[dict] = []
    excluded: list[dict] = []
    for page in pages:
        row = score_page(corpus, page)
        row["page"] = page
        if row["scored"]:
            page_rows.append(row)
        else:
            excluded.append({"page": page, "reason": row["reason"]})
    keys: list[str] = [r.dir for r in corpus.readers]
    if any("reference" in p.get("readers", {}) for p in page_rows):
        keys.append("reference")
    aggregates: dict[str, dict] = {}
    for key in keys:
        rows = [p["readers"][key] for p in page_rows if key in p.get("readers", {})]
        if not rows:
            continue
        cers = [r["cer_diplomatic"] for r in rows if r["cer_diplomatic"] is not None]
        cers2 = [r["cer_modern"] for r in rows if r["cer_modern"] is not None]
        exact_ok = sum(r["entry_exact"][0] for r in rows)
        exact_n = sum(r["entry_exact"][1] for r in rows)
        names_ok = sum(r["names"][0] for r in rows)
        names_n = sum(r["names"][1] for r in rows)
        aggregates[key] = {
            "pages": len(rows),
            "cer_diplomatic": round(sum(cers) / len(cers), 6) if cers else None,
            "cer_modern": round(sum(cers2) / len(cers2), 6) if cers2 else None,
            "entry_exact": round(exact_ok / exact_n, 6) if exact_n else None,
            "entry_total": exact_n,
            "names_found": names_ok,
            "names_total": names_n,
            "names": round(names_ok / names_n, 6) if names_n else None,
            "partial": len(rows) < len(page_rows),
        }
    caveats: list[str] = []
    n = len(page_rows)
    if n == 0:
        caveats.append("no page has a valid gold: score nothing until a page "
                       "is human-corrected and not stale")
    elif n < 5:
        caveats.append(f"n_pages={n}: a screen for failure, not a ranking")
    if excluded:
        caveats.append(f"{len(excluded)} page(s) excluded: "
                       + ", ".join(f"{e['page']} ({e['reason']})" for e in excluded[:6])
                       + (" …" if len(excluded) > 6 else ""))
    partial = [k for k, a in aggregates.items() if a["partial"]]
    if partial:
        caveats.append("partial coverage: " + ", ".join(partial)
                       + " did not read every scored page; their rows are not "
                         "comparable to the full-coverage rows")
    caveats.append("names are token-level and diacritic-insensitive; they are "
                   "not entity-linked, so a variant spelling scores as a miss")
    caveats.append("CER uses difflib character alignment on the printed "
                   "normalisation profile; it is a screen, not a grade")
    results = {
        "schema": SCORE_VERSION,
        "generator": "pha bench score",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "corpus": corpus_id or corpus.root.name,
        "title": corpus.title,
        "profiles": {
            "diplomatic": "case-folded, whitespace collapsed; diacritics and "
                          "punctuation kept",
            "modern": "long-s folded, diacritics stripped, u/v merged, "
                      "punctuation dropped, case-folded",
        },
        "readers": [{"dir": r.dir, "label": r.name, "model": r.model}
                    for r in corpus.readers] + (
            [{"dir": "reference",
              "label": "machine merge (reference/, first variant)"}]
            if "reference" in aggregates else []),
        "pages": page_rows,
        "excluded": excluded,
        "aggregates": aggregates,
        "caveats": caveats,
    }
    if write:
        write_scores(corpus.root / "scores", results)
    return results


def write_scores(out_dir: Path, results: dict) -> None:
    """Write ``results.json`` + ``results.csv`` (the citable artefacts)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_scores_csv(out_dir / "results.csv", results.get("pages") or [])


def _write_scores_csv(path: Path, pages: list[dict]) -> None:
    import csv

    cols = ["page", "reader", "cer_diplomatic", "cer_modern", "entry_ok",
            "entry_total", "names_found", "names_total", "chars"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for p in pages:
            for reader, r in sorted(p["readers"].items()):
                w.writerow([p["page"], reader, r["cer_diplomatic"], r["cer_modern"],
                            r["entry_exact"][0], r["entry_exact"][1],
                            r["names"][0], r["names"][1], r["chars"]])


# --------------------------------------------------------------------------- CLI


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def cmd_score(cfg: Config, args) -> int:
    corpus = resolve_corpus(cfg, args.corpus)
    res = score_corpus(corpus, write=not args.dry_run,
                       corpus_id=_corpus_id(cfg, corpus))
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    print(f"corpus: {corpus.title}")
    print(f"scored pages: {len(res['pages'])}; excluded: {len(res['excluded'])}")
    if res["aggregates"]:
        rows = []
        for key, a in res["aggregates"].items():
            rows.append([key, str(a["pages"]),
                         "—" if a["cer_diplomatic"] is None else f"{a['cer_diplomatic']:.3f}",
                         "—" if a["cer_modern"] is None else f"{a['cer_modern']:.3f}",
                         "—" if a["entry_exact"] is None else f"{a['entry_exact']:.1%}",
                         "—" if a["names"] is None else f"{a['names']:.1%}",
                         "partial" if a["partial"] else ""])
        _print_table(rows, ["reader", "pages", "CER dipl.", "CER mod.",
                            "entry", "names", ""])
    print()
    for caveat in res["caveats"]:
        print(f"  · {caveat}")
    if not args.dry_run:
        print(f"\nwrote {corpus.root / 'scores' / 'results.json'}")
    return 0


def _corpus_id(cfg: Config, corpus: "Corpus") -> str:
    try:
        return str(corpus.root.relative_to(bench_root(cfg)))
    except ValueError:
        return corpus.root.name


def cmd_export(cfg: Config, args) -> int:
    from . import benchexport

    corpus = resolve_corpus(cfg, args.corpus)
    res = benchexport.export_site(
        corpus, cfg, out=args.out, images=not args.no_images,
        score=not args.no_score, force=args.force,
        max_image_px=args.max_image_px)
    print(f"exported {res['pages']} page(s) to {res['out']}")
    print(f"  images: {res['images']}  scores: {'yes' if res['scores'] else 'no'}")
    for w in res["warnings"]:
        print(f"  warning: {w}")
    return 0


def cmd_serve(cfg: Config, args) -> int:
    from . import benchui

    benchui.run_server(cfg, host=args.host, port=args.port, quiet=args.quiet)
    return 0


def cmd_init(cfg: Config, args) -> int:
    root = Path(args.path).expanduser()
    corpus = init_corpus(root, title=args.title,
                         palaeographer=args.palaeographer, force=args.force,
                         all_readers=args.all_readers)
    ignored = [d.name for d in sorted(root.iterdir())
               if d.is_dir() and d.name not in RESERVED_DIRS
               and d.name not in {r.dir for r in corpus.readers}
               and any(d.glob("*.md"))]
    print(f"wrote {corpus.root / 'bench.yaml'}")
    print(f"  title  : {corpus.title}")
    print(f"  readers: {', '.join(r.dir for r in corpus.readers)}")
    for r in corpus.readers:
        print(f"    - {r.dir}: model={r.model or '?'} access={r.access or '?'}")
    if corpus.palaeographer:
        print(f"  rules  : {corpus.palaeographer}")
    if ignored:
        print(f"  ignored: {', '.join(ignored)} (not in the comparison's reader "
              f"set; --all-readers includes them)")
    print("\nnext: set `document.path` (and `document.pages` if ids are not the "
          "source image stems), then `pha bench run`.")
    return 0


def cmd_list(cfg: Config, args) -> int:
    root = bench_root(cfg, args.root)
    corpora = discover(root)
    cands = candidates(root)
    if args.json:
        print(json.dumps({
            "bench_root": str(root),
            "corpora": [corpus_status(c) for c in corpora],
            "unconfigured": [str(p) for p in cands],
        }, indent=2, ensure_ascii=False))
        return 0
    print(f"bench root: {root}\n")
    if not corpora:
        print("no configured corpora (each needs a bench.yaml)")
    else:
        rows = []
        for c in corpora:
            st = corpus_status(c)
            rows.append([str(c.root.relative_to(root)) if c.root.is_relative_to(root)
                         else str(c.root), str(len(st["readers"])),
                         str(st["pages"]), str(st["gold"]), str(st["stale"])])
        _print_table(rows, ["corpus", "readers", "pages", "gold", "stale"])
    if cands:
        print("\nunconfigured (corpus-shaped, no bench.yaml):")
        for p in cands:
            print(f"  {p}   ->  pha bench init {p}")
    return 0


def cmd_status(cfg: Config, args) -> int:
    corpus = resolve_corpus(cfg, args.corpus)
    st = corpus_status(corpus)
    if args.json:
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0
    print(f"corpus : {corpus.root}")
    print(f"title  : {corpus.title}")
    print(f"readers: {', '.join(r.dir for r in corpus.readers) or '(none)'}")
    print(f"pages  : {st['pages']}  readings complete: {st['readings_complete']}  "
          f"compared: {st['compared']}  gold: {st['gold']}  "
          f"drafts: {st['drafts']}  stale: {st['stale']}")
    if args.pages or args.all:
        print()
        rows = []
        for p in st["page_status"]:
            mark = "G" if p["gold"] else ("D" if p.get("draft") else "-")
            flags = mark + ("S" if p["stale"] else "-")
            rows.append([p["page"], flags, str(p["entries"]),
                         ",".join(p["missing"]) or ""])
        _print_table(rows, ["page", "gs", "entries", "missing readings"])
        print("\nG = human-corrected gold, D = draft, S = stale (readings changed)")
    return 0


def cmd_verify(cfg: Config, args) -> int:
    corpus = resolve_corpus(cfg, args.corpus)
    expected = args.readings or (len(corpus.readers) or None)
    reports = verify_corpus(corpus, readings=expected,
                            require_images=args.require_images,
                            include_gold=not args.no_gold)
    failed = 0
    file_reports = [r for r in reports if r.path.is_file()]
    for rep in reports:
        rel = rep.path.relative_to(corpus.root) if rep.path.is_relative_to(corpus.root) else rep.path
        if rep.ok:
            print(f"ok   {rel}")
        else:
            failed += 1
            print(f"FAIL {rel}")
            for p in rep.problems:
                print(f"     - {p}")
        for w in rep.warnings:
            print(f"warn {rel}: {w}")
    ok_files = sum(1 for r in file_reports if r.ok)
    print(f"\n{ok_files}/{len(file_reports)} file(s) OK")
    return 1 if failed else 0


def cmd_compare(cfg: Config, args) -> int:
    corpus = resolve_corpus(cfg, args.corpus)
    res = build_comparison(corpus, profile=args.profile,
                           readings=args.readings, write=not args.dry_run)
    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {res['pages']} comparison page(s) in {res['out']}")
    by_profile: dict[str, int] = {}
    for s in res["stats"]:
        by_profile[s["profile"]] = by_profile.get(s["profile"], 0) + 1
    print("profiles: " + ", ".join(f"{k}={v}" for k, v in sorted(by_profile.items())))
    bad = [s for s in res["stats"] if s["problems"]]
    for s in bad[:10]:
        print(f"  warning {s['page']}: {'; '.join(s['problems'])}")
    return 0


def cmd_reference(cfg: Config, args) -> int:
    corpus = resolve_corpus(cfg, args.corpus)
    res = build_reference(corpus, readings=args.readings,
                          divergent=args.divergent or (), write=not args.dry_run)
    verb = "would write" if args.dry_run else "wrote"
    n_div = sum(1 for s in res["stats"] if s["divergent"])
    print(f"{verb} {res['pages']} reference page(s) in {res['out']}"
          + (f" ({n_div} kept unconsolidated)" if n_div else ""))
    return 0


def cmd_run(cfg: Config, args) -> int:
    corpus = resolve_corpus(cfg, args.corpus)
    pages = None
    if args.pages:
        pages = [x.strip() for x in args.pages.replace(",", " ").split() if x.strip()]
    readers = None
    if args.readers and args.readers != "all":
        readers = [x.strip() for x in args.readers.split(",") if x.strip()]
    res = harvest(corpus, cfg, pages=pages, readers=readers, force=args.force,
                  dry_run=args.dry_run, max_tokens=args.max_tokens)
    return 1 if res.get("failures") else 0


def cmd_page(cfg: Config, args) -> int:
    corpus = resolve_corpus(cfg, args.corpus)
    page = args.page
    if page not in corpus.page_ids() and not corpus.comparison_path(page).is_file():
        raise SystemExit(f"unknown page {page!r} in {corpus.root}")
    view = page_view(corpus, page)
    if args.json:
        print(json.dumps(view, indent=2, ensure_ascii=False))
        return 0
    print(f"page {page} — gold: {view['gold_status'] or '(none)'}"
          + ("  STALE" if view["stale"] else ""))
    print(f"readings hash: {view['readings_hash']}")
    for r in view["readings"]:
        text = r["text"] or ""
        print(f"\n--- {r['reader']} ({len(text)} chars) ---")
        print(text[:2000] + ("\n…" if len(text) > 2000 else ""))
    if view["comparison"]:
        print("\n--- comparison ---\n" + view["comparison"][:3000])
    if view["gold"]:
        print("\n--- gold ---\n" + view["gold"][:3000])
    return 0


COMMANDS = {
    "serve": cmd_serve,
    "init": cmd_init,
    "list": cmd_list,
    "status": cmd_status,
    "verify": cmd_verify,
    "compare": cmd_compare,
    "reference": cmd_reference,
    "score": cmd_score,
    "export": cmd_export,
    "run": cmd_run,
    "page": cmd_page,
}


def run_command(cfg: Config, args) -> int:
    fn = COMMANDS.get(getattr(args, "bench_cmd", ""))
    if fn is None:
        raise SystemExit(f"unknown bench command: {getattr(args, 'bench_cmd', '')!r}")
    return fn(cfg, args) or 0

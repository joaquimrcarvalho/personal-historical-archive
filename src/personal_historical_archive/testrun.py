"""`pha test` — run the pipeline (transcription -> editing -> encoding) on a
SAMPLE of a document's pages to sanity-check a configuration.

The point of `test` is to try a configuration (the `pha.yaml` sidecar plus the
palaeographer / editor / encoder / model / prompt choices) on a handful of pages
and look at the output BEFORE committing to a full `pha scan` / `pha edit` /
`pha encode` over the whole archive. It is therefore completely isolated:

- It renders only the sampled pages.
- It NEVER touches the archive DB, library, or renders folders. Every page image
  and every stage output is written to a throwaway scratch directory under the
  archive (`<archive_dir>/.pha-test/<doc>-<timestamp>/`), together with a
  human-readable `report.md` and a machine-readable `report.json`.
- It runs the SAME resolution the real pipeline uses (`resolve_sidecar`,
  `resolve_palaeographer_id`, `resolve_editor_id`, `encoder_files_for`,
  `resolve_prompt`), so what you see here is exactly what a real pass would do —
  unless you override it with the `--palaeographer/--editor/--encoder/--model/
  --prompt/--temperature/--max-tokens` flags.

`pha test --show` re-prints the most recent saved report without running the
models again, so results are easy to review while you tune a prompt/model.
"""

from __future__ import annotations

import dataclasses
import json
import random
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, Editor, Encoder, Palaeographer
from .extract import (
    build_page_prompt,
    compose_prompts,
    encoder_file_named,
    encoder_files_for,
    is_supported,
    page_count,
    render_document,
    resolve_editor_id,
    resolve_encoder_id,
    resolve_palaeographer_id,
    resolve_prompt,
)
from .ingest import (
    _acquire_scan_lock,
    _expand_records,
    _parse_json_array,
    _release_scan_lock,
    discover,
    transcribe_page,
)
from .model_client import ModelClient
from .sidecar import effective_render, resolve_sidecar

NULL_EDITORS = ("null", "passthrough")
NO_EDITOR = ("none", "")


# --------------------------------------------------------------------------- small result structures

@dataclass
class EditorPlan:
    """How the editor stage resolves for one document."""
    kind: str                     # "none" | "null" | "editor"
    editor: Editor | None = None  # the resolved Editor when kind == "editor"
    id: str | None = None
    source: str | None = None


@dataclass
class DocResult:
    """Per-document testing result + what it resolved to."""
    path: str
    slug: str
    total_pages: int
    selected: list[int] = field(default_factory=list)
    palaeographer: dict = field(default_factory=dict)
    editor: dict = field(default_factory=dict)
    encoders: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)   # [{page, transcription_chars, edited_chars, edited}]
    records: list[dict] = field(default_factory=list)  # [{encoder, count}]
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers

def _select_pages(total: int, n: int, randomize: bool, seed: int | None) -> list[int]:
    """Choose which 1-based page numbers to sample.

    `n` is clamped to [1, total]; `randomize` draws a uniform sample (seeded
    when `seed` is given, so a run is reproducible); otherwise pages 1..n.
    """
    if total <= 0:
        return []
    n = max(1, min(int(n or 1), total))
    if randomize:
        rng = random.Random(seed)
        return sorted(rng.sample(range(1, total + 1), n))
    return list(range(1, n + 1))


def _resolve_target(cfg: Config, target: str | None) -> list[Path]:
    """Turn `target` (a dropbox-relative or absolute path) into document units.

    A file that is itself a document (PDF / image) is a single unit; a
    directory is discovered like a scan `--path`. `None` falls back to the
    whole dropbox (so `pha test` with no target tests a sample of everything).
    """
    if not target:
        return discover(cfg.dropbox, cfg.dir_documents)
    t = Path(target)
    if not t.is_absolute():
        t = cfg.dropbox / t
    t = t.resolve()
    if cfg.dropbox.resolve() not in t.parents and t != cfg.dropbox.resolve() and not t.exists():
        print(f"  target not found: {target}", flush=True)
        return []
    if t.is_file() and is_supported(t.name):
        return [t]
    if t.is_dir():
        return discover(cfg.dropbox, cfg.dir_documents, root=t)
    return discover(cfg.dropbox, cfg.dir_documents, root=t)


def _file_dir(path: Path) -> Path:
    return path if path.is_dir() else path.parent


def _doc_stem(path: Path) -> str:
    return path.stem if not path.is_dir() else path.name


def _doc_slug(path: Path) -> str:
    """A safe, human-readable directory name for the document."""
    stem = _doc_stem(path)
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in stem).strip().replace(" ", "_")
    return safe or "doc"


def _resolve_palaeographer(
    cfg: Config, path: Path, sc, pal_override: str | None,
    model_override: str | None, temperature: float | None, max_tokens: int | None,
) -> tuple[Palaeographer, str, str | None]:
    """Return (palaeographer, pal_id, source) with overrides applied."""
    file_dir = _file_dir(path)
    model_id = None
    if sc.palaeographer:
        pal_id = sc.palaeographer.rules
        model_id = sc.palaeographer.model
        source = f"{sc.source} (pha.yaml)"
    else:
        pal_id, source = resolve_palaeographer_id(_doc_stem(path), file_dir, cfg.dropbox)
        if not source:
            source = "config default (vision.palaeographer)"
    if pal_override is not None:
        pal_id = pal_override
        source = f"flag:{pal_override}"
    pal = cfg.get_palaeographer(pal_id) if pal_id else cfg.get_palaeographer()
    pal = cfg.resolve_model(pal, model_override or model_id)
    if temperature is not None:
        pal = dataclasses.replace(pal, temperature=temperature)
    if max_tokens is not None:
        pal = dataclasses.replace(pal, max_tokens=max_tokens)
    return pal, pal_id, source


def _resolve_editor(
    cfg: Config, path: Path, sc, ed_override: str | None,
    model_override: str | None, temperature: float | None, max_tokens: int | None,
) -> EditorPlan:
    """Resolve the editor plan (none / null-passthrough / a real Editor)."""
    file_dir = _file_dir(path)
    editor_model_id = None
    source: str | None = None
    if ed_override is not None:
        ed_id = ed_override
        source = f"flag:{ed_override}"
    elif sc.editor_set:
        if sc.editor is None:
            ed_id = None
            source = f"{sc.source} (pha.yaml)"
        else:
            ed_id = sc.editor.rules
            editor_model_id = sc.editor.model
            source = f"{sc.source} (pha.yaml)"
    else:
        ed_id, source = resolve_editor_id(_doc_stem(path), file_dir, cfg.dropbox)
    if not ed_id or ed_id in NO_EDITOR:
        return EditorPlan(kind="none", id=None, source=source)
    if ed_id in NULL_EDITORS:
        return EditorPlan(kind="null", id=ed_id, source=source)
    editor = cfg.get_editor(ed_id)
    editor = cfg.resolve_model(editor, model_override or editor_model_id)
    if temperature is not None:
        editor = dataclasses.replace(editor, temperature=temperature)
    if max_tokens is not None:
        editor = dataclasses.replace(editor, max_tokens=max_tokens)
    return EditorPlan(kind="editor", editor=editor, id=ed_id, source=source)


def _resolve_encoders(
    cfg: Config, path: Path, sc, enc_override: str | None,
    model_override: str | None, temperature: float | None, max_tokens: int | None,
) -> list[tuple[Encoder, Path | None, str, str]]:
    """Resolve the encoders for a document: [(encoder, enc_file, id, source)].

    `--encoder` selects ONE encoder (a collection-local `encoders/<name>.md`
    file wins over a global id). Without it the resolution is: the pha.yaml
    `encoders:` list (collection-local or global), else the collection-local
    `encoders/` directory files, else the global encoder id fallback.
    """
    file_dir = _file_dir(path)
    out: list[tuple[Encoder, Path | None, str, str, str | None]] = []  # (encoder, enc_file, id, source, model_hint)

    def _load(spec_id: str) -> Encoder | None:
        local = encoder_file_named(spec_id, file_dir, cfg.dropbox)
        if local is not None:
            enc = cfg.encoder_from_file(local)
            if enc is not None:
                return enc
        if spec_id in cfg.encoders:
            return cfg.get_encoder(spec_id)
        return None

    if enc_override is not None:
        enc = _load(enc_override)
        if enc is None:
            raise KeyError(f"unknown encoder {enc_override!r}")
        out.append((enc, encoder_file_named(enc_override, file_dir, cfg.dropbox),
                    enc_override, f"flag:{enc_override}", None))
    elif sc.encoders:
        for spec in sc.encoders:
            enc = _load(spec.rules)
            if enc is not None:
                out.append((enc, encoder_file_named(spec.rules, file_dir, cfg.dropbox),
                            spec.rules, f"{sc.source} (pha.yaml)", spec.model))
    else:
        locals_found = encoder_files_for(_doc_stem(path), file_dir, cfg.dropbox)
        if locals_found:
            for p in locals_found:
                enc = cfg.encoder_from_file(p)
                if enc is not None:
                    out.append((enc, p, p.stem, str(p), None))
        else:
            enc_id, src = resolve_encoder_id(_doc_stem(path), file_dir, cfg.dropbox)
            if enc_id and enc_id in cfg.encoders:
                out.append((cfg.get_encoder(enc_id), None, enc_id, src or "config default", None))

    resolved: list[tuple[Encoder, Path | None, str, str]] = []
    for enc, p, eid, src, model_hint in out:
        enc = cfg.resolve_model(enc, model_override or model_hint)
        if temperature is not None:
            enc = dataclasses.replace(enc, temperature=temperature)
        if max_tokens is not None:
            enc = dataclasses.replace(enc, max_tokens=max_tokens)
        resolved.append((enc, p, eid, src))
    return resolved


def _page_units(
    cfg: Config, path: Path, selected: list[int], scratch_renders: Path,
) -> list[tuple[int, Path, str | None]]:
    """Render the selected pages and return [(page_no, render_path, source_name)]."""
    sidecar = resolve_sidecar(cfg.dropbox, _file_dir(path))
    render_dpi, max_image_px, jpeg_quality = effective_render(cfg, sidecar)
    units: list[tuple[int, Path, str | None]] = []
    if path.is_dir():
        images = [f for f in sorted(path.iterdir())
                  if f.is_file() and is_supported(f.name) and not f.name.startswith(".")]
        for idx, img in enumerate(images, start=1):
            if idx not in selected:
                continue
            out = render_document(img, scratch_renders, render_dpi, max_image_px, jpeg_quality,
                                  prefix=img.stem)[0]
            units.append((idx, out, img.stem))
    else:
        paths = render_document(path, scratch_renders, render_dpi, max_image_px, jpeg_quality,
                                pages=set(selected))
        for p in paths:
            page_no = int(p.stem[1:])
            units.append((page_no, p, None))
    units.sort(key=lambda u: u[0])
    return units


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _pf_repr(stage) -> dict:
    return {
        "id": getattr(stage, "id", None),
        "model": getattr(stage, "model", None),
        "model_ref": getattr(stage, "model_ref", ""),
        "description": getattr(stage, "description", ""),
        "temperature": getattr(stage, "temperature", None),
        "max_tokens": getattr(stage, "max_tokens", None),
        "thinking": getattr(stage, "thinking", None),
    }


def _editor_kwargs(plan: EditorPlan) -> dict:
    if plan.kind != "editor":
        return {"kind": plan.kind, "id": plan.id, "source": plan.source}
    return {"kind": plan.kind, **{k: v for k, v in _pf_repr(plan.editor).items() if v is not None},
            "source": plan.source}


# --------------------------------------------------------------------------- one document

def _test_doc(
    cfg: Config, path: Path, scratch: Path, scratch_renders: Path,
    pages: int, randomize: bool, seed: int | None,
    pal_override: str | None, ed_override: str | None, enc_override: str | None,
    model: str | None, prompt: str | None, temperature: float | None, max_tokens: int | None,
    verbose: bool,
) -> DocResult:
    file_dir = _file_dir(path)
    sidecar = resolve_sidecar(cfg.dropbox, file_dir, stem=(path.stem if not path.is_dir() else None))
    out_dir = scratch / _doc_slug(path)

    # --- total page count ---------------------------------------------------
    if path.is_dir():
        total = len([f for f in sorted(path.iterdir())
                     if f.is_file() and is_supported(f.name) and not f.name.startswith(".")])
    else:
        total = page_count(path)
    selected = _select_pages(total, pages, randomize, seed)

    # --- resolve stages -----------------------------------------------------
    pal, pal_id, pal_src = _resolve_palaeographer(
        cfg, path, sidecar, pal_override, model, temperature, max_tokens)
    plan = _resolve_editor(cfg, path, sidecar, ed_override, model, temperature, max_tokens)
    encoders = _resolve_encoders(cfg, path, sidecar, enc_override, model, temperature, max_tokens)

    dr = DocResult(path=str(path), slug=_doc_slug(path), total_pages=total, selected=selected)
    dr.palaeographer = {"id": pal.id, "model": pal.model, "model_ref": pal.model_ref,
                        "description": pal.description, "temperature": pal.temperature,
                        "max_tokens": pal.max_tokens, "source": pal_src}
    dr.editor = _editor_kwargs(plan)

    # --- palaeographer prompt ----------------------------------------------
    pal_prompt, prompt_source = resolve_prompt(
        _doc_stem(path), file_dir, cfg.dropbox, cfg.prompts, explicit=prompt, kind="prompt")
    composed = compose_prompts(pal.prompt_text, pal_prompt)
    dr.palaeographer["prompt_source"] = prompt_source
    _write(out_dir / "prompt-transcription.md",
           f"# Effective transcription prompt (palaeographer {pal.id})\n"
           f"model: {pal.model}\nmodel_ref: {pal.model_ref}\n"
           f"temperature: {pal.temperature} max_tokens: {pal.max_tokens}\n"
           f"source: {pal_src} / prompt file: {prompt_source}\n\n---\n\n{composed}")

    # --- render + transcribe ------------------------------------------------
    units = _page_units(cfg, path, selected, scratch_renders)
    transcriptions: dict[int, str] = {}
    vision_client: ModelClient | None = None
    try:
        if (pal.engine or "").strip().lower() in ("", "llm"):
            vision_client = ModelClient(pal.base_url, timeout_s=pal.timeout_s,
                                        api_key=pal.api_key, api_style=pal.api_style)
        for page_no, img, source_name in units:
            if verbose:
                print(f"  page {page_no}/{total}: transcribing ...", flush=True)
            prompt_txt = build_page_prompt(composed, path.name, page_no, total)
            try:
                raw = transcribe_page(vision_client, pal, prompt_txt, img,
                                      source=path, page_no=page_no, total=total)
            except Exception as e:  # noqa: BLE001 - a test must survive one bad page/engine
                dr.errors.append(f"page {page_no} transcription failed: {e}")
                _write(out_dir / f"page-{page_no}-transcription.md", f"ERROR: {e}\n")
                continue
            transcriptions[page_no] = raw
            _write(out_dir / f"page-{page_no}-transcription.md", f"# Page {page_no} — raw transcription\n\n{raw}")
    finally:
        if vision_client is not None:
            vision_client.close()

    # --- editor stage -------------------------------------------------------
    edited: dict[int, str] = {}
    if plan.kind == "editor":
        _write(out_dir / "prompt-edit.md",
               f"# Effective editor prompt ({plan.editor.id})\n"
               f"model: {plan.editor.model}\nmodel_ref: {plan.editor.model_ref}\n"
               f"temperature: {plan.editor.temperature} max_tokens: {plan.editor.max_tokens}\n"
               f"source: {plan.source}\n\n---\n\n{plan.editor.prompt_text}")
        eclient = ModelClient(plan.editor.base_url, timeout_s=plan.editor.timeout_s,
                              api_key=plan.editor.api_key, api_style=plan.editor.api_style)
        try:
            for page_no, raw in sorted(transcriptions.items()):
                if verbose:
                    print(f"  page {page_no}/{total}: editing ...", flush=True)
                edit_prompt = (
                    f"{plan.editor.prompt_text}\n\n"
                    f"Document: {path.name}\nPage: {page_no} of {total}\n\n"
                    f"Transcription to edit:\n{raw}"
                )
                try:
                    out = eclient.chat_text(plan.editor.model, edit_prompt,
                                            plan.editor.temperature, plan.editor.max_tokens,
                                            thinking=plan.editor.thinking)
                    edited[page_no] = out
                    _write(out_dir / f"page-{page_no}-edited.md",
                           f"# Page {page_no} — edited\n\n{out}")
                except Exception as e:  # noqa: BLE001 - a test must survive one bad page
                    dr.errors.append(f"page {page_no} editing failed: {e}")
                    _write(out_dir / f"page-{page_no}-edited.md", f"ERROR: {e}\n")
        finally:
            eclient.close()
    elif plan.kind == "null":
        edited = dict(transcriptions)  # verbatim passthrough
        for page_no, raw in sorted(transcriptions.items()):
            _write(out_dir / f"page-{page_no}-edited.md",
                   f"# Page {page_no} — edited (passthrough, no model call)\n\n{raw}")
    # else "none": no editing stage

    # per-page result rows
    for page_no in sorted(set(transcriptions | edited)):
        dr.pages.append({
            "page": page_no,
            "transcription_chars": len(transcriptions.get(page_no) or ""),
            "edited": page_no in edited,
            "edited_chars": len(edited.get(page_no) or ""),
        })

    # --- encoder stage ------------------------------------------------------
    texts = [(n, (edited.get(n) or transcriptions.get(n) or "").strip())
             for n in sorted(transcriptions.keys())]
    for enc, enc_file, eid, src in encoders:
        dr.encoders.append({"id": eid, "model": enc.model, "model_ref": enc.model_ref,
                            "source": src, "temperature": enc.temperature,
                            "max_tokens": enc.max_tokens})
        if not texts:
            continue
        if verbose:
            print(f"  encoding {len(texts)} page(s) with {eid} ...", flush=True)
        # compose the encoder prompt (base + detection rules + langextract)
        if enc_file is not None:
            from .extract import resolve_encoder_prompt
            doc_prompt, _s = resolve_encoder_prompt(enc_file, "encoder.prompt")
            lx_prompt, _lx = resolve_encoder_prompt(enc_file, "encoder.prompt.langextract")
        else:
            doc_prompt, _s = resolve_prompt(_doc_stem(path), file_dir, cfg.dropbox,
                                            cfg.prompts, kind="encoder.prompt")
            lx_prompt, _lx = resolve_prompt(_doc_stem(path), file_dir, cfg.dropbox,
                                            cfg.prompts, kind="encoder.prompt.langextract")
        base = compose_prompts(enc.prompt_text, doc_prompt)
        if lx_prompt:
            base = compose_prompts(base, lx_prompt)
        _write(out_dir / f"prompt-encode-{eid}.md",
               f"# Effective encoder prompt ({eid})\nmodel: {enc.model}\n"
               f"model_ref: {enc.model_ref}\ntemperature: {enc.temperature} "
               f"max_tokens: {enc.max_tokens}\nsource: {src}\n\n---\n\n{base}")
        block = "\n\n".join(f"--- page {pno} ---\n{t}" for pno, t in texts)
        prompt_txt = (
            f"{base}\n\n"
            f"Document: {path.name}\nPages: {texts[0][0]}-{texts[-1][0]}\n\n{block}"
        )
        eclient = ModelClient(enc.base_url, timeout_s=enc.timeout_s, api_key=enc.api_key,
                              api_style=enc.api_style)
        try:
            parsed: list | None = None
            for attempt in range(3):
                out = eclient.chat_text(enc.model, prompt_txt, enc.temperature,
                                        max(8192, enc.max_tokens), thinking=enc.thinking)
                parsed = _parse_json_array(out)
                if parsed is not None or not out.strip():
                    break
            records = _expand_records(parsed or [])
            _write(out_dir / f"records-{eid}.json",
                   json.dumps({"document": str(path), "encoder": eid,
                               "records": records}, ensure_ascii=False, indent=2))
            _write(out_dir / f"concatenated-{eid}.md", block)
            dr.records.append({"encoder": eid, "count": len(records)})
        except Exception as e:  # noqa: BLE001 - a test must survive one bad encoder/model
            dr.errors.append(f"encoding ({eid}) failed: {e}")
        finally:
            eclient.close()

    # per-doc config + report
    return dr


# --------------------------------------------------------------------------- reports

def _report_markdown(summary: dict, documents: list[DocResult]) -> str:
    lines: list[str] = []
    lines.append("# pha test report")
    lines.append(f"target: `{summary['target']}`")
    lines.append(f"pages sampled: {summary['pages']} ({'random' if summary['random'] else 'first N'})")
    lines.append(f"scratch: `{summary['scratch']}`")
    lines.append("")
    for dr in documents:
        lines.append(f"## {dr.path}")
        lines.append(f"- total pages: {dr.total_pages}; sampled: {', '.join(str(p) for p in dr.selected)}")
        p = dr.palaeographer
        lines.append(f"- palaeographer: **{p.get('id')}** ({p.get('model') or p.get('description') or ''}) "
                     f"@ {p.get('model_ref') or 'inline'} | "
                     f"temperature {p.get('temperature')} max_tokens {p.get('max_tokens')} "
                     f"| {p.get('source')}")
        if p.get("prompt_source"):
            lines.append(f"  - transcription prompt: {p['prompt_source']}")
        e = dr.editor
        if e.get("kind") == "editor":
            lines.append(f"- editor: **{e.get('id')}** ({e.get('model') or ''}) "
                         f"@ {e.get('model_ref') or 'inline'} | "
                         f"temperature {e.get('temperature')} max_tokens {e.get('max_tokens')} "
                         f"| {e.get('source')}")
        elif e.get("kind") == "null":
            lines.append(f"- editor: passthrough (verbatim copy) | {e.get('source')}")
        else:
            lines.append(f"- editor: none | {e.get('source') or 'no editor configured'}")
        if dr.encoders:
            lines.append(f"- encoders: {', '.join(x['id'] for x in dr.encoders)} "
                         f"(models: {', '.join(x['model'] for x in dr.encoders)})")
        else:
            lines.append("- encoders: none")
        if dr.pages:
            lines.append("")
            lines.append("| page | transcription chars | edited | edited chars |")
            lines.append("|------|--------------------|--------|--------------|")
            for row in dr.pages:
                lines.append(f"| {row['page']} | {row['transcription_chars']} | "
                             f"{'yes' if row['edited'] else 'no'} | {row['edited_chars']} |")
        if dr.records:
            lines.append("")
            rec_line = ", ".join(f"{r['encoder']}={r['count']}" for r in dr.records)
            lines.append(f"records: {rec_line}")
        if dr.errors:
            lines.append("")
            lines.append("errors:")
            for err in dr.errors:
                lines.append(f"- {err}")
        lines.append("")
    lines.append(f"output files: `{summary['scratch']}/`")
    return "\n".join(lines)


def _latest_report_dir(cfg: Config, target: str | None) -> Path | None:
    """The most recent .pha-test run dir, optionally filtered by an unpinned
    substring of the document path (target)."""
    root = cfg.data / ".pha-test"
    if not root.is_dir():
        return None
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / "report.json").is_file()]
    if not dirs:
        return None
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    if target:
        for d in dirs:
            try:
                payload = json.loads((d / "report.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            if any(target.lower() in (doc.get("path") or "").lower()
                   for doc in payload.get("documents", [])):
                return d
        # fall back: newest dir whose name contains the target slug
        for d in dirs:
            if target.lower() in d.name.lower():
                return d
        return None
    return dirs[0]


def show_latest(cfg: Config, target: str | None = None) -> int:
    """Print the most recent `pha test` report (or raise a clear error)."""
    d = _latest_report_dir(cfg, target)
    if d is None:
        msg = "no pha test report found"
        if target:
            msg += f" matching {target!r}"
        print(msg, file=sys.stderr)
        return 1
    md = d / "report.md"
    if md.exists():
        print(md.read_text(encoding="utf-8"))
    else:
        print(json.dumps(json.loads((d / "report.json").read_text(encoding="utf-8")),
                         ensure_ascii=False, indent=2))
    print(f"\n(scratch: {d})")
    return 0


# --------------------------------------------------------------------------- listing + cleaning

def _run_dir(cfg: Config) -> Path:
    return cfg.data / ".pha-test"


def list_runs(cfg: Config) -> list[dict]:
    """All `pha test` runs under the archive's `.pha-test/`, newest first, with
    a little metadata from each report.json."""
    root = _run_dir(cfg)
    if not root.is_dir():
        return []
    runs: list[dict] = []
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        rj = d / "report.json"
        if not rj.is_file():
            continue
        try:
            payload = json.loads(rj.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        runs.append({
            "dir": d,
            "name": d.name,
            "mtime": d.stat().st_mtime,
            "target": payload.get("target"),
            "pages": payload.get("pages"),
            "documents": len(payload.get("documents", [])),
            # all document paths, for a substring match on `--clean`/`--list`
            "paths": [doc.get("path") for doc in payload.get("documents", [])],
        })
    return runs


def _run_matches(run: dict, target: str) -> bool:
    t = target.lower()
    if t in run["name"].lower():
        return True
    if run["target"] and t in run["target"].lower():
        return True
    return any(p and t in p.lower() for p in run.get("paths", []))


def clean_runs(cfg: Config, target: str | None = None, dry_run: bool = False) -> dict:
    """Remove `pha test` scratch runs (all of them, or those matching `target`).

    Only ever deletes directories under the archive's `.pha-test/` — never the
    archive itself. With `dry_run` nothing is deleted; the would-be-removed
    paths are still reported."""
    root = _run_dir(cfg)
    if not root.is_dir():
        return {"removed": 0, "cleaned": [], "root": str(root)}
    runs = list_runs(cfg)
    if target:
        runs = [r for r in runs if _run_matches(r, target)]
    cleaned: list[str] = []
    for r in runs:
        cleaned.append(str(r["dir"]))
        if not dry_run:
            shutil.rmtree(r["dir"], ignore_errors=True)
    if not dry_run:
        try:
            if not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass
    return {"removed": len(runs), "cleaned": cleaned, "root": str(root)}


# --------------------------------------------------------------------------- main entry

def run_test(
    cfg: Config,
    target: str | None,
    pages: int = 3,
    randomize: bool = False,
    seed: int | None = None,
    palaeographer: str | None = None,
    editor: str | None = None,
    encoder: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    verbose: bool = True,
) -> dict:
    """Run the pipeline on a sample of pages and write the output to a scratch dir.

    Returns a summary dict. Raises KeyError for an unknown override id; other
    errors are captured per document in `DocResult.errors`.
    """
    if not _acquire_scan_lock(cfg):
        return {"skipped": True, "reason": "another scan/edit job is running (one local model at a time)"}
    try:
        cfg.ensure_dirs()
        docs = _resolve_target(cfg, target)
        if not docs:
            return {"skipped": True, "reason": "no documents matched the target"}

        stamped = time.strftime("%Y%m%d-%H%M%S")
        scratch = cfg.data / ".pha-test" / f"{_doc_slug(docs[0])}-{stamped}"
        scratch.mkdir(parents=True, exist_ok=True)
        scratch_renders = scratch / "renders"

        documents: list[DocResult] = []
        for doc_path in docs:
            dr = _test_doc(cfg, doc_path, scratch, scratch_renders, pages, randomize, seed,
                           palaeographer, editor, encoder, model, prompt, temperature, max_tokens,
                           verbose)
            documents.append(dr)

        summary: dict = {
            "target": target or "(whole dropbox)",
            "scratch": str(scratch),
            "pages": pages,
            "random": randomize,
            "seed": seed,
            "documents": [dataclasses.asdict(dr) for dr in documents],
        }
        _write(scratch / "report.json", json.dumps(summary, ensure_ascii=False, indent=2))
        _write(scratch / "report.md", _report_markdown(summary, documents))
        return summary
    finally:
        _release_scan_lock(cfg)

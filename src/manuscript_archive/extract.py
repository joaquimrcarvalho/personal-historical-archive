from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # pymupdf (fitz API)

SUPPORTED_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

DEFAULT_PROMPT = """You are a scholarly transcriber working with historical manuscripts in Western
European languages (Latin, Portuguese, French, Italian, Spanish, German, English).

Transcribe the page faithfully:

1. Keep the original language and spelling. When you are confident about an
   abbreviation, expand it in square brackets, e.g. "dñs" -> "d[omi]n[u]s".
2. Preserve paragraphs, headings, and line breaks where they matter.
   Label marginal notes as `[margin: ...]` and interlinear additions as
   `[interlinear: ...]`.
3. Mark anything you cannot read: `[illegible]` for a few characters,
   `[illegible: N words]` for larger passages, `[damaged]`, `[hole]`, `[seal]`.
4. Never invent text you cannot see. If the page is blank, write `[blank page]`.

After the transcription add a section headed `## Notes` (write the notes in English):

- Language: the language(s) of the page
- Script: e.g. secretary hand, humanist, cursive, print
- Date clues: any explicit dates or datable references you can see
- Named entities: people, places, institutions mentioned
- Content summary: 2-4 sentences describing what this page is about
- Foliation / archival marks: page numbers, folio numbers, stamps, shelfmarks

Output format: Markdown, starting with `## Transcription`.
"""


def is_supported(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTS


def render_document(
    path: Path, out_dir: Path, dpi: int = 200, max_px: int = 1800, jpeg_quality: int = 88
) -> list[Path]:
    """Render every page of a PDF (or a single image) to JPEG files, returning their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    with fitz.open(str(path)) as doc:
        for i, page in enumerate(doc):
            rect = page.rect
            zoom = dpi / 72.0
            if max_px and max(rect.width, rect.height) * zoom > max_px:
                zoom = max_px / max(rect.width, rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            out = out_dir / f"p{i + 1:03d}.jpg"
            out.write_bytes(pix.tobytes("jpeg", jpg_quality=jpeg_quality))
            images.append(out)
    return images


def page_count(path: Path) -> int:
    with fitz.open(str(path)) as doc:
        return len(doc)


def prompt_candidates(
    stem: str,
    file_dir: Path,
    dropbox: Path,
    prompts_dir: Path,
    explicit: str | None = None,
) -> list[Path]:
    """Candidate prompt files in resolution order.

    Order:
      1. explicit prompt file (--prompt flag)
      2. next to the document: <stem>.prompt.md / <stem>.pdf.prompt.md
      3. directory chain (nearest first):
           <dir>/prompt.md, <dir>/<dirname>.prompt.md,
           <parent>/<dirname>.prompt.md (sidecar next to the directory),
         walking up to the dropbox root — this is how a collection-level prompt
         (e.g. dropbox/collections/COLX/prompt.md) applies to everything under
         it, and how a document-directory of images gets one prompt for all pages
      4. prompts dir: <stem>.prompt.md
    """
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = prompts_dir / explicit
        return [p]
    cands: list[Path] = [
        file_dir / f"{stem}.prompt.md",
        file_dir / f"{stem}.pdf.prompt.md",
    ]
    d = file_dir
    while True:
        cands.append(d / "prompt.md")
        cands.append(d / f"{d.name}.prompt.md")
        if d != dropbox and dropbox in d.parents:
            cands.append(d.parent / f"{d.name}.prompt.md")
        if d == dropbox or dropbox not in d.parents:
            break
        d = d.parent
    cands.append(prompts_dir / f"{stem}.prompt.md")
    return cands


def resolve_prompt(
    stem: str,
    file_dir: Path,
    dropbox: Path,
    prompts_dir: Path,
    explicit: str | None = None,
) -> tuple[str, str]:
    """Return (prompt_text, source) for a document at file_dir.

    Resolution order: explicit --prompt, file-sidecar, directory/collection
    chain (nearest first), prompts/<stem>.prompt.md, prompts/default_prompt.md,
    built-in default.
    """
    for cand in prompt_candidates(stem, file_dir, dropbox, prompts_dir, explicit):
        if cand.exists():
            return cand.read_text(), str(cand)
    default = prompts_dir / "default_prompt.md"
    if default.exists():
        return default.read_text(), str(default)
    return DEFAULT_PROMPT, "builtin"


def compose_prompts(palaeographer_prompt: str, doc_prompt: str) -> str:
    """The palaeographer's base prompt comes BEFORE the document prompt."""
    pal = (palaeographer_prompt or "").strip()
    if not pal:
        return doc_prompt
    return f"{pal}\n\n---\n\n{doc_prompt}"


def build_page_prompt(file_prompt: str, filename: str, page_no: int, total: int) -> str:
    return f"Document: {filename}\nPage: {page_no} of {total}\n\n{file_prompt}"

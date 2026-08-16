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


def resolve_prompt(
    stem: str,
    dropbox: Path,
    prompts_dir: Path,
    explicit: str | None = None,
) -> tuple[str, str]:
    """Return (prompt_text, source) for a file.

    Resolution order:
      1. explicit prompt file (--prompt flag)
      2. <stem>.prompt.md next to the document in the dropbox
      3. <stem>.prompt.md in the prompts dir
      4. prompts/default_prompt.md
      5. built-in default
    """
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = prompts_dir / explicit
        if p.exists():
            return p.read_text(), str(p)
        raise FileNotFoundError(f"Explicit prompt file not found: {explicit}")
    for cand in (
        dropbox / f"{stem}.prompt.md",
        dropbox / f"{stem}.pdf.prompt.md",
        prompts_dir / f"{stem}.prompt.md",
    ):
        if cand.exists():
            return cand.read_text(), str(cand)
    default = prompts_dir / "default_prompt.md"
    if default.exists():
        return default.read_text(), str(default)
    return DEFAULT_PROMPT, "builtin"


def build_page_prompt(file_prompt: str, filename: str, page_no: int, total: int) -> str:
    return f"Document: {filename}\nPage: {page_no} of {total}\n\n{file_prompt}"

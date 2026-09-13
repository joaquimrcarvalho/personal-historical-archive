from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_DOTENV: dict[str, str] | None = None


def _expand(value: str) -> str:
    """Expand ${ENV} and ${ENV:-default} in config strings.

    Resolution order for a variable:
      1. the real environment,
      2. a gitignored .env file in the project root,
      3. the macOS Keychain (service 'pha', account = variable name),
      4. the ${VAR:-default} fallback, or ''.
    """

    def repl(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        v = os.environ.get(name)
        if not v:
            v = _dotenv().get(name, "")
        if not v:
            v = _secret_get(name)
        return v or default or ""

    return _ENV_RE.sub(repl, value)


def _dotenv() -> dict[str, str]:
    global _DOTENV
    if _DOTENV is None:
        _DOTENV = {}
        p = find_project_root() / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                _DOTENV[k.strip()] = v.strip().strip('"').strip("'")
    return _DOTENV


# --------------------------------------------------------------------------- native secret stores

def _secret_get(name: str) -> str:
    """Read a secret from the platform's secure store: macOS Keychain,
    Linux libsecret (secret-tool), or Windows DPAPI (encrypted blob)."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(
                ["security", "find-generic-password", "-a", "pha", "-s", name, "-w"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        if sys.platform.startswith("linux"):
            r = subprocess.run(
                ["secret-tool", "lookup", "service", "pha", "key", name],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        if sys.platform == "win32":
            blob = _dpapi_read(name)
            return blob.decode() if blob else ""
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _secret_set(name: str, value: str) -> bool:
    """Store a secret in the platform's secure store. Returns False when the
    store is unavailable (caller falls back to a gitignored .env file)."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(
                ["security", "add-generic-password", "-a", "pha", "-s", name, "-w", value, "-U"],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        if sys.platform.startswith("linux"):
            r = subprocess.run(
                ["secret-tool", "store", "--label=pha", "service", "pha", "key", name],
                input=value + "\n", capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        if sys.platform == "win32":
            _dpapi_write(name, value.encode())
            return _dpapi_read(name) == value.encode()
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _dpapi_path(name: str) -> Path:
    return find_project_root() / "data" / "secrets" / f"{name}.bin"


def _dpapi_write(name: str, data: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def to_blob(raw: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))

    crypt32 = ctypes.windll.crypt32
    pin = to_blob(data)
    pout = DATA_BLOB()
    if crypt32.CryptProtectData(ctypes.byref(pin), None, None, None, None, 0, ctypes.byref(pout)):
        try:
            out = ctypes.string_at(pout.pbData, pout.cbData)
            path = _dpapi_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(out)
        finally:
            ctypes.windll.kernel32.LocalFree(pout.pbData)


def _dpapi_read(name: str) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def to_blob(raw: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))

    path = _dpapi_path(name)
    if not path.exists():
        return b""
    crypt32 = ctypes.windll.crypt32
    pin = to_blob(path.read_bytes())
    pout = DATA_BLOB()
    if crypt32.CryptUnprotectData(ctypes.byref(pin), None, None, None, None, 0, ctypes.byref(pout)):
        try:
            return ctypes.string_at(pout.pbData, pout.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(pout.pbData)
    return b""


def find_project_root(start: Path | None = None) -> Path:
    env = os.environ.get("PHA_HOME")
    if env:
        return Path(env).resolve()
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "config.yaml").exists():
            return p
    # Fallback: the editable install lives inside the project tree.
    pkg = Path(__file__).resolve().parents[2]
    if (pkg / "config.yaml").exists():
        return pkg
    return cur


@dataclass
class Model:
    """A named model interface: how to reach and talk to a model, plus its
    capacity/limits. Content rules live separately in
    palaeographers/editors/encoders and reference a Model by id.

    Fields are the transport + capacity + vision limits that used to be
    inlined in every palaeographer/editor/encoder file. Sampling settings
    (temperature, max_tokens) and extraction params stay on the stage files,
    because each stage has different sensible defaults.
    """

    id: str
    description: str = ""
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = ""
    model: str = ""            # server-side model name (e.g. qwen/qwen3-vl-8b)
    api_style: str = "openai"
    thinking: bool = True
    max_vision_px: int = 1800
    vision_jpeg_quality: int = 88
    context_tokens: int = 200_000
    # engine: the implementation that produces the stage's output.
    #   "" / "llm" (default) - an HTTP chat/vision endpoint via ModelClient.
    #   otherwise - a named local engine looked up in model_client.PAGE_ENGINES
    #     (e.g. "tesseract", "liteparse"). Non-LLM engines have no
    #     base_url/model/api_key; their settings live in the per-engine fields
    #     below.
    engine: str = ""
    # Tesseract engine settings
    tesseract_lang: str = ""       # -l language(s), e.g. "por", "lat", "por+lat" ("" = tesseract default)
    tesseract_psm: int | None = None  # --psm page-segmentation mode (tesseract) or None
    # LiteParse engine settings (local document/OCR parser via `lit parse`)
    liteparse_lang: str = ""       # --ocr-language (Tesseract format, e.g. "por", "fra")
    liteparse_dpi: int | None = None  # --dpi render resolution (default 150; 300 = quality)
    # liteparse_format: `lit parse` output format. "text" (default) is the
    # human-readable, layout-preserved transcription; "markdown" is structured
    # markdown; "json" is text + per-item bounding boxes/confidence (a spatial
    # dump a later model/encoder stage can reason over).
    liteparse_format: str = "text"
    # liteparse_ocr: which input `lit parse` reads.
    #   "fresh" (default) - the rendered page RASTER, so LiteParse must OCR it
    #     (no embedded text layer to fall back on). Safe on historical scans.
    #   "embedded" - the ORIGINAL source PDF page (--target-pages), so LiteParse
    #     uses the PDF's embedded/native text layer where present, OCRing only
    #     the gaps. Faster/cleaner on typed PDFs, but may surface an archive's
    #     old low-quality text layer. Non-PDF sources always fall back to fresh.
    #   "prefer-embedded" - decide PER PAGE: read the page's embedded text with
    #     pymupdf and reuse it (parsing the source PDF) only when it passes the
    #     quality gate below; otherwise OCR the raster. Best of both: a
    #     born-digital/well-OCR'd PDF is not re-OCR'd, a scan with a junk layer
    #     is OCR'd normally. Non-PDF sources always fall back to fresh.
    liteparse_ocr: str = "fresh"
    # Quality gate for `liteparse_ocr: prefer-embedded` (see
    # model_client.embedded_text_is_good). min_chars is the minimum
    # non-whitespace character count for the layer to be considered at all;
    # min_quality is the ratio floor applied to both "share of characters that
    # are letters" and "share of tokens that look like words".
    liteparse_embedded_min_chars: int = 200
    liteparse_embedded_min_quality: float = 0.60
    prompt_file: Path | None = None


@dataclass
class Palaeographer:
    """A named vision model that transcribes documents.

    `prompt_text` is the palaeographer's base prompt; it is prepended BEFORE
    the document/collection sidecar prompt when extracting a page.
    """

    id: str
    description: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout_s: int
    prompt_text: str
    prompt_file: Path | None = None
    thinking: bool = True
    # api_style: the wire format for ALL calls to this model.
    #   "openai"   (default) - /chat/completions, OpenAI-style messages with
    #              image_url blocks. Works with LM Studio, Ollama, vLLM,
    #              OpenAI, OpenRouter, Groq, ...
    #   "anthropic" - /anthropic/v1/messages (host root), image embedded as a
    #              plain-text data URI in content. Needed for MiniMax vision
    #              (their OpenAI-compatible endpoint silently drops image_url
    #              blocks) and any Anthropic-compatible service.
    # max_vision_px caps the longest image edge for vision models with a small
    # image context (e.g. MiniMax M2.7 works well at ~1400).
    api_style: str = "openai"
    max_vision_px: int = 1800
    # vision_jpeg_quality: JPEG quality used when re-encoding the page for the
    # anthropic-style path (the image travels as base64 TEXT, ~2 chars/token,
    # so a full-res render can cost ~190k input tokens). Lower quality keeps
    # full resolution while shrinking the base64: q55 at 1800px ~= 150k tokens
    # vs q88 ~= 195k. Only affects re-encoding for anthropic-style models;
    # openai-style images (LM Studio, qwen) are sent as rendered, untouched.
    vision_jpeg_quality: int = 88
    model_ref: str = ""  # models/<id>.md this palaeographer uses ("" = legacy inline)
    # engine: ""/"llm" (default) = chat_vision via ModelClient; otherwise a
    # named local engine in model_client.PAGE_ENGINES. Local OCR/parse engines
    # (tesseract, liteparse) are NOT HTTP models — no base_url / api_key /
    # token limits. Their per-page output becomes the page transcript.
    engine: str = ""
    tesseract_lang: str = ""       # -l language(s), e.g. "por", "lat", "por+lat" ("" = tesseract default)
    tesseract_psm: int | None = None  # --psm page-segmentation mode (tesseract) or None
    liteparse_lang: str = ""       # --ocr-language (Tesseract format, e.g. "por", "fra")
    liteparse_dpi: int | None = None  # --dpi render resolution (default 150; 300 = quality)
    liteparse_format: str = "text"  # "text" | "markdown" | "json" (see Model)
    liteparse_ocr: str = "fresh"    # "fresh" (raster) | "embedded" (source PDF text layer)
                                    #   | "prefer-embedded" (embedded only when the
                                    #   layer passes the quality gate, else raster)
    liteparse_embedded_min_chars: int = 200      # prefer-embedded gate (see Model)
    liteparse_embedded_min_quality: float = 0.60  # prefer-embedded gate (see Model)

    @property
    def prompt_source(self) -> str:
        return f"palaeographer:{self.id}"


@dataclass
class Editor:
    """A named text model that transforms the palaeographer's transcription.

    An editor is a completely DIFFERENT model from the palaeographer: it runs
    on its own endpoint (local or remote) and applies an editing prompt
    (modernize spelling, translate, ...) to the per-page transcription text.
    """

    id: str
    description: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout_s: int
    prompt_text: str
    prompt_file: Path | None = None
    thinking: bool = True
    api_style: str = "openai"
    model_ref: str = ""  # models/<id>.md this editor uses ("" = legacy inline)


@dataclass
class Encoder:
    """A named text model that turns (edited) transcriptions into structured
    records (e.g. letter metadata: from/to/date/place). The third stage of the
    pipeline: palaeographer reads -> editor transforms -> encoder structures.
    Runs on its own endpoint (local or remote) with its encoding prompt."""

    id: str
    description: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout_s: int
    prompt_text: str
    prompt_file: Path | None = None
    thinking: bool = True
    api_style: str = "openai"
    batch_pages: int = 20
    # context_tokens: the model's input context window in tokens. The encoder
    # feeds the document as ONE concatenated text when it fits the window
    # (max_input_chars, derived from context_tokens at ~4 chars/token); larger
    # documents are chunked. Different models have different windows (MiniMax
    # M2.5 = 200k; a local 7B might be 32k) — set this per encoder so the
    # single-pass/chunked decision matches the model.
    context_tokens: int = 200_000
    max_input_chars: int | None = None
    overlap_pages: int = 4
    extraction_passes: int = 1
    # Deterministic entry detection (fast path). When both are set, pages whose
    # text matches candidate_pattern (e.g. a lone Roman numeral line) and whose
    # following lines match candidate_header (e.g. a 'Name aos Name' header)
    # are treated as entry starts; the model then extracts each entry from its
    # own small span (page told to it) instead of hunting the whole document.
    # Without these, detection falls back to a cheap model scan per chunk
    # driven by the detection rules in the collection's encoder.prompt.md.
    candidate_pattern: str | None = None
    candidate_header: str | None = None
    # pages: which PDF pages of the document this encoder handles, e.g. "1-15"
    # (the chronological table in Pfister's front matter). These are PDF page
    # numbers — NOT the numbers printed on the page (Pfister's table is printed
    # i–xv but occupies PDF pages 1-15). Empty = the whole document. Multiple
    # encoders in one collection run in page order.
    pages: str = ""
    model_ref: str = ""  # models/<id>.md this encoder uses ("" = legacy inline)

    @property
    def effective_max_input_chars(self) -> int:
        """max_input_chars if set in the encoder file, else derived from
        context_tokens at ~4 chars/token (a reasonable average for European
        languages, incl. accented historical text)."""
        if self.max_input_chars:
            return self.max_input_chars
        return max(4_000, self.context_tokens * 4)


@dataclass
class Config:
    root: Path
    # archive_dir is the single self-contained data root: documents, the model
    # definitions (palaeographers/editors/encoders), user-facing notes and
    # everything the pipeline generates (library, renders, db) live under it.
    # The project dir holds only code, engine-level prompts and the _sample.md
    # templates.
    archive_dir: Path
    # paths
    dropbox: Path
    inbox: Path
    library: Path
    data: Path
    renders: Path
    # notes: user-facing Obsidian-compatible markdown notes generated from
    # queries to the archive (sibling of dropbox/library; NOT pipeline output).
    notes: Path
    prompts: Path
    palaeographers_dir: Path
    editors_dir: Path
    encoders_dir: Path
    models_dir: Path
    # stage filters (<archive>/filters/<id>/); archive-level only (DEC-3)
    filters_dir: Path
    db_path: Path
    # model interface registry (models/<id>.md); stage rules files carry no
    # model — the model is chosen per document in pha.yaml (or this default).
    models: dict[str, Model]
    default_model: str
    # palaeographers (vision models)
    palaeographers: dict[str, Palaeographer]
    active_palaeographer: str
    # editors (text models that transform transcriptions)
    editors: dict[str, Editor]
    # encoders (text models that extract structured records)
    encoders: dict[str, Encoder]
    # embedding model
    embed_backend: str
    embed_base_url: str
    embed_model: str
    embed_timeout_s: int
    embed_batch_size: int
    # extraction
    render_dpi: int
    max_image_px: int
    jpeg_quality: int
    chunk_chars: int
    chunk_overlap: int
    concurrency: int
    dir_documents: bool
    # search
    default_mode: str
    top_k: int
    # self-update: daily startup check + `pha update`
    update_enabled: bool
    update_interval_h: int
    update_timeout: int
    update_repo: str
    update_branch: str

    @classmethod
    def load(cls, root: Path | None = None) -> "Config":
        root = find_project_root(root)
        cfg_path = root / "config.yaml"
        raw: dict = {}
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        paths = raw.get("paths", {})
        vis = raw.get("vision", {}) or {}
        emb = raw.get("embeddings", {}) or {}
        ext = raw.get("extraction", {}) or {}
        sea = raw.get("search", {}) or {}
        upd = raw.get("update", {}) or {}

        def _env_setting(name: str) -> str | None:
            """Read a setting from the real environment, then a line NAME=... in
            the gitignored .env AT THIS ROOT (so tests with a tmp root are
            isolated). Returns None if unset."""
            v = os.environ.get(name)
            if v:
                return v
            envp = root / ".env"
            if envp.exists():
                for line in envp.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith(name + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
            return None

        # archive_dir is the single self-contained data root. Everything the
        # archive owns — documents, model definitions, generated output —
        # lives under it. The project dir holds only code, engine-level
        # prompts and the _sample.md templates.
        # Precedence: PHA_ARCHIVE_DIR env > PHA_ARCHIVE_DIR in .env >
        # paths.archive_dir > default "." (the project root, backward
        # compatible).
        archive_env = _env_setting("PHA_ARCHIVE_DIR")
        archive_dir = _p(root, str(archive_env or paths.get("archive_dir", ".")))

        # engine-level prompts stay in the PROJECT (not the archive).
        prompts_dir = _p(root, paths.get("prompts", "prompts"))

        # Data + model definitions derive from archive_dir. Individual
        # paths.* entries are relative to archive_dir (absolute still wins).
        # PHA_DROPBOX is a DEPRECATED legacy alias: it is honored ONLY when
        # archive_dir was NOT explicitly set (i.e. we are in the old single-
        # dropbox layout). Once archive_dir is configured, the dropbox is
        # always archive_dir/dropbox — a stale PHA_DROPBOX in .env must not
        # hijack it.
        if archive_env:
            dropbox = _p(archive_dir, paths.get("dropbox", "dropbox"))
        else:
            dropbox = _p(archive_dir, str(_env_setting("PHA_DROPBOX") or paths.get("dropbox", "dropbox")))
        pal_dir = _p(archive_dir, paths.get("palaeographers", "palaeographers"))
        ed_dir = _p(archive_dir, paths.get("editors", "editors"))
        enc_dir = _p(archive_dir, paths.get("encoders", "encoders"))
        models_dir = _p(archive_dir, paths.get("models", "models"))
        # Stage filters: one archive-level directory (a sibling of the other
        # definition folders). A filter is a subprocess/helper unit that shapes
        # the value flowing between pipeline stages; see FILTERS_PLAN.md and
        # `pha filters`. Not per-collection: which filters run is configured in
        # each pha.yaml (DEC-3).
        filters_dir = _p(archive_dir, paths.get("filters", "filters"))
        # inbox: documents parked on hold. pha scan never touches them and
        # `pha status` reports them as 'on hold'; `pha inbox --move` relocates
        # them into the dropbox (preserving the relative layout) to be scanned.
        inbox = _p(archive_dir, paths.get("inbox", "inbox"))

        # Seed the zero-config defaults BEFORE parsing, so a fresh archive has
        # working default model/palaeographer/editor/encoder on first load.
        _seed_default_model(models_dir, _DEFAULT_MODEL)
        _seed_default(pal_dir, _DEFAULT_PAL)
        _seed_default(ed_dir, _DEFAULT_ED)
        _seed_default(enc_dir, _DEFAULT_ENC)

        # Migrate legacy definitions that live in the project dir (before the
        # archive_dir split) into the archive dir, preserving their ids.
        _migrate_legacy_defs(root / "palaeographers", pal_dir)
        _migrate_legacy_defs(root / "editors", ed_dir)

        models = _parse_models(models_dir)
        palaeographers, active = _parse_palaeographers(raw, vis, prompts_dir, root, pal_dir, models)
        editors = _parse_editors(raw, prompts_dir, root, ed_dir, models)
        encoders = _parse_encoders(enc_dir, models)

        return cls(
            root=root,
            archive_dir=archive_dir,
            dropbox=dropbox,
            inbox=inbox,
            library=_p(archive_dir, paths.get("library", "library")),
            data=archive_dir,  # runtime state (e.g. the scan lock) lives at the root of the archive
            renders=_p(archive_dir, paths.get("renders", "renders")),
            notes=_p(archive_dir, paths.get("notes", "notes")),
            prompts=prompts_dir,
            palaeographers_dir=pal_dir,
            editors_dir=ed_dir,
            encoders_dir=enc_dir,
            models_dir=models_dir,
            filters_dir=filters_dir,
            db_path=_p(archive_dir, paths.get("db", "archive.db")),
            models=models,
            default_model=str(vis.get("model", "default")).strip() or "default",
            palaeographers=palaeographers,
            active_palaeographer=active,
            editors=editors,
            encoders=encoders,
            embed_backend=str(emb.get("backend", "lmstudio")),
            embed_base_url=str(emb.get("base_url", "http://127.0.0.1:1234/v1")),
            embed_model=str(emb.get("model", "text-embedding-nomic-embed-text-v1.5@q4_k_m")),
            embed_timeout_s=int(emb.get("timeout_s", 120)),
            embed_batch_size=int(emb.get("batch_size", 100)),
            render_dpi=int(ext.get("render_dpi", 200)),
            max_image_px=int(ext.get("max_image_px", 1800)),
            jpeg_quality=int(ext.get("jpeg_quality", 88)),
            chunk_chars=int(ext.get("chunk_chars", 2000)),
            chunk_overlap=int(ext.get("chunk_overlap", 200)),
            concurrency=int(ext.get("concurrency", 1)),
            dir_documents=bool(ext.get("dir_documents", True)),
            default_mode=str(sea.get("default_mode", "hybrid")),
            top_k=int(sea.get("top_k", 10)),
            update_enabled=bool(upd.get("enabled", True)),
            update_interval_h=int(upd.get("interval_h", 24)),
            update_timeout=int(upd.get("timeout", 5)),
            update_repo=str(upd.get("repo", "joaquimrcarvalho/personal-historical-archive")),
            update_branch=str(upd.get("branch", "main")),
        )

    def get_palaeographer(self, pal_id: str | None = None) -> Palaeographer:
        pal_id = pal_id or self.active_palaeographer
        if pal_id not in self.palaeographers:
            raise KeyError(
                f"unknown palaeographer {pal_id!r}; configured: {sorted(self.palaeographers)}"
            )
        return self.palaeographers[pal_id]

    def get_editor(self, editor_id: str) -> Editor:
        if editor_id not in self.editors:
            raise KeyError(f"unknown editor {editor_id!r}; configured: {sorted(self.editors)}")
        return self.editors[editor_id]

    def get_encoder(self, encoder_id: str) -> Encoder:
        if encoder_id not in self.encoders:
            raise KeyError(f"unknown encoder {encoder_id!r}; configured: {sorted(self.encoders)}")
        return self.encoders[encoder_id]

    def get_model(self, model_id: str) -> Model:
        if model_id not in self.models:
            raise KeyError(f"unknown model {model_id!r}; configured: {sorted(self.models)}")
        return self.models[model_id]

    def with_model(self, stage, model_id: str | None):
        """Return a copy of a palaeographer/editor/encoder with its interface
        fields overridden by the given model (models/<id>.md). A falsy model_id
        returns the stage unchanged."""
        if not model_id:
            return stage
        import dataclasses

        m = self.get_model(model_id)
        common = dict(
            base_url=m.base_url, api_key=m.api_key, model=m.model,
            api_style=m.api_style, thinking=m.thinking, model_ref=m.id,
        )
        if isinstance(stage, Encoder):
            common["context_tokens"] = m.context_tokens
        if isinstance(stage, Palaeographer):
            common["max_vision_px"] = m.max_vision_px
            common["vision_jpeg_quality"] = m.vision_jpeg_quality
            common["engine"] = m.engine
            common["tesseract_lang"] = m.tesseract_lang
            common["tesseract_psm"] = m.tesseract_psm
            common["liteparse_lang"] = m.liteparse_lang
            common["liteparse_dpi"] = m.liteparse_dpi
            common["liteparse_format"] = m.liteparse_format
            common["liteparse_ocr"] = m.liteparse_ocr
            common["liteparse_embedded_min_chars"] = m.liteparse_embedded_min_chars
            common["liteparse_embedded_min_quality"] = m.liteparse_embedded_min_quality
        return dataclasses.replace(stage, **common)

    def resolve_model(self, stage, model_id: str | None = None):
        """Bind a model to a stage: `model_id` when given (from pha.yaml),
        else the stage's own legacy inline interface, else the global
        default_model."""
        if model_id:
            return self.with_model(stage, model_id)
        if not getattr(stage, "base_url", ""):
            return self.with_model(stage, self.default_model)
        return stage

    def encoder_from_file(self, path: Path) -> Encoder | None:
        """Load a single encoder definition file (collection-local:
        dropbox/collections/COLX/encoders/<name>.md). None on failure."""
        if not path.exists():
            return None
        try:
            return _encoder_from_frontmatter(path.stem, path.read_text(encoding="utf-8"), path, self.models)
        except Exception as e:  # noqa: BLE001 - a bad file must not break the load
            print(f"warning: invalid encoder file {path}: {e}")
            return None

    def _seed_filter_sample(self) -> None:
        """Seed `filters/_sample/` (the how-to-write-a-filter template).

        The archive-level filter template is written here rather than through
        `builtin_samples()` (which seeds the project side) because a filter
        operates on archive data and is adopted per collection. Never
        overwrites a human's edits.
        """
        try:
            from .filters import FILTER_SAMPLE_MD, FILTER_SAMPLE_PY
        except Exception:  # noqa: BLE001 - a missing template must not break a command
            return
        sample = self.filters_dir / "_sample"
        try:
            sample.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        _seed_sample(sample, "filter.md", FILTER_SAMPLE_MD)
        _seed_sample(sample, "filter.py", FILTER_SAMPLE_PY)

    def ensure_dirs(self) -> None:
        for d in (self.dropbox, self.inbox, self.library, self.data, self.renders, self.notes,
                  self.prompts, self.palaeographers_dir, self.editors_dir, self.encoders_dir,
                  self.models_dir, self.filters_dir):
            d.mkdir(parents=True, exist_ok=True)
        # user-facing notes folder: seed the instructions/format guide when it is
        # first created (never overwrite a human's edits to notes/README.md). The
        # repo's notes/README.md is authoritative (fallback: the inline template).
        _seed_sample(self.notes, "README.md", notes_readme_template(self.root))
        # pre-create the dropbox sub-layout so a fresh archive is ready to use:
        # documents/ for individual documents, collections/COLX/ for collections.
        for sub in ("documents", "collections"):
            (self.dropbox / sub).mkdir(parents=True, exist_ok=True)
        # seed the BUILTIN samples into the PROJECT (code side): they are both
        # the how-to-create templates and the ready-to-duplicate catalogue of
        # what pha supports out of the box (never loaded; names start with '_').
        for sub, fname, content in builtin_samples():
            d = self.root / sub
            d.mkdir(parents=True, exist_ok=True)
            _seed_sample(d, fname, content)
        # stage filters live in the ARCHIVE (they shape archive data), so their
        # template is seeded there rather than into the project like the stage
        # definitions above. `filters/_sample/` is ignored by the loader (the
        # '_' prefix) — copy it to `filters/<my-filter>/` to adopt one.
        self._seed_filter_sample()
        # keep the archive's agent-facing docs (README.md / AGENTS.md) current
        # with the installed pha version: create them when missing and refresh a
        # pristine generated doc when a pha update changed the template. Never
        # overwrites a user-customised file; and SKIP entirely when the archive
        # IS the project dir (the legacy single-dropbox layout) so the repo's own
        # README.md / AGENTS.md are never replaced.
        try:
            from . import archive_init
            if self.archive_dir.resolve() != self.root.resolve():
                archive_init.refresh_archive_agent_docs(self.archive_dir)
        except Exception:  # noqa: BLE001 - a doc refresh must never break a command
            pass


def _parse_palaeographers(
    raw: dict, vis: dict, prompts_dir: Path, root: Path, pal_dir: Path, models: dict | None = None
) -> tuple[dict[str, Palaeographer], str]:
    """Palaeographers are one file per palaeographer in `palaeographers/`
    (YAML front matter = model reference, body = the base prompt). Falls back
    to the legacy `palaeographers:` config map / `vision:` block."""
    pals = _load_model_dir(pal_dir, "palaeographer", _palaeographer_from_frontmatter, models=models or {})
    if pals:
        active = str(vis.get("palaeographer", "")) or (next(iter(pals), ""))
        return pals, active

    raw_pals = raw.get("palaeographers")
    if isinstance(raw_pals, dict) and raw_pals:
        pals = {}
        for pal_id, entry in raw_pals.items():
            if not isinstance(entry, dict):
                continue
            pals[str(pal_id)] = _palaeographer_from_entry(
                str(pal_id), entry, prompts_dir, root
            )
        active = str(vis.get("palaeographer", "")) or (next(iter(pals), ""))
        return pals, active

    # legacy: single vision block
    default_file = prompts_dir / "palaeographers" / "default.md"
    prompt_text = ""
    if default_file.exists():
        prompt_text = default_file.read_text(encoding="utf-8")
    pal = Palaeographer(
        id="default",
        description="legacy vision block",
        base_url=str(vis.get("base_url", "http://127.0.0.1:1234/v1")),
        api_key=_expand(str(vis.get("api_key", ""))),
        model=str(vis.get("model", "qwen/qwen3-vl-8b")),
        temperature=float(vis.get("temperature", 0.1)),
        max_tokens=int(vis.get("max_tokens", 4096)),
        timeout_s=int(vis.get("timeout_s", 900)),
        prompt_text=prompt_text,
    )
    return {"default": pal}, "default"


def _parse_editors(raw: dict, prompts_dir: Path, root: Path, ed_dir: Path, models: dict | None = None) -> dict[str, Editor]:
    """Editors are one file per editor in `editors/` (front matter = model
    reference, body = the editing prompt). Falls back to the legacy `editors:`
    config map."""
    eds = _load_model_dir(ed_dir, "editor", _editor_from_frontmatter, models=models or {})
    if eds:
        return eds
    raw_eds = raw.get("editors")
    editors: dict[str, Editor] = {}
    if not isinstance(raw_eds, dict):
        return editors
    for ed_id, entry in raw_eds.items():
        if not isinstance(entry, dict):
            continue
        prompt_text = ""
        prompt_file: Path | None = None
        pf = entry.get("prompt_file")
        if pf:
            p = Path(str(pf))
            if not p.is_absolute():
                p = root / p
                if not p.exists():
                    alt = prompts_dir / p
                    if alt.exists():
                        p = alt
            if p.exists():
                prompt_file = p
                prompt_text = p.read_text(encoding="utf-8")
        elif isinstance(entry.get("prompt"), str):
            prompt_text = entry["prompt"]
        editors[str(ed_id)] = Editor(
            id=str(ed_id),
            description=str(entry.get("description", "")),
            base_url=_expand(str(entry.get("base_url", "http://127.0.0.1:1234/v1"))),
            api_key=_expand(str(entry.get("api_key", ""))),
            model=str(entry.get("model", "")),
            temperature=float(entry.get("temperature", 0.1)),
            max_tokens=int(entry.get("max_tokens", 4096)),
            timeout_s=int(entry.get("timeout_s", 300)),
            prompt_text=prompt_text,
            prompt_file=prompt_file,
        )
    return editors


def _palaeographer_from_entry(
    pal_id: str, entry: dict, prompts_dir: Path, root: Path
) -> Palaeographer:
    prompt_text = ""
    prompt_file: Path | None = None
    if entry.get("prompt_file"):
        p = Path(str(entry["prompt_file"]))
        if not p.is_absolute():
            p = root / p  # config paths are relative to the project root
            if not p.exists():
                alt = prompts_dir / Path(str(entry["prompt_file"]))
                if alt.exists():
                    p = alt
        if p.exists():
            prompt_file = p
            prompt_text = p.read_text(encoding="utf-8")
    elif isinstance(entry.get("prompt"), str):
        prompt_text = entry["prompt"]
    return Palaeographer(
        id=pal_id,
        description=str(entry.get("description", "")),
        base_url=_expand(str(entry.get("base_url", "http://127.0.0.1:1234/v1"))),
        api_key=_expand(str(entry.get("api_key", ""))),
        model=str(entry.get("model", "")),
        temperature=float(entry.get("temperature", 0.1)),
        max_tokens=int(entry.get("max_tokens", 4096)),
        timeout_s=int(entry.get("timeout_s", 900)),
        prompt_text=prompt_text,
        prompt_file=prompt_file,
    )


# --------------------------------------------------------------------------- file-based model configs

def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split '--- yaml ---' front matter from the prompt body."""
    fm: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(text[3:end].strip()) or {}
                if not isinstance(fm, dict):
                    fm = {}
            except yaml.YAMLError:
                fm = {}
            body = text[end + 4 :].strip()
    return fm, body


def _load_model_dir(directory: Path, kind: str, builder, **kwargs) -> dict:
    """Load one config per file from a directory. File stem = id; files whose
    name starts with '_' or '.' (samples, hidden) are ignored; malformed files
    are skipped with a warning so a typo never breaks the whole load."""
    models: dict = {}
    if not directory.is_dir():
        return models
    for f in sorted(directory.iterdir()):
        if not f.is_file() or f.name.startswith(("_", ".")):
            continue
        if f.suffix.lower() not in (".md", ".txt"):
            continue
        try:
            model = builder(f.stem, f.read_text(encoding="utf-8"), f, **kwargs)
        except Exception as e:  # noqa: BLE001 - a bad file must not kill the load
            print(f"warning: invalid {kind} file {f}: {e}")
            continue
        if model is not None:
            models[f.stem] = model
    return models


def _parse_models(models_dir: Path) -> dict[str, Model]:
    """Models are one file per model interface in `models/` (front matter
    only; the body, if any, is ignored)."""
    return _load_model_dir(models_dir, "model", _model_from_frontmatter)


def _model_from_frontmatter(model_id: str, text: str, file: Path) -> Model | None:
    fm, _ = _split_frontmatter(text)
    return Model(
        id=model_id,
        description=str(fm.get("description", "")),
        base_url=_expand(str(fm.get("base_url", "http://127.0.0.1:1234/v1"))),
        api_key=_expand(str(fm.get("api_key", ""))),
        model=str(fm.get("model", "")),
        api_style=str(fm.get("api_style", "openai")).strip().lower() or "openai",
        thinking=_thinking(fm),
        max_vision_px=int(fm.get("max_vision_px", 1800)),
        vision_jpeg_quality=int(fm.get("vision_jpeg_quality", 88)),
        context_tokens=int(fm.get("context_tokens", 200_000)),
        engine=str(fm.get("engine", "")).strip().lower(),
        tesseract_lang=str(fm.get("tesseract_lang", "")).strip(),
        tesseract_psm=(int(fm["tesseract_psm"]) if fm.get("tesseract_psm") is not None else None),
        liteparse_lang=str(fm.get("liteparse_lang", "")).strip(),
        liteparse_dpi=(int(fm["liteparse_dpi"]) if fm.get("liteparse_dpi") is not None else None),
        liteparse_format=str(fm.get("liteparse_format", "text")).strip().lower() or "text",
        liteparse_ocr=str(fm.get("liteparse_ocr", "fresh")).strip().lower() or "fresh",
        liteparse_embedded_min_chars=int(fm.get("liteparse_embedded_min_chars", 200)),
        liteparse_embedded_min_quality=float(fm.get("liteparse_embedded_min_quality", 0.60)),
        prompt_file=file,
    )


def _resolve_model(fm: dict, models: dict) -> tuple[Model, str]:
    """Resolve a stage file's model interface.

    New rules files carry NO model — the model is chosen in pha.yaml at
    document time. Legacy files inline their interface
    (`base_url`/`api_key`/`api_style`/...) and are synthesised into an
    anonymous Model (model_ref = "").
    """
    if "base_url" in fm or "api_key" in fm or "api_style" in fm or "engine" in fm \
            or "tesseract_lang" in fm or "tesseract_psm" in fm \
            or "liteparse_lang" in fm or "liteparse_dpi" in fm \
            or "liteparse_format" in fm or "liteparse_ocr" in fm \
            or "liteparse_embedded_min_chars" in fm \
            or "liteparse_embedded_min_quality" in fm:
        # legacy inline interface (pre-registry) — includes non-LLM engines
        # (e.g. `engine: tesseract` or `engine: liteparse`) which have no
        # base_url/api_key.
        m = Model(
            id="",
            description=str(fm.get("description", "")),
            base_url=_expand(str(fm.get("base_url", "http://127.0.0.1:1234/v1"))),
            api_key=_expand(str(fm.get("api_key", ""))),
            model=str(fm.get("model", "")),
            api_style=str(fm.get("api_style", "openai")).strip().lower() or "openai",
            thinking=_thinking(fm),
            max_vision_px=int(fm.get("max_vision_px", 1800)),
            vision_jpeg_quality=int(fm.get("vision_jpeg_quality", 88)),
            context_tokens=int(fm.get("context_tokens", 200_000)),
            engine=str(fm.get("engine", "")).strip().lower(),
            tesseract_lang=str(fm.get("tesseract_lang", "")).strip(),
            tesseract_psm=(int(fm["tesseract_psm"]) if fm.get("tesseract_psm") is not None else None),
            liteparse_lang=str(fm.get("liteparse_lang", "")).strip(),
            liteparse_dpi=(int(fm["liteparse_dpi"]) if fm.get("liteparse_dpi") is not None else None),
            liteparse_format=str(fm.get("liteparse_format", "text")).strip().lower() or "text",
            liteparse_ocr=str(fm.get("liteparse_ocr", "fresh")).strip().lower() or "fresh",
            liteparse_embedded_min_chars=int(fm.get("liteparse_embedded_min_chars", 200)),
            liteparse_embedded_min_quality=float(fm.get("liteparse_embedded_min_quality", 0.60)),
        )
        return m, ""
    # new rules-only file: no model here (chosen per document in pha.yaml);
    # empty base_url marks it as unbound so resolve_model() fills the default.
    return Model(id="", base_url=""), ""


def _palaeographer_from_frontmatter(pal_id: str, text: str, file: Path, models: dict | None = None) -> Palaeographer | None:
    fm, body = _split_frontmatter(text)
    m, model_ref = _resolve_model(fm, models or {})
    return Palaeographer(
        id=pal_id,
        description=str(fm.get("description", "")),
        model_ref=model_ref,
        base_url=m.base_url,
        api_key=m.api_key,
        model=m.model,
        api_style=m.api_style,
        timeout_s=int(fm.get("timeout_s", 900)),
        thinking=m.thinking,
        max_vision_px=m.max_vision_px,
        vision_jpeg_quality=m.vision_jpeg_quality,
        engine=m.engine or str(fm.get("engine", "")).strip().lower(),
        tesseract_lang=m.tesseract_lang or str(fm.get("tesseract_lang", "")).strip(),
        tesseract_psm=m.tesseract_psm if m.tesseract_psm is not None
        else (int(fm["tesseract_psm"]) if fm.get("tesseract_psm") is not None else None),
        liteparse_lang=m.liteparse_lang or str(fm.get("liteparse_lang", "")).strip(),
        liteparse_dpi=m.liteparse_dpi if m.liteparse_dpi is not None
        else (int(fm["liteparse_dpi"]) if fm.get("liteparse_dpi") is not None else None),
        liteparse_format=m.liteparse_format if m.liteparse_format != "text"
        else str(fm.get("liteparse_format", "text")).strip().lower() or "text",
        liteparse_ocr=m.liteparse_ocr if m.liteparse_ocr != "fresh"
        else str(fm.get("liteparse_ocr", "fresh")).strip().lower() or "fresh",
        liteparse_embedded_min_chars=m.liteparse_embedded_min_chars
        if m.liteparse_embedded_min_chars != 200
        else int(fm.get("liteparse_embedded_min_chars", 200)),
        liteparse_embedded_min_quality=m.liteparse_embedded_min_quality
        if m.liteparse_embedded_min_quality != 0.60
        else float(fm.get("liteparse_embedded_min_quality", 0.60)),
        temperature=float(fm.get("temperature", 0.1)),
        max_tokens=int(fm.get("max_tokens", 4096)),
        prompt_text=body,
        prompt_file=file,
    )


def _editor_from_frontmatter(ed_id: str, text: str, file: Path, models: dict | None = None) -> Editor | None:
    fm, body = _split_frontmatter(text)
    m, model_ref = _resolve_model(fm, models or {})
    return Editor(
        id=ed_id,
        description=str(fm.get("description", "")),
        model_ref=model_ref,
        base_url=m.base_url,
        api_key=m.api_key,
        model=m.model,
        api_style=m.api_style,
        timeout_s=int(fm.get("timeout_s", 300)),
        thinking=m.thinking,
        temperature=float(fm.get("temperature", 0.1)),
        max_tokens=int(fm.get("max_tokens", 4096)),
        prompt_text=body,
        prompt_file=file,
    )


def _parse_encoders(enc_dir: Path, models: dict | None = None) -> dict[str, Encoder]:
    """Encoders are one file per encoder in `encoders/` (front matter = model
    reference, body = the base encoding prompt)."""
    return _load_model_dir(enc_dir, "encoder", _encoder_from_frontmatter, models=models or {})


def _encoder_from_frontmatter(enc_id: str, text: str, file: Path, models: dict | None = None) -> Encoder | None:
    fm, body = _split_frontmatter(text)
    m, model_ref = _resolve_model(fm, models or {})
    return Encoder(
        id=enc_id,
        description=str(fm.get("description", "")),
        model_ref=model_ref,
        base_url=m.base_url,
        api_key=m.api_key,
        model=m.model,
        api_style=m.api_style,
        timeout_s=int(fm.get("timeout_s", 300)),
        thinking=m.thinking,
        context_tokens=m.context_tokens,
        temperature=float(fm.get("temperature", 0.0)),
        max_tokens=int(fm.get("max_tokens", 4096)),
        prompt_text=body,
        prompt_file=file,
        batch_pages=int(fm.get("batch_pages", 20)),
        max_input_chars=(int(fm["max_input_chars"]) if fm.get("max_input_chars") else None),
        overlap_pages=int(fm.get("overlap_pages", 4)),
        extraction_passes=int(fm.get("extraction_passes", 1)),
        candidate_pattern=str(fm.get("candidate_pattern", "") or "") or None,
        candidate_header=str(fm.get("candidate_header", "") or "") or None,
        pages=str(fm.get("pages", "") or "").strip(),
    )


def _thinking(fm: dict) -> bool:
    v = fm.get("thinking", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("disabled", "false", "off", "no", "0")
    return bool(v)


def _p(root: Path, s: str) -> Path:
    p = Path(s).expanduser()  # support '~/...' paths in config.yaml
    return p if p.is_absolute() else (root / p).resolve()


def _seed_sample(directory: Path, name: str, content: str) -> None:
    sample = directory / name
    if not sample.exists():
        sample.write_text(content, encoding="utf-8")


def _seed_default(directory: Path, content: str) -> None:
    """Seed `default.md` in `directory` only when it has no real definition
    yet (no non-underscore .md files). This gives a fresh archive a working
    zero-config default (qwen) without ever overwriting a user's files."""
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    has_def = any(
        f.is_file() and f.suffix == ".md" and not f.name.startswith("_")
        for f in directory.iterdir()
    )
    if not has_def:
        target = directory / "default.md"
        if not target.exists():
            target.write_text(content, encoding="utf-8")


def _seed_default_model(directory: Path, content: str) -> None:
    """Ensure a `default` model exists (it is the global fallback bound to a
    rules-only stage when pha.yaml/selection files name no model), without
    overwriting a user's model. Unlike `_seed_default`, this seeds even when
    OTHER models exist — `default` is a well-known referenced id, not just a
    fallback."""
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    has_default = any(
        f.is_file() and f.stem == "default" and f.suffix.lower() in (".md", ".txt")
        and not f.name.startswith(("_", "."))
        for f in directory.iterdir()
    )
    if not has_default:
        target = directory / "default.md"
        if not target.exists():
            target.write_text(content, encoding="utf-8")


def _migrate_legacy_defs(project_dir: Path, archive_dir: Path) -> None:
    """Copy real (non-underscore) definition files from the project dir into
    the archive dir when they are missing there, id-preserving. This lets a
    relocated archive keep its existing palaeographer/editor definitions that
    previously lived in the project, while a default (archive_dir = project
    root) archive is a no-op (the dirs are the same). Never overwrites."""
    if not project_dir.is_dir():
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(project_dir.iterdir()):
        if not f.is_file() or f.name.startswith(("_", ".")):
            continue
        if f.suffix.lower() not in (".md", ".txt"):
            continue
        dest = archive_dir / f.name
        if not dest.exists():
            dest.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")


# --- zero-config defaults (seeded into the archive as default.md) ----------
# All three stages point at the same local model (qwen3-vl-8b via LM Studio)
# so a fresh archive works with no configuration. qwen3-vl handles both vision
# (palaeographer) and text (editor/encoder). The model interface lives in
# models/default.md; the stage files below are content-only and reference it.

_DEFAULT_MODEL = """---
description: default model — qwen3-vl-8b via LM Studio (local)
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
api_key: ""
api_style: openai
max_vision_px: 1800
vision_jpeg_quality: 88
context_tokens: 32768
---

"""

_DEFAULT_PAL = """---
description: default palaeographer — generic transcription (qwen3-vl-8b local)
temperature: 0.1
max_tokens: 4096
timeout_s: 900
---

You are an expert paleographer in historical documents. Analyse the attached
file and provide:

Transcription: Provide a verbatim transcription of the text, keeping the
original line breaks.

Visual Notes: Describe any difficult-to-read sections, annotations, or layout
features (like marginalia).

Uncertainties: Use brackets [?] for words you aren't 100% sure about based on
the context.

Do not add any comments other than those above.
"""

_DEFAULT_ED = """---
description: default editor — expand abbreviations, extract named entities + notes (qwen3-vl-8b local)
temperature: 0.0
max_tokens: 4096
timeout_s: 300
---

You are a scholarly editor of transcribed historical texts.

Work from the transcription provided. Two tasks:

1. **Expansion and clean-up** — expand abbreviations into their full words
   using the conventions of the period and language, WITHOUT needing an
   exhaustive list: expand any contracted form you recognise. When an
   expansion is uncertain, keep the abbreviation and add the hypothesis in
   square brackets, e.g. "p[adre]". Do not modernize orthography beyond
   expanding abbreviations. Keep line breaks and any footnotes.

2. **Named entities and notes** — extract the named entities present in the
   text and list them in a `## Notes` section at the end:

   ## Notes

   ### Named entities

   - one entity per line, ALWAYS a bullet "- Name (role, place or context)"
   - people (with role/occupation), places, and institutions
   - NEVER join several entities on one line
   - if there are none, write exactly: - none

CRITICAL — preserve NON-LATIN characters exactly: any Greek, Hebrew, or
Chinese characters must be reproduced unchanged, not romanized, translated,
or dropped.

Output the edited transcription followed by the ## Notes section, with no
preamble or commentary.
"""

_DEFAULT_ENC = """---
description: default encoder — JSON dump of document metadata + named entities + notes (qwen3-vl-8b local)
temperature: 0.0
max_tokens: 4096
timeout_s: 300
batch_pages: 20
overlap_pages: 4
extraction_passes: 1
---

You extract a structured JSON record from a transcription. The input is the
document as ONE concatenated text with '--- page N ---' markers between
pages. Return a single JSON object (NOT an array) that dumps the document's
metadata, named entities and notes — i.e. everything the editor produced
besides the transcription:

{
  "document": {"title": <exact text or null>, "type": <...>, "page": <...>},
  "named_entities": [
    {"text": "<exact text>", "type": "person|place|institution|other", "page": <int>}
  ],
  "notes": {"language": <...>, "script": <...>, "summary": <...>}
}

Use EXACT TEXT from the input for every value; do not paraphrase. Cite the
page each entity appears on when the input gives page markers. If the
transcription has no such content, return null/[] as appropriate. Output
ONLY the JSON object, with no preamble or commentary.
"""


_MODEL_SAMPLE = """---
# HOW TO CREATE A NEW MODEL (interface)
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the model id, e.g. "minimax-m3.md").
#   2. Edit the settings below: endpoint (base_url), server model name, api
#      key, wire format (api_style), and the limits (max_vision_px,
#      vision_jpeg_quality, context_tokens).
#   3. Reference it from a pha.yaml sidecar, e.g.
#      `palaeographer: {rules: <id>, model: <id>}`.
#   4. Save — the model is ready.
# Fields (all optional except base_url/model):
#   base_url: the API root (LM Studio/Ollama/vLLM/OpenAI/MiniMax/...).
#   model: the server-side model name (e.g. qwen/qwen3-vl-8b).
#   api_key: the API key, as ${ENV} or a literal (secrets stay in .env/keychain).
#   api_style: "openai" (default) or "anthropic" — wire format for ALL calls.
#   thinking: true/false — allow (or disable) reasoning-block models.
#   max_vision_px: longest image edge sent to a vision model (default 1800).
#   vision_jpeg_quality: JPEG quality when re-encoding for the vision path.
#   context_tokens: the model's input window in tokens (drives encoder chunking).
#
# NON-LLM ENGINES (no HTTP endpoint): set an `engine` to drive the palaeographer
# stage with a LOCAL OCR/parse tool instead of an LLM vision call. Engines have
# no base_url/model/api_key; set `engine` + the engine's settings instead:
#   engine: tesseract                # Tesseract OCR (needs `tesseract` on PATH)
#   tesseract_lang: por              # -l value ("por", "lat", "por+lat", ...; "" = tesseract default)
#   tesseract_psm: 6                 # optional --psm page-segmentation mode
#   engine: liteparse                # LiteParse `lit parse` (needs `lit` on PATH:
#                                    #   pip install liteparse | npm i -g @llamaindex/liteparse)
#   liteparse_lang: por              # --ocr-language (Tesseract format, e.g. "por", "fra")
#   liteparse_dpi: 300               # optional --dpi render resolution (default 150; 300 = quality)
#   liteparse_format: text           # output: "text" (default) | "markdown" | "json"
#                                    #   ("json" = text + per-item bboxes/confidence for a
#                                    #   later reasoning/encoder stage)
#   liteparse_ocr: fresh             # input: "fresh" (default) = OCR the rendered page
#                                    #   raster (ignores any embedded text layer; safe on
#                                    #   scans); "embedded" = parse the ORIGINAL source PDF
#                                    #   page, using its embedded/native text layer where
#                                    #   present (fast on typed PDFs; may surface an archive's
#                                    #   old low-quality layer); "prefer-embedded" = per page,
#                                    #   use the embedded layer only when it passes the quality
#                                    #   gate below, else OCR the raster. Non-PDF sources:
#                                    #   always fresh.
#   liteparse_embedded_min_chars: 200      # prefer-embedded gate: minimum non-whitespace
#                                          #   characters for the layer to be considered
#   liteparse_embedded_min_quality: 0.60   # prefer-embedded gate: ratio floor for "share of
#                                          #   characters that are letters" and "share of tokens
#                                          #   that look like words"
# Then select it per document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: <this-model-id>}
# (or inline `engine: tesseract`/`engine: liteparse` in the palaeographer's own
# front matter). Engines are a registry in model_client.PAGE_ENGINES — add a
# run_* helper + entry there (and the settings on Model/Palaeographer) for a new
# local tool.
# Files starting with '_' are ignored (this sample is never loaded).
description: example model — edit me
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
api_key: ""
api_style: openai
max_vision_px: 1800
vision_jpeg_quality: 88
context_tokens: 32768
---

"""


_MODEL_TESSERACT_SAMPLE = """---
# SAMPLE Tesseract OCR model — never loaded (name starts with '_').
# To USE it: copy this file to models/tesseract.md (drop the leading '_'),
# tweak the language, and select it per document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: tesseract}
# Tesseract is a LOCAL OCR engine, NOT an LLM: it needs the `tesseract`
# executable + its language data on PATH (mac: `brew install tesseract
# tesseract-lang`). It reads printed/typeset text well but is weak on dense
# handwriting — use a vision model for manuscripts.
description: Tesseract OCR (local) — printed/typeset text
engine: tesseract
tesseract_lang: por+lat      # -l value(s): "por", "lat", "por+lat", "fra", "eng" ("" = tesseract default)
tesseract_psm: 6             # optional --psm page-segmentation mode (3 = auto; 6 = single uniform block)
---


"""


_MODEL_LITEPARSE_SAMPLE = """---
# SAMPLE LiteParse model — never loaded (name starts with '_').
# To USE it: copy this file to models/liteparse.md (drop the leading '_'),
# tweak the settings, and select it per document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: liteparse}
# LiteParse is a LOCAL document/OCR parser, NOT an LLM: install the `lit` CLI
# (`pip install liteparse` or `npm i -g @llamaindex/liteparse`). It gives
# layout-preserved text and (with json) per-item bounding boxes/confidence a
# later reasoning/encoder stage can use.
# liteparse_ocr:
#   fresh (default) — OCR the rendered page raster, so LiteParse must OCR it
#     (no embedded text layer to fall back on; safe on historical scans).
#   embedded — parse the ORIGINAL source PDF page, using the PDF's embedded/
#     native text layer where present (faster on typed PDFs, but may surface
#     an archive's old low-quality layer). Non-PDF sources are always fresh.
#   prefer-embedded — decide PER PAGE: read the page's embedded text with
#     pymupdf and reuse it (parsing the source PDF) only when it passes the
#     quality gate (liteparse_embedded_min_chars / liteparse_embedded_min_quality),
#     else OCR the raster — so a good layer is never re-OCR'd and a junk one is
#     never trusted.
# liteparse_format:
#   text (default) — layout-preserved plain text as the transcript.
#   markdown — structured markdown.  json — text + per-item bboxes/confidence.
description: LiteParse (local document parser / OCR)
engine: liteparse
liteparse_lang: por          # --ocr-language (Tesseract format: "por", "fra", ...)
liteparse_dpi: 300           # optional --dpi render resolution (default 150; 300 = quality)
liteparse_ocr: fresh         # fresh (default) | embedded | prefer-embedded
liteparse_format: text       # text (default) | markdown | json
---


"""


_MODEL_LITEPARSE_EMBEDDED_SAMPLE = """---
# SAMPLE LiteParse model — REUSE a good embedded PDF text layer, else OCR —
# never loaded (name starts with '_'). To USE it: copy this file to
# models/liteparse-embedded.md (drop the leading '_') and pair it with a
# content-only palaeographer rules file in pha.yaml:
#   palaeographer: {rules: ocr, model: liteparse-embedded}
# LiteParse is a LOCAL document/OCR parser, NOT an LLM (install the `lit` CLI:
# `pip install liteparse` or `npm i -g @llamaindex/liteparse`).
# Why this variant: `liteparse_ocr: embedded` ALWAYS parses the original PDF
# page — great when the PDF carries a real text layer, harmful when that layer
# is the residue of a bad OCR pass. `prefer-embedded` decides PER PAGE: pha
# reads the page's embedded text with pymupdf (no OCR, no subprocess) and
# reuses it only when it passes the quality gate (enough text, mostly letters,
# tokens that look like words); otherwise the page is OCR'd from the rendered
# raster exactly as with `fresh`. So one model covers a mixed collection —
# born-digital or well-OCR'd pages are not re-OCR'd, scans with a junk layer
# are. Non-PDF sources (single images / folders of images) always OCR.
# Gate tunables (defaults shown; lower them for sparse pages, raise them if a
# poor layer still slips through):
#   liteparse_embedded_min_chars: 200      # non-whitespace chars on the page
#   liteparse_embedded_min_quality: 0.60   # ratio floor for letter-share and
#                                          # word-likeness (0..1)
description: LiteParse (local OCR) — reuse a good embedded PDF text layer, else OCR
engine: liteparse
liteparse_lang: por           # --ocr-language (Tesseract format: "por", "fra", ...)
liteparse_dpi: 300            # render resolution used when the page must be OCR'd
liteparse_ocr: prefer-embedded   # fresh | embedded | prefer-embedded
liteparse_format: text        # layout-preserved plain text
liteparse_embedded_min_chars: 200
liteparse_embedded_min_quality: 0.60
---


"""


_MODEL_LITEPARSE_FRA_SAMPLE = """---
# SAMPLE LiteParse model configured for printed FRENCH — never loaded (name
# starts with '_'). To USE it: copy this file to models/liteparse-fra.md (drop
# the leading '_') and pair it with a content-only palaeographer rules file in
# pha.yaml:  palaeographer: {rules: ocr, model: liteparse-fra}
# The `fra` Tesseract language data must be installed on the archive machine
# (mac: `brew install tesseract-lang`). See models/_sample.liteparse.md for the
# full explanation of liteparse_ocr / liteparse_format.
description: LiteParse (local OCR) — printed French
engine: liteparse
liteparse_lang: fra           # --ocr-language (Tesseract format: "fra")
liteparse_dpi: 300            # optional --dpi render resolution (300 = quality)
liteparse_ocr: fresh          # OCR the rendered raster ("embedded" only when
                              # the PDF's own text layer is good)
liteparse_format: text        # layout-preserved plain text
---


"""


_MODEL_LITEPARSE_SPA_SAMPLE = """---
# SAMPLE LiteParse model configured for printed SPANISH (+ Latin) — never
# loaded (name starts with '_'). To USE it: copy this file to
# models/liteparse-spa.md (drop the leading '_') and pair it with a content-only
# palaeographer rules file in pha.yaml:
#   palaeographer: {rules: ocr, model: liteparse-spa}
# The `spa` (+ `lat`) Tesseract language data must be installed on the archive
# machine. See models/_sample.liteparse.md for liteparse_ocr / liteparse_format.
description: LiteParse (local OCR) — printed Spanish (+ Latin)
engine: liteparse
liteparse_lang: spa+lat       # --ocr-language (Tesseract: "spa+lat")
liteparse_dpi: 300            # optional --dpi render resolution (300 = quality)
liteparse_ocr: fresh          # OCR the rendered raster
liteparse_format: text        # layout-preserved plain text
---


"""


_MODEL_QWEN3_LOCAL_SAMPLE = """---
# SAMPLE local vision model — qwen3-vl served by LM Studio — never loaded (name
# starts with '_'). To USE it: copy this file to models/qwen3-vision-local.md
# (drop the leading '_'), set the server-side model name, and select it per
# document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: qwen3-vision-local}
# This is the MODEL INTERFACE only (endpoint + limits); the transcription rules
# live in a content-only palaeographer file.
#   context_tokens MUST match the model's real window — it drives encoder
#     chunking (the Model default, 200000, is far too large for a local model).
#   thinking: disabled skips reasoning blocks (some builds leak
#     `<|channel>thought` into the transcript) — faster and cleaner.
#   timeout_s is NOT a model field: the stage timeout lives in the
#     palaeographer/editor rules file front matter.
description: qwen3-vl via LM Studio (local) — vision + text
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
api_key: ""
api_style: openai
max_vision_px: 1800
vision_jpeg_quality: 88
context_tokens: 32768
thinking: disabled
---


"""


_MODEL_GEMMA4_LOCAL_SAMPLE = """---
# SAMPLE local vision model — gemma-4 served by LM Studio — never loaded (name
# starts with '_'). To USE it: copy this file to models/gemma4-vision-local.md
# (drop the leading '_'), set the server-side model name (e.g.
# `google/gemma-4-e4b`, or the `-mlx` build on Apple Silicon), and select it per
# document/collection in pha.yaml:
#   palaeographer: {rules: <rules-id>, model: gemma4-vision-local}
# This is the MODEL INTERFACE only (endpoint + limits); the transcription rules
# live in a content-only palaeographer file.
#   max_vision_px 3000 keeps dense printed pages legible (the page only reaches
#     that size when the collection's render setting allows it).
#   thinking: disabled avoids the `<|channel>thought` blocks gemma-4 can emit.
#   timeout_s is NOT a model field: set it in the stage rules file.
description: gemma-4 via LM Studio (local) — vision + text
base_url: http://127.0.0.1:1234/v1
model: google/gemma-4-e4b
api_key: ""
api_style: openai
max_vision_px: 3000
vision_jpeg_quality: 88
context_tokens: 32768
thinking: disabled
---


"""


_PAL_SAMPLE = """---
# HOW TO CREATE A NEW PALAEOGRAPHER (transcription rules)
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the palaeographer's id, e.g. "my-hand.md").
#   2. This file is CONTENT ONLY — the model is chosen in pha.yaml.
#   3. Replace this body with the instructions the vision model should follow
#      when transcribing (your palaeographic expertise).
#   4. Save — the palaeographer is ready. Select it per document/collection in
#      pha.yaml (palaeographer.rules) or a 'palaeographer' file.
#   To drive this stage with a LOCAL OCR/parse tool (not an LLM) instead of a
#   vision model, reference a model file with an `engine` — e.g. pha.yaml:
#   palaeographer: {rules: <this-id>, model: tesseract} (or ...model: liteparse)
#   — or put `engine: tesseract`/`engine: liteparse` (+ its settings) directly
#   in this file's front matter.
# Optional front matter:
#   temperature: sampling temperature (default 0.1).
#   max_tokens: completion token cap (default 4096).
#   timeout_s: HTTP timeout in seconds (default 900 for vision).
# Files starting with '_' are ignored (this sample is never loaded).
description: example palaeographer — edit me
temperature: 0.1
max_tokens: 4096
timeout_s: 900
---

You are a palaeographer specialised in reading historical documents
(edit this description and the rules to match your documents' tradition,
language and period). Transcribe the page faithfully; mark [illegible] parts;
add a "## Notes" section in English with only READING NOTES (Language, Script,
difficult words) — do NOT produce named-entity lists or content summaries
(those belong to the editor/encoder). This is
one page of a multi-page document — do not comment on completeness.
"""


_PAL_OCR_SAMPLE = """---
# SAMPLE OCR palaeographer — never loaded (name starts with '_').
# OCR engines (tesseract / liteparse) IGNORE the prompt body below — the OCR
# behaviour and its settings live in the MODEL the rules are paired with
# (models/tesseract.md: tesseract_lang/psm; models/liteparse.md:
# liteparse_lang/dpi/ocr/format). This content file only NAMES the reading pass
# and gives it a friendly description (shown by `pha palaeographer` and in the
# library headers).
# To USE it: copy this file to palaeographers/ocr.md (drop the leading '_') and
# pair it with an OCR model per document/collection in pha.yaml, e.g.:
#   palaeographer: {rules: ocr, model: tesseract}    # or: model: liteparse
# For a distinct id per engine (nicer library folder names), copy it twice as
# ocr-tesseract.md and ocr-liteparse.md — the content is the same either way.
description: OCR palaeographer (printed/typeset transcript via a local OCR engine; pair it with model tesseract or liteparse)
---
This page is transcribed by a LOCAL OCR engine (Tesseract or LiteParse), NOT a
vision model — no prompt is sent. The engine and its settings (language, dpi,
fresh/embedded OCR, output format) are defined by the model this rules file is
paired with; see the samples in models/_sample.tesseract.md and
models/_sample.liteparse.md. The engine's layout-preserved text becomes this
page's transcript, so the transcription is a faithful machine reading —
correct obvious OCR slips later in the editor pass or by review.
"""


_PAL_PRINTED_BOOKS_SAMPLE = """---
# SAMPLE palaeographer — generic printed books, 19th-20th century — never
# loaded (name starts with '_'). CONTENT ONLY: pair it with a model in pha.yaml,
# e.g.  palaeographer: {rules: printed-books, model: qwen3-vision-local}
description: generic transcription of 19th-20th-century printed books
temperature: 0.1
max_tokens: 4096
timeout_s: 900
---
You are an expert paleographer in printed books 19-20 centuries. Analyse the attached file and provide:

Transcription: Provide a verbatim transcription of the text, keeping the original line breaks.

Uncertainties: Use brackets [?] for words you aren't 100% sure about based on the context.

After the transcription, add `## Notes` (in English) with only READING NOTES:

Language: ... (the language of the page)
Script: ... (19th-20th-century printed type)
Difficult words: ... (any words you had to work out)

Do not add any comments other than those above.
"""


_PAL_PRINTED_CRITICAL_SAMPLE = """---
# SAMPLE palaeographer — generic modern printed critical editions — never loaded
# (name starts with '_'). CONTENT ONLY: pair it with a model in pha.yaml, e.g.
#   palaeographer: {rules: printed-critical-edition, model: qwen3-vision-local}
description: Generic printed historical-document transcription (Latin/Portuguese/Spanish,
  modern critical editions)
temperature: 0.1
max_tokens: 8000
timeout_s: 1800
---
You are an expert palaeographer transcribing ONE page of a printed historical
document (Latin, Portuguese, Spanish, or another Western European language).
This is a modern printed critical edition, NOT a manuscript: clean modern
Roman/italic type, black on white.

Transcribe the page verbatim exactly as it appears, keeping the original line
breaks and paragraph structure. Transcribe only what is visible on this one
page; do not look ahead to neighbouring pages.

TRANSCRIPTION RULES

1. Preserve the exact original spelling, diacritics (ã, õ, ç, á, é, í, ó, ú, à,
   â, ê, ô, ü, and macrons) and punctuation. Do NOT modernise, translate, or
   expand abbreviations. Expansion belongs to the editor pass.
2. Transcribe the RUNNING HEAD, printed PAGE NUMBER, section/document headings,
   body, and any FOOTNOTE block at the bottom.
3. Keep editorial square brackets [ ] exactly, and superscript footnote-marker
   numerals exactly where they appear (e.g. "mittere jubebatur8").
4. Mark anything you cannot read as `[illegible]`; mark uncertain words with
   `[?]`.
5. NEVER repeat a word, phrase, list, or clause. If a page (or part of it) is
   faded, blurred, bleed-through, or unreadable, output `[illegible]` for it and
   STOP immediately. Do NOT guess, enumerate, or invent text.
6. Do NOT translate, and do NOT produce named-entity lists or content
   summaries — those belong to the editor/encoder passes.

After the transcription, add `## Notes` (in English) with only READING NOTES:

Language: ... (the language(s) of the page)
Script: ... (20th-century printed Roman/italic type)
Document / heading: ... (the document/heading and running head, if legible)
Page: ... (the printed page number)
Difficult words: ... (any words you had to work out)

Do not add any commentary beyond the Transcription and Notes.
"""


_ED_SAMPLE = """---
# HOW TO CREATE A NEW EDITOR (transform rules)
#   1. Duplicate this file and give it a new name (the file name, without the
#      extension, becomes the editor's id, e.g. "translate-english.md").
#   2. This file is CONTENT ONLY — the model is chosen in pha.yaml.
#   3. Replace this body with your editing instructions (e.g. convert to
#      modern Portuguese, translate to English, normalize names).
#   4. Save — the editor is ready. Select it per document/collection in
#      pha.yaml (editor.rules) or an 'editor' file.
# Optional front matter:
#   temperature: sampling temperature (default 0.1).
#   max_tokens: completion token cap (default 4096).
#   timeout_s: HTTP timeout in seconds (default 300 for text).
# Files starting with '_' are ignored (this sample is never loaded).
description: example editor — edit me
temperature: 0.0
max_tokens: 4096
timeout_s: 300
---

You are a scholarly editor. Transform the transcription as requested by these
instructions. Keep the content faithful: do not add, remove or reorder
information. Keep the document structure. Output only the edited text.
"""


_ED_GENERIC_SAMPLE = """---
# SAMPLE editor — general-purpose scholarly editing of printed/historical texts
# — never loaded (name starts with '_'). CONTENT ONLY: pair it with a text model
# in pha.yaml, e.g.  editor: {rules: generic, model: qwen3-vision-local}
description: "General-purpose editor — expand abbreviations, extract named entities, preserve non-Latin scripts"
temperature: 0.0
max_tokens: 4096
timeout_s: 300
---
You are a scholarly editor of transcribed historical texts.

Work from the transcription provided. Two tasks:

1. **Expansion and clean-up** — expand abbreviations into their full words
   using the conventions of the period and language, WITHOUT needing an
   exhaustive list: expand any contracted form you recognise (e.g. "q" ->
   "que", "dñs" -> "Dominus", "St" -> "Saint", "N." -> "natus/né"). When an
   expansion is uncertain, keep the abbreviation and add the hypothesis in
   square brackets, e.g. "p[adre]". Do not modernize orthography beyond
   expanding abbreviations — keep the rest as transcribed. Do not reorder or
   remove content. Keep line breaks, paragraph structure and any footnotes.

2. **Named entities** — extract the named entities present in the text and
   list them in a `## Notes` section at the end:

   ## Notes

   ### Named entities

   - one entity per line, ALWAYS a bullet "- Name (role, place or context)"
   - people (with role/occupation), places, and institutions
   - use the name as it appears (or its common form if clearly identifiable)
   - NEVER join several entities on one line
   - if there are none, write exactly: - none

CRITICAL — preserve NON-LATIN characters exactly: any Greek, Hebrew, or
Chinese characters (and other non-Latin scripts) in the transcription must
be reproduced unchanged, not romanized, translated, or dropped.

Output the edited transcription followed by the ## Notes section, with no
preamble or commentary.
"""


_ENC_SAMPLE = """---
# HOW TO CREATE A NEW ENCODER (structured-extraction rules)
#   1. Create a folder next to your documents: dropbox/collections/COLX/encoders/
#      and add one file per STRUCTURE TYPE in the document (e.g. table.md for
#      the chronological table, biographies.md for the person notices). The
#      encoder files travel with the source PDFs.
#   2. This file is CONTENT ONLY — the model is chosen in pha.yaml.
#   3. `pages: "1-15"` limits this encoder to those PDF page numbers (the
#      number in the PDF, NOT the number printed on the page — e.g. Pfister's
#      chronological table is printed as i–xv but occupies PDF pages 1-15).
#      Empty = the whole document. Multiple encoders run in page order.
#   4. Replace this body with the generic encoding framing (what records to
#      produce, output format). Collection-specific detection rules go in
#      encoders/<name>.prompt.md next to this file.
#   5. Add a '## Examples' section — or, better, put schema + examples in
#      encoders/<name>.langextract.md. To produce these without knowing the
#      format, run 'pha encoder new' or paste prompts/encoder-helper.md into
#      any chat model.
# The encoder is fed the document as ONE CONCATENATED text ('--- page N ---'
# markers between pages), in a single call when it fits the model window;
# larger documents are chunked with overlap_pages of overlap and records are
# deduplicated. Ask the model to cite the page each record starts on and to
# use EXACT TEXT from the input. extraction_passes > 1 re-runs the whole
# extraction and keeps first-pass-wins (LangExtract-style recall boost).
# Output items use the flat form {class: text, class_attributes: {...}} and
# may mix several classes (e.g. person + letter) in one array; each item is
# stored as one record with kind = class.
# The model's context window (single-pass/chunked threshold) lives in the
# model file (context_tokens); set max_input_chars here to override it.
# Optional front matter:
#   temperature: sampling temperature (default 0.0).
#   max_tokens: completion token cap (default 4096).
#   timeout_s: HTTP timeout in seconds (default 300 for text).
#   batch_pages / overlap_pages / extraction_passes: chunking + recall knobs.
# Files starting with '_' are ignored (this sample is never loaded).
description: example encoder — edit me
temperature: 0.0
max_tokens: 4096
timeout_s: 300
batch_pages: 20
overlap_pages: 4
extraction_passes: 1
---

You extract structured records from transcriptions. You are given the
document as a single CONCATENATED text with '--- page N ---' markers
between pages — records may span several pages, so read the whole text
before deciding. Use EXACT TEXT from the input for every extracted value
(do not paraphrase), and list records in order of appearance. Return a
JSON array of extraction items in the flat form:

  {"<class>": "<exact text from the input>",
   "<class>_attributes": {<attribute>: <value>, ...}}

Each item may carry its own class (e.g. "person", "letter", "date"), so one
array can contain several classes. Output ONLY the JSON array, with no
preamble or commentary. Follow the '## Examples' section below for the
exact classes, attributes and shapes expected.

## Examples

Q: <paste one sample passage from your material>

A:
[{"<class>": "<exact text>", "<class>_attributes": {<attribute>: <value>}}]
"""


def builtin_samples() -> tuple[tuple[str, str, str], ...]:
    """The ready-to-duplicate BUILTIN samples shipped with pha, as
    (subdir, filename, content).

    These are the starting points for new definitions (and the discoverable
    catalogue of what pha supports out of the box). Every name starts with '_',
    so the loader ignores them: they are copied to a real id to be used.
    Shared by Config.ensure_dirs (project side) and archive_init.init_archive
    (archive side) so the two lists can never drift apart.
    """
    return (
        # how-to-create templates
        ("models", "_sample.md", _MODEL_SAMPLE),
        ("palaeographers", "_sample.md", _PAL_SAMPLE),
        ("editors", "_sample.md", _ED_SAMPLE),
        ("encoders", "_sample.md", _ENC_SAMPLE),
        # local OCR/parse engines (not LLMs)
        ("models", "_sample.tesseract.md", _MODEL_TESSERACT_SAMPLE),
        ("models", "_sample.liteparse.md", _MODEL_LITEPARSE_SAMPLE),
        ("models", "_sample.liteparse.embedded.md", _MODEL_LITEPARSE_EMBEDDED_SAMPLE),
        ("models", "_sample.liteparse.fra.md", _MODEL_LITEPARSE_FRA_SAMPLE),
        ("models", "_sample.liteparse.spa.md", _MODEL_LITEPARSE_SPA_SAMPLE),
        ("palaeographers", "_sample.ocr.md", _PAL_OCR_SAMPLE),
        # local vision/text models served by LM Studio
        ("models", "_sample.local-qwen3-vl.md", _MODEL_QWEN3_LOCAL_SAMPLE),
        ("models", "_sample.local-gemma4.md", _MODEL_GEMMA4_LOCAL_SAMPLE),
        # generic printed-book content rules
        ("palaeographers", "_sample.printed-books.md", _PAL_PRINTED_BOOKS_SAMPLE),
        ("palaeographers", "_sample.printed-critical-edition.md", _PAL_PRINTED_CRITICAL_SAMPLE),
        ("editors", "_sample.generic.md", _ED_GENERIC_SAMPLE),
    )


# Seeds `notes/README.md` in an archive (created once, never overwritten). The
# notes folder stores Obsidian-compatible markdown notes generated from queries
# to the archive; this file tells humans and agents how to format and cite them.
_NOTES_README_MD = """# Notes
This folder stores **Markdown notes generated from queries to this archive** —
human- or agent-written research notes that summarize what the archive holds on
a topic. Notes live in the archive itself (a sibling of `dropbox/`, `library/`,
etc.) and are **Obsidian compatible**: use `[[wikilinks]]` to connect notes and
`[^1]`-style footnotes for citations.

For the wider picture — embedding a page image in a note, and letting an agent
read or write a personal Obsidian vault — see `obsidian-integration.md` in this
folder.

## Why a notes folder?

`pha search` returns snippets and `pha page` returns a full page; a note is the
step after that. It pulls together hits across several documents/pages into one
human-readable summary with links and provenance, so the next query — or the
next person — does not have to re-search from scratch.

## Note format (Obsidian compatible)

- **Filename**: `lowercase-hyphenated.md` (e.g. `malaca.md`). One note per
  topic; keep it focused.
- **Wikilinks** `[[Note Name]]` link between notes. **Only link to notes that
  already exist in this `notes/` folder and that are genuinely related to the
  new note's content. Never leave a `[[wikilink]]` pointing at a note that does
  not exist.** If you are tempted to link to a not-yet-written topic, instead
  mention it in plain prose (or create that note now and then link it). Before
  finishing a note, check that every `[[wikilink]]` resolves to a real file.
- **Footnotes** use standard Markdown/Obsidian footnote syntax:

      The port was fortified in 1547.[^1]

      [^1]: *Historians' description of Malaca*, DocHistMissPadPortOriente vol02, doc 20, p. 187.

  The next footnote is `[^2]`, the next `[^3]`, and so on; the definition
  block (each `[^n]: ...` line) sits at the end of the file.
- **Front matter** (recommended) is a YAML block at the very top, delimited by
  `---` lines:

      ---
      title: Malaca
      created: 2026-09-08
      tags: [portugal, malaca, 16c]
      sources: ["DocHistMissPadPortOriente", "Documenta Indica"]
      ---

- Use ordinary Markdown for headings, tables, quotes and inline code. Keep the
  Obsidian-specific syntax to wikilinks + footnotes so the files also render in
  any Markdown viewer.

## How to cite an archive source

Every claim in a note should be traceable back to the archive. Cite the
**document + page number** and say which variant you used — a page can have
several (the raw transcription plus one or more edited versions, each by a
different model), and a claim rests on one of them.

- `pha search "Malaca"` → hits carry a `page_file` and a page number.
- `pha page <doc> <page>` prints a page's full transcription;
  `pha page <doc> <page> --edited` prints the edited/translated variant.
- `<doc>` is a document id or a filename substring, so both
  `pha page 19 379` and `pha page DOCUMENTA-INDICA 379` work.
- `pha cite <doc> <page> [--edited]` prints a paste-ready citation naming the
  stable **slug** and the exact **filled** variant. Prefer it to hand-writing a
  path: it refuses to cite an empty (`*waiting*`) variant, and when several
  filled variants exist it lists them and asks for `--editor` /
  `--palaeographer` rather than guessing.

Cite as a footnote, e.g.:

    Malaca's Jewish community is described in the 1548–50 register.[^1]

    [^1]: *DocHist do Padroado do Oriente* vol04 (doc 22), p. 437, edited —
          `pha page 22 437 --edited`.

The footnote carries enough to open the page: the document (doc 22) and the page
(437). An agent asked to "show the page referred to in footnote 12" runs
`pha page 22 437 --edited` and reports the text.

### Durable links (optional — but they don't rot)

A document's numeric id, its dated library folder (`<stem>_YYYY-MM-DD`) and its
`renders/<sha256>/` directory all change when the document is re-processed, so
never embed those in a note. The **slug** does not change: it derives from the
dropbox-relative path (no date, no hash), and only renaming or moving the source
file changes it.

`pha page <doc> <page> --json` reports the slug, the relative path, the current
sha, the render path and the full variant set. With `pha serve` running
(read-only, loopback) a page image can be embedded and keeps working across
re-scans:

    ![](http://127.0.0.1:8765/doc/<slug>/p437.jpg)

The endpoint resolves the document's current render per request, so a re-scan
changes the bytes behind the URL without breaking the note.

**Such a link resolves only while that server is running** — start it with
`pha serve` (read-only, loopback `http://127.0.0.1:8765`). **A note that embeds
one must carry a short warning near the top**, so a reader who sees a missing
image knows it is the server, not the note. Full setup and caveats:
`obsidian-integration.md`.

## How an agent should create a note

1. **Search** the archive for the topic: `pha search "Malaca"` (or the
   `pha_search` MCP tool).
2. **Read each hit in full** — run `pha page <doc> <page>` (and `--edited` when
   available) to get the complete page text. Do not summarize from a snippet
   alone.
3. **Synthesize** into a single note in THIS folder:
   - State only what the archive supports; say what it does *not* contain.
   - Cite each fact to its document + page in a footnote (`pha cite <doc> <page>`
     gives you the slug and the exact variant).
   - Link to other notes with `[[wikilinks]]`; create a new note when another
     topic deserves its own page.
4. **Save it** here as `lowercase-hyphenated.md` (create any linked-topic note
   too), and make sure every footnote `[^n]` has a matching definition.
5. Re-check that no citation points at the wrong page, that every `[[wikilink]]`
   resolves to a real file, and that all footnotes resolve.

### Recording the ask

Optional: put the prompt/query that produced the note in an HTML comment at the
top, so a later reader knows what question it answers:

```
<!-- Prompt: search the archive for "Malaca" and summarize available
     information in a new note in this archive. -->
```

## Example prompt

> Search the archive for "Malaca" and summarize available information in a new
> note in this archive.

The agent answers by writing e.g. `malaca.md` in this folder: the archive to be
searched, the documents/pages it found, a synthesis of what they say, a
footnote per page cited, and `[[wikilinks]]` to any genuinely related notes that
already exist in this folder.
"""


def notes_readme_template(project_root: Path) -> str:
    """The canonical `notes/README.md` body that is seeded into an archive's
    `notes/` folder (once, never overwritten).

    The repo's own `notes/README.md` is the authoritative copy when the project
    is checked out (so editors keep one source of truth); the inline
    `_NOTES_README_MD` is the fallback for vendored / no-repo-file installs.
    """
    p = project_root / "notes" / "README.md"
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    except OSError:
        pass
    return _NOTES_README_MD

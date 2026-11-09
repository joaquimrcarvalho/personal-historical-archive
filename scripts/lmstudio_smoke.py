#!/usr/bin/env python3
"""LM Studio model-store smoke test.

Answers one question: after the LM Studio model files were moved (typically to
an external disk), do the models this machine actually uses still load and do
real work?

It lives in the pha source checkout (``scripts/``); the wheel does not ship
it, so a wheel-only install does not have it. It is deliberately standalone
(standard library only), so it runs with the system ``python3``.

What it checks
  1. server        the endpoint answers (``/v1/models``; LM Studio's native
                   ``/api/v0/models`` when present).
  2. model store   every tested model resolves to a file/dir inside the expected
                   model directory, and the tool reports *where* the weights
                   really are (external store, LM Studio bundled model, hub
                   metadata, or somewhere stale). If the external disk is not
                   mounted this fails before any slow model load.
  3. locality      the model is loaded on THIS machine, not routed by LM Link to
                   a peer that happens to have the same model loaded. A request
                   answered by another computer proves nothing about the moved
                   local files.
  4. embeddings    the embedding model returns a usable vector set: right
                   count/dimension, finite values, deterministic, and
                   semantically ordered (a question is closer to its answer than
                   to an unrelated sentence).
  5. vision        each vision model reads a fixed image of printed text and
                   gets it right, then counts/names coloured shapes. That is a
                   real inference, not just an HTTP 200.
  6. archive       optional (``--archive-dir``): the archive's configured
                   embedding model is the one tested and the vectors already in
                   ``archive.db`` have the same dimension, so semantic search
                   still works after the move.

The text image is the committed fixture ``lmstudio_smoke_text.png`` next to this
script (rendered once with a real system font: blocky hand-drawn glyphs are
misread by some small VLMs, which would make the test lie). The shapes image is
drawn at run time. Models are loaded on demand through LM Studio's just-in-time
loading; models this script caused to load are unloaded afterwards
(``--keep-loaded`` keeps them).

Exit code: 0 when every check passed, 1 when a check failed, 2 when the test
could not run at all.

Examples
--------
    # the archive's embedding model + every vision model on this machine
    python3 scripts/lmstudio_smoke.py --archive-dir ~/jesuit-archive

    # weights must come from the external SSD (fails if it is unplugged)
    python3 scripts/lmstudio_smoke.py \\
        --model-store /Volumes/ARSI/models \\
        --archive-dir ~/jesuit-archive

    # a one-model quick probe
    python3 scripts/lmstudio_smoke.py --vision-model qwen --vision-only

    # local vs remote: which models are on this device, which only via LM Link
    python3 scripts/lmstudio_smoke.py --list

    # force local copies under unique ids (deterministic with LM Link)
    python3 scripts/lmstudio_smoke.py --pin-local --vision-model gemma
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"

# The text fixture is a committed PNG next to this script: small VLMs misread
# pixel-font glyphs (S -> 5, A -> H), so the image must look like ordinary
# printed text. Regenerate it with any real font at ~120 px if the text changes.
TEXT_FIXTURE = Path(__file__).resolve().with_name("lmstudio_smoke_text.png")

# --- embedding fixture -------------------------------------------------------
# Two paraphrases of the same fact and one unrelated sentence. pha prefixes
# nomic strings with search_document:/search_query: (see embed.py), so we do
# the same when the model id says "nomic" — the test then exercises the exact
# text path the archive index uses.
EMBED_DOCS = [
    "The Jesuit mission in Japan began in 1549 with Francis Xavier.",
    "Francis Xavier arrived in Japan in 1549 to preach Christianity.",
    "Photosynthesis converts light energy into chemical energy in plants.",
]
EMBED_QUERY = "When did Francis Xavier arrive in Japan?"

# --- vision fixture ----------------------------------------------------------
TEXT_IMAGE_TOKENS = ["PHA", "SSD", "TEST", "42", "2026"]
TEXT_PROMPT = (
    "Transcribe every word printed in this image. "
    "Reply with the transcription only, no explanation."
)
COUNT_PROMPT = "How many separate shapes are in this image? Reply with a single number."
COLOR_PROMPT = (
    "Name the color of each shape in this image. "
    "Reply with only the color names, separated by commas."
)
COUNT_EXPECTED = 3
COLORS_EXPECTED = ["red", "blue", "green"]


class ProbeError(RuntimeError):
    """A check could not complete (transport, timeout, bad response)."""


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
class Report:
    def __init__(self, emit: bool = True):
        self.emit = emit
        self.checks: list[dict] = []
        self.section_name = ""
        self.counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}

    def section(self, title: str, meta: str = "") -> None:
        self.section_name = title
        if self.emit:
            print()
            print(f"{title}  {meta}" if meta else title)

    def check(self, name: str, status: str, detail: str = "") -> None:
        status = status.upper()
        if status in self.counts:
            self.counts[status] += 1
        self.checks.append(
            {"section": self.section_name, "check": name, "status": status, "detail": detail}
        )
        if self.emit:
            mark = {"PASS": "ok  ", "FAIL": "FAIL", "WARN": "warn", "SKIP": "skip"}.get(status, "info")
            print(f"  {mark}  {name}" + (f": {detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return self.counts["FAIL"] > 0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_json(url: str, payload: dict | None = None, timeout: float = 600.0):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()[:800]
        raise ProbeError(f"HTTP {exc.code} from {url}: {body.decode('utf-8', 'replace')}") from exc
    except urllib.error.URLError as exc:
        raise ProbeError(f"cannot reach {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise ProbeError(f"request to {url} failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProbeError(f"non-JSON answer from {url}: {exc}") from exc


def split_base_url(base_url: str) -> tuple[str, str]:
    """Return (OpenAI api root, server root) for a user-supplied base URL."""
    url = base_url.strip().rstrip("/")
    if url.endswith("/v1"):
        return url, url[: -len("/v1")]
    return url + "/v1", url


# ---------------------------------------------------------------------------
# child processes
# ---------------------------------------------------------------------------
def run_utf8(argv: list[str], timeout: float):
    """Run a child that writes UTF-8 (LM Studio's ``lms`` is Node).

    Text mode without an explicit encoding decodes with the locale code page;
    on Windows that is cp950/cp1252 while ``lms`` writes UTF-8, and the reader
    thread dies with ``UnicodeDecodeError`` (the same failure `pha update` hit
    through pnpm). This script is standalone and cannot import pha's
    ``subproc.py``, so it states the encoding itself.
    """
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


# ---------------------------------------------------------------------------
# LM Studio CLI helpers (locality + on-disk model facts)
# ---------------------------------------------------------------------------
def lms_executable() -> str | None:
    found = shutil.which("lms")
    if found:
        return found
    candidate = Path.home() / ".lmstudio" / "bin" / "lms"
    return str(candidate) if candidate.exists() else None


def lms_json(args: list[str], timeout: float = 180.0):
    exe = lms_executable()
    if not exe:
        return None
    try:
        proc = run_utf8([exe, *args], timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        return None
    if text.startswith(("[", "{")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # lms occasionally prints log/progress lines before the JSON body.
    for opener in ("[", "{"):
        start = text.find(opener)
        if start < 0:
            continue
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue
    return None


def lms_ps() -> list[dict]:
    data = lms_json(["ps", "--json"])
    return [entry for entry in (data or []) if isinstance(entry, dict)]


def lms_link() -> dict:
    data = lms_json(["link", "status", "--json"])
    return data if isinstance(data, dict) else {}


def lms_inventory(link: dict) -> dict[str, dict]:
    """Model key -> on-disk entry, limited to copies that live on THIS machine."""
    data = lms_json(["ls", "--json"])
    if not data:
        return {}
    current = link.get("deviceIdentifier")
    inventory: dict[str, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        dev = entry.get("deviceIdentifier")
        rel = str(entry.get("path") or "")
        if dev not in (None, "") and str(dev) != str(current):
            continue
        if re.match(r"^[0-9a-f]{32}:", rel):  # remote copy: path is device-id-prefixed
            continue
        key = str(entry.get("modelKey") or "")
        if key:
            inventory[key] = entry
    return inventory


def device_names(link: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for peer in link.get("peers") or []:
        dev = peer.get("deviceIdentifier")
        if dev:
            names[str(dev)] = str(peer.get("deviceName") or dev)
    dev = link.get("deviceIdentifier")
    if dev:
        names[str(dev)] = str(link.get("deviceName") or dev)
    return names


def is_on_this_device(entry: dict, current: str | None) -> bool:
    dev = entry.get("deviceIdentifier")
    return dev in (None, "") or (current is not None and str(dev) == str(current))


# ---------------------------------------------------------------------------
# Where the weights really are
# ---------------------------------------------------------------------------
def candidate_stores(explicit: str | None) -> list[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    found: list[Path] = []
    settings = Path.home() / ".lmstudio" / "settings.json"
    try:
        downloads = json.loads(settings.read_text(encoding="utf-8")).get("downloadsFolder")
        if downloads:
            found.append(Path(downloads).expanduser())
    except (OSError, json.JSONDecodeError):
        pass
    found.append(Path.home() / ".lmstudio" / "models")
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def store_relpath(entry: dict) -> str:
    """Path of the model inside the model store.

    Hub-installed models carry ``qwen/qwen3-vl-4b@lmstudio-community/...``; the
    part after '@' is the base model that actually lives under the store root.
    """
    ident = str(entry.get("indexedModelIdentifier") or entry.get("path") or "")
    if "@" in ident:
        ident = ident.split("@", 1)[1]
    return ident


def resolve_in_store(store: Path, entry: dict) -> Path | None:
    target = store / store_relpath(entry)
    return target if target.exists() else None


def model_index_cache() -> tuple[dict[str, dict], dict[str, str]]:
    """LM Studio's own index of model directories (absolute paths).

    Used only as a fallback: a hub "virtual" model or a bundled model has no
    file directly under the model store, and this is where LM Studio records the
    directory it actually reads. Absent/renamed on other versions -> empty.

    Returns (entries by indexed identifier, virtual model -> base model key).
    """
    path = Path.home() / ".lmstudio" / ".internal" / "model-index-cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    cache: dict[str, dict] = {}
    bases: dict[str, str] = {}
    for entry in data.get("models", []):
        ident = str(entry.get("indexedModelIdentifier") or "")
        if not ident:
            continue
        cache[ident] = entry
        if "@" in ident:
            virtual, base = ident.split("@", 1)
            bases.setdefault(virtual, base)
    return cache, bases


def resolve_model_files(entry: dict, stores: list[Path]) -> tuple[Path | None, str, Path | None]:
    """Resolve an lms entry to the path actually read, plus where that is.

    Returns (path, origin, store) where origin is one of ``store``, ``cache`` or
    ``unknown`` and store is the expected store the path belongs to (or None).
    """
    for store in stores:
        path = resolve_in_store(store, entry)
        if path is not None:
            return path, "store", store
    cache, bases = model_index_cache()
    if cache:
        ident = str(entry.get("indexedModelIdentifier") or "")
        keys: list[str] = []
        if "@" in ident:  # hub virtual model -> its base model on disk
            keys.append(ident.split("@", 1)[1])
        keys.extend([store_relpath(entry), str(entry.get("path") or "")])
        for key in keys:
            hit = cache.get(key)
            if not hit:
                continue
            # A hub entry ("qwen/qwen3-vl-4b") records the metadata directory;
            # the weights belong to the BASE model it composes, which is also in
            # the index (usually under the external store).
            base = bases.get(key)
            if base and base in cache:
                base_raw = cache[base].get("containingDirAbsolutePath")
                if base_raw and Path(base_raw).exists():
                    hit = cache[base]
                    key = base
            raw = hit.get("containingDirAbsolutePath")
            if not raw:
                continue
            path = Path(raw)
            if not path.exists():
                continue
            if path.is_dir() and Path(key).name.endswith((".gguf", ".safetensors", ".bin")):
                candidate = path / Path(key).name
                if candidate.exists():
                    path = candidate
            for store in stores:
                if str(path).startswith(str(store) + os.sep):
                    return path, "store", store
            return path, "cache", None
    return None, "unknown", None


def classify_origin(path: Path | None, store: Path | None) -> str:
    if path is None:
        return "unknown"
    if store is not None and str(path).startswith(str(store) + os.sep):
        return "store"
    internal = Path.home() / ".lmstudio"
    if str(path).startswith(str(internal / ".internal" / "bundled-models") + os.sep):
        return "bundled"
    if str(path).startswith(str(internal / "hub") + os.sep):
        return "hub"
    return "other"


def dir_weight_size(directory: Path) -> int:
    total = 0
    for pattern in ("*.safetensors", "*.gguf", "*.bin"):
        for path in directory.glob(pattern):
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def size_detail(entry: dict, path: Path) -> tuple[bool, str]:
    listed = entry.get("sizeBytes")
    if path.is_file():
        actual = path.stat().st_size
        if actual <= 0:
            return False, f"{path} is empty"
        note = f"{path} ({actual / 1e9:.2f} GB)"
        if listed and abs(actual - listed) / listed > 0.02:
            note += f" [lms lists {listed / 1e9:.2f} GB; may include companion files]"
        return True, note
    actual = dir_weight_size(path)
    if actual <= 0:
        return False, f"{path} holds no weight files"
    note = f"{path} ({actual / 1e9:.2f} GB of weights)"
    if listed and abs(actual - listed) / listed > 0.02:
        note += f" [lms lists {listed / 1e9:.2f} GB]"
    return True, note


def open_weight_files() -> list[str] | None:
    """Paths currently open, via lsof (None when lsof is unavailable)."""
    exe = shutil.which("lsof")
    if not exe:
        return None
    try:
        proc = run_utf8([exe, "-n", "-F", "n"], 60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [line[1:] for line in (proc.stdout or "").splitlines() if line.startswith("n/")]


def find_open_weights(target: Path, opened: list[str]) -> str | None:
    text = str(target)
    for path in opened:
        if path == text or path.endswith(text) or (text + os.sep) in path:
            return path
    return None


# ---------------------------------------------------------------------------
# Test images
# ---------------------------------------------------------------------------
class Canvas:
    """Tiny RGB canvas with a PNG writer (no Pillow)."""

    def __init__(self, width: int, height: int, background=(255, 255, 255)):
        self.w, self.h = width, height
        self.buf = bytearray(bytes(background) * (width * height))

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, color) -> None:
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            start = (y * self.w + x0) * 3
            self.buf[start:start + len(row)] = row

    def fill_ellipse(self, cx: int, cy: int, rx: int, ry: int, color) -> None:
        for y in range(max(0, cy - ry), min(self.h, cy + ry + 1)):
            for x in range(max(0, cx - rx), min(self.w, cx + rx + 1)):
                dx, dy = (x - cx) / rx, (y - cy) / ry
                if dx * dx + dy * dy <= 1.0:
                    self._px(x, y, color)

    def fill_triangle(self, p1, p2, p3, color) -> None:
        xs = [p1[0], p2[0], p3[0]]
        ys = [p1[1], p2[1], p3[1]]
        for y in range(max(0, min(ys)), min(self.h, max(ys) + 1)):
            for x in range(max(0, min(xs)), min(self.w, max(xs) + 1)):
                if _point_in_triangle((x, y), p1, p2, p3):
                    self._px(x, y, color)

    def _px(self, x: int, y: int, color) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            start = (y * self.w + x) * 3
            self.buf[start:start + 3] = bytes(color)

    def save_png(self, path: Path) -> None:
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)  # PNG filter: none
            raw += self.buf[y * stride:(y + 1) * stride]

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def _point_in_triangle(p, a, b, c) -> bool:
    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

    d1, d2, d3 = sign(p, a, b), sign(p, b, c), sign(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def prepare_text_image(tmpdir: Path) -> Path:
    if not TEXT_FIXTURE.exists():
        raise ProbeError(
            f"missing text fixture {TEXT_FIXTURE} — ship lmstudio_smoke_text.png "
            "next to this script"
        )
    target = tmpdir / "text.png"
    target.write_bytes(TEXT_FIXTURE.read_bytes())
    return target


def shapes_image(path: Path) -> Path:
    canvas = Canvas(900, 600)
    canvas.fill_ellipse(170, 300, 95, 95, (220, 30, 30))                     # red circle
    canvas.fill_rect(350, 205, 530, 385, (30, 60, 200))                     # blue square
    canvas.fill_triangle((700, 390), (805, 390), (752, 225), (30, 150, 60))  # green triangle
    canvas.save_png(path)
    return path


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def normalise_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def text_answer_ok(answer: str) -> tuple[bool, str]:
    padded = f" {normalise_text(answer)} "
    missing = [token for token in TEXT_IMAGE_TOKENS if f" {token} " not in padded]
    return not missing, ("missing " + ", ".join(missing) if missing else "all tokens found")


def count_answer_ok(answer: str) -> tuple[bool, str]:
    words = re.findall(r"\b(\d+|three|two|four|five|six)\b", answer.lower())
    numbers = {"three": "3", "two": "2", "four": "4", "five": "5", "six": "6"}
    values = [numbers.get(word, word) for word in words]
    return str(COUNT_EXPECTED) in values, f"got {words!r}, expected {COUNT_EXPECTED}"


def colors_answer_ok(answer: str) -> tuple[bool, str]:
    low = answer.lower()
    missing = [color for color in COLORS_EXPECTED if color not in low]
    return not missing, ("missing " + ", ".join(missing) if missing else "all colors named")


# ---------------------------------------------------------------------------
# Core probes
# ---------------------------------------------------------------------------
def discover_server(api_url: str, server_root: str, timeout: float):
    """Return (model ids, native metadata by id or None)."""
    _, data = http_json(api_url + "/models", timeout=timeout)
    ids = [str(m.get("id")) for m in data.get("data", []) if m.get("id")]
    if not ids:
        raise ProbeError(f"{api_url}/models returned no models")
    try:
        _, native_data = http_json(server_root + "/api/v0/models", timeout=timeout)
        native = {str(m["id"]): m for m in native_data.get("data", []) if m.get("id")}
    except ProbeError:
        native = None
    return ids, native


def embed_texts(api_url: str, model: str, texts: list[str], timeout: float):
    started = time.time()
    _, data = http_json(api_url + "/embeddings", {"model": model, "input": texts}, timeout=timeout)
    elapsed = time.time() - started
    try:
        vectors = [list(map(float, item["embedding"])) for item in data["data"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ProbeError(f"unexpected embeddings response: {str(data)[:300]}") from exc
    if len(vectors) != len(texts):
        raise ProbeError(f"asked for {len(texts)} vectors, got {len(vectors)}")
    return vectors, elapsed


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if (na == 0.0 or nb == 0.0) else dot / (na * nb)


def vision_call(api_url: str, model: str, prompt: str, image_path: Path, timeout: float):
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
            ],
        }],
        "temperature": 0,
        "max_tokens": 300,
        "stream": False,
    }
    started = time.time()
    _, data = http_json(api_url + "/chat/completions", payload, timeout=timeout)
    elapsed = time.time() - started
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        raise ProbeError(f"unexpected chat response: {str(data)[:300]}") from exc
    if isinstance(content, list):  # some servers return content parts
        content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "").strip(), elapsed, finish


def _transient_load_error(message: str) -> bool:
    low = message.lower()
    return ("failed to load model" in low or "error loading model" in low
            or "model is not loaded" in low)


def call_with_load_retry(call, pause_s: float = 8.0):
    """Retry once when LM Studio refuses to load a model mid-transition.

    A model being unloaded (or an OOM eviction) can make the *next* request
    fail with "Failed to load model" even though the weights are fine; one
    delayed retry separates that flake from a real broken model.
    """
    try:
        return call()
    except ProbeError as exc:
        if not _transient_load_error(str(exc)):
            raise
        time.sleep(pause_s)
        return call()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_store(report: Report, entry: dict | None, stores: list[Path], args) -> tuple[Path | None, Path | None, str]:
    """Locate the model on disk; return (store, actual path, origin)."""
    if entry is None:
        if lms_executable():
            report.check("model store", "WARN", "not in `lms ls` (remote-only?)")
        else:
            report.check("model store", "SKIP", "lms CLI unavailable; cannot locate files")
        return None, None, "unknown"

    path, resolved_origin, store = resolve_model_files(entry, stores)
    if path is None:
        expected = stores[0] if stores else "?"
        report.check(
            "model store", "FAIL" if args.model_store else "WARN",
            f"not found under {expected} — is the model disk mounted/populated?",
        )
        return None, None, "unknown"

    origin = classify_origin(path, store)
    ok, detail = size_detail(entry, path)
    if not ok:
        report.check("model store", "FAIL", detail)
    elif origin == "store":
        report.check("model store", "PASS", detail)
    elif origin == "bundled":
        report.check("model store", "INFO", detail + " (bundled with LM Studio, not the moved store)")
    elif origin == "hub":
        report.check("model store", "INFO", detail + " (hub model metadata)")
    else:
        report.check("model store", "WARN", detail + f" (outside {store})")
    return store, path, origin


def check_weights(report: Report, path: Path | None, store: Path | None, origin: str) -> None:
    opened = open_weight_files()
    if opened is None:
        report.check("weights file", "SKIP", "lsof unavailable")
        return
    if path is None:
        return
    found = find_open_weights(path, opened)
    if not found:
        report.check("weights file", "SKIP", "runtime keeps no handle open (weights loaded into RAM)")
        return
    if origin == "store":
        status = "PASS"
    elif origin in ("bundled", "hub"):
        status = "INFO"
    else:
        status = "WARN"
    report.check("weights file", status, found)


def check_locality(report: Report, model: str, link: dict) -> None:
    if not lms_executable():
        report.check("local execution", "SKIP", "lms CLI unavailable")
        return
    loaded = lms_ps()
    names = device_names(link)
    current = link.get("deviceIdentifier")
    matches = [entry for entry in loaded
               if str(entry.get("identifier") or "") == model
               or str(entry.get("modelKey") or "") == model]
    if not matches:
        report.check("local execution", "WARN", "no loaded instance found after the request")
        return
    if any(is_on_this_device(entry, current) for entry in matches):
        report.check("local execution", "PASS",
                     f"loaded on this device ({names.get(str(current), 'unknown')})")
        return
    devices = ", ".join(
        str(names.get(str(entry.get("deviceIdentifier")), entry.get("deviceIdentifier")))
        for entry in matches
    )
    report.check(
        "local execution", "FAIL",
        f"served by another device ({devices}) — the moved disk was not exercised; "
        "use --pin-local or unload the remote copy",
    )


def unload_model(report: Report, args, model: str) -> None:
    if args.keep_loaded or args.pin_local or not lms_executable():
        return
    if model in args._preloaded:
        report.check("unload", "SKIP", "was already loaded before the test")
        return
    identifiers = [str(entry.get("identifier")) for entry in lms_ps()
                   if str(entry.get("identifier") or "") == model
                   or str(entry.get("modelKey") or "") == model]
    exe = lms_executable()
    assert exe
    for identifier in identifiers:
        try:
            run_utf8([exe, "unload", identifier], 120)
        except (OSError, subprocess.TimeoutExpired):
            continue
        # The next model loads only when the previous one has really gone; a load
        # attempted against a half-unloaded model is the one transient failure
        # this test can see, so wait for the unload to land.
        deadline = time.time() + 30
        while time.time() < deadline:
            still = [entry for entry in lms_ps()
                     if str(entry.get("identifier") or "") == identifier
                     or str(entry.get("modelKey") or "") == identifier]
            if not still:
                break
            time.sleep(0.5)
        else:
            report.check("unload", "WARN", f"{identifier!r} still loaded after 30s")


def check_embedding(report: Report, args, api_url: str, model: str, served: str,
                    inventory: dict[str, dict], stores: list[Path], link: dict) -> int | None:
    """`model` is the catalogue id (for disk checks); `served` is the id the
    request uses (a pinned alias under --pin-local)."""
    report.section("[embedding]", served if served == model else f"{model} (as {served})")
    store, path, origin = check_store(report, inventory.get(model), stores, args)

    docs = list(EMBED_DOCS)
    query = EMBED_QUERY
    if "nomic" in model.lower():  # mirror pha's embed.py prefixing
        docs = ["search_document: " + text for text in docs]
        query = "search_query: " + query
    batch_texts = docs + [query]

    try:
        vectors, cold = call_with_load_retry(
            lambda: embed_texts(api_url, served, batch_texts, args.timeout)
        )
    except ProbeError as exc:
        report.check("vectors", "FAIL", str(exc))
        check_locality(report, served, link)
        unload_model(report, args, served)
        return None

    dimension = len(vectors[0])
    lengths = {len(vector) for vector in vectors}
    finite = all(math.isfinite(value) for vector in vectors for value in vector)
    report.check(
        "vectors", "PASS" if (len(lengths) == 1 and finite) else "FAIL",
        f"{len(vectors)} vectors, dim {dimension}, " + ("all finite" if finite else "NON-FINITE values"),
    )
    norms = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
    mean_norm = sum(norms) / len(norms)
    if abs(mean_norm - 1.0) <= 0.01:
        report.check("normalised", "PASS", f"unit length ({mean_norm:.4f})")
    else:
        report.check("normalised", "INFO", f"mean length {mean_norm:.4f} (cosine still valid)")

    try:
        vectors_again, warm = embed_texts(api_url, served, batch_texts, args.timeout)
        repeat_sim = min(cosine(a, b) for a, b in zip(vectors, vectors_again))
        report.check("deterministic", "PASS" if repeat_sim > 0.9999 else "FAIL",
                     f"same input -> cosine {repeat_sim:.6f} (cold {cold:.2f}s, warm {warm:.2f}s)")
        single, _ = embed_texts(api_url, served, [batch_texts[-1]], args.timeout)
        single_sim = cosine(single[0], vectors[-1])
        report.check("batch==single", "PASS" if single_sim > 0.999 else "FAIL",
                     f"cosine {single_sim:.6f}")
    except ProbeError as exc:
        report.check("deterministic", "FAIL", str(exc))

    doc_vecs, query_vec = vectors[: len(docs)], vectors[-1]
    sims = [cosine(query_vec, vector) for vector in doc_vecs]
    relevant, unrelated = max(sims[0], sims[1]), sims[2]
    margin = relevant - unrelated
    report.check("semantics", "PASS" if margin > 0.05 else "FAIL",
                 f"related {sims[0]:.3f}/{sims[1]:.3f} vs unrelated {unrelated:.3f} "
                 f"(margin {margin:.3f})")

    check_weights(report, path, store, origin)
    check_locality(report, served, link)
    unload_model(report, args, served)
    return dimension


def check_vision(report: Report, args, api_url: str, model: str, served: str,
                 inventory: dict[str, dict], stores: list[Path], link: dict,
                 text_png: Path, shapes_png: Path) -> None:
    """`model` is the catalogue id (for disk checks); `served` is the request id."""
    report.section("[vision]", served if served == model else f"{model} (as {served})")
    store, path, origin = check_store(report, inventory.get(model), stores, args)

    try:
        answer, cold, finish = call_with_load_retry(
            lambda: vision_call(api_url, served, TEXT_PROMPT, text_png, args.timeout)
        )
    except ProbeError as exc:
        report.check("read text", "FAIL", str(exc))
        check_locality(report, served, link)
        unload_model(report, args, served)
        return
    ok, detail = text_answer_ok(answer)
    report.check("read text", "PASS" if ok else "FAIL",
                 f"{detail}; answer {answer[:120]!r} (cold {cold:.1f}s)")

    try:
        count_answer, count_time, _ = vision_call(api_url, served, COUNT_PROMPT, shapes_png, args.timeout)
        count_ok, count_detail = count_answer_ok(count_answer)
        report.check("count shapes", "PASS" if count_ok else "FAIL",
                     f"{count_detail}; answer {count_answer[:80]!r} ({count_time:.1f}s)")
    except ProbeError as exc:
        report.check("count shapes", "FAIL", str(exc))

    try:
        color_answer, color_time, _ = vision_call(api_url, served, COLOR_PROMPT, shapes_png, args.timeout)
        colors_ok, colors_detail = colors_answer_ok(color_answer)
        report.check("name colours", "PASS" if colors_ok else "FAIL",
                     f"{colors_detail}; answer {color_answer[:100]!r} ({color_time:.1f}s)")
    except ProbeError as exc:
        report.check("name colours", "FAIL", str(exc))

    if finish == "length":
        report.check("not truncated", "WARN", "answer hit max_tokens")
    check_weights(report, path, store, origin)
    check_locality(report, served, link)
    unload_model(report, args, served)


# ---------------------------------------------------------------------------
# Archive integration (optional)
# ---------------------------------------------------------------------------
def read_embeddings_config(config_file: Path | None) -> dict:
    """Dependency-free read of the two config.yaml keys this test needs."""
    if config_file is None:
        return {}
    try:
        lines = config_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    config: dict = {}
    in_embeddings = False
    for line in lines:
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if re.match(r"^embeddings\s*:", stripped):
            in_embeddings = True
            continue
        if not in_embeddings:
            continue
        if not stripped.startswith((" ", "\t")):
            break
        match = re.match(r"^\s+(model|base_url|backend)\s*:\s*(.+?)\s*$", stripped)
        if match:
            config[match.group(1)] = match.group(2).strip().strip("'\"")
    return config


def find_config(explicit: str | None, archive_dir: Path | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    candidates = [Path.cwd() / "config.yaml"]
    if archive_dir:
        candidates.append(archive_dir / "config.yaml")
    return next((candidate for candidate in candidates if candidate.exists()), None)


def check_archive(report: Report, archive_dir: Path, config_file: Path | None, config: dict,
                  tested_embeddings: list[str], dimensions: dict[str, int | None],
                  base_url: str) -> None:
    report.section("[archive]", str(archive_dir))
    if config_file and config_file.exists():
        report.check("config", "PASS", f"{config_file}")
    else:
        report.check("config", "WARN", "no config.yaml found (pass --config)")
    configured_model = config.get("model")
    if configured_model:
        report.check("config model", "PASS", f"embeddings.model = {configured_model}")
        if configured_model in tested_embeddings:
            report.check("tested model", "PASS", "the configured embedding model was tested")
        else:
            report.check("tested model", "WARN", "configured model was not among the tested models")
        configured_url = config.get("base_url")
        if configured_url:
            same = configured_url.rstrip("/") == base_url.rstrip("/")
            report.check("base_url", "PASS" if same else "WARN",
                         f"config {configured_url} vs tested {base_url}")
    else:
        report.check("config model", "WARN", "no embeddings.model in config.yaml")

    db_path = archive_dir / "archive.db"
    if not db_path.exists():
        report.check("database", "SKIP", f"{db_path} not found")
        return
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT embed_model, length(embedding) FROM chunks "
                "WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
            total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        report.check("database", "WARN", f"could not read {db_path}: {exc}")
        return
    if not row:
        report.check("database", "SKIP", "no embedded chunks in the index yet")
        return
    stored_model, blob_len = row
    stored_dim = int(blob_len) // 4  # float32
    model_dim = dimensions.get(configured_model) if configured_model else None
    detail = f"{total} chunks; stored vectors dim {stored_dim} (model {stored_model!r})"
    if stored_model == configured_model:
        report.check("stored model", "PASS", f"index vectors were made by {stored_model}")
    else:
        report.check("stored model", "WARN",
                     f"index vectors by {stored_model!r}, config now {configured_model!r}")
    if model_dim is None:
        report.check("database", "WARN", detail + "; tested model has no dimension")
    elif model_dim == stored_dim:
        report.check("database", "PASS", detail + f" == tested dim {model_dim}")
    else:
        report.check("database", "FAIL",
                     detail + f" != tested dim {model_dim} — semantic search would break")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Smoke-test the models served by LM Studio (embeddings + vision).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default=None, help=f"API root (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--model-store", default=None,
                        help="expected model directory (e.g. /Volumes/SSD/models); "
                             "default: autodetect from ~/.lmstudio/settings.json")
    parser.add_argument("--config", default=None,
                        help="config.yaml to read the archive's embedding model from "
                             "(default: ./config.yaml, else <archive-dir>/config.yaml)")
    parser.add_argument("--embedding-model", action="append", default=[], metavar="SUBSTR",
                        help="test this embedding model (repeatable substring match)")
    parser.add_argument("--vision-model", action="append", default=[], metavar="SUBSTR",
                        help="test this vision model (repeatable substring match)")
    parser.add_argument("--archive-dir", default=None,
                        help="pha archive to cross-check (config + DB dimensions)")
    parser.add_argument("--pin-local", action="store_true",
                        help="load local copies under unique identifiers with `lms load` "
                             "(deterministic when LM Link has the same model loaded remotely)")
    parser.add_argument("--keep-loaded", action="store_true",
                        help="do not unload models the test loaded")
    parser.add_argument("--timeout", type=float, default=600.0, help="per-request timeout in seconds")
    parser.add_argument("--vision-only", action="store_true", help="skip embedding models")
    parser.add_argument("--embedding-only", action="store_true", help="skip vision models")
    parser.add_argument("--list", action="store_true", help="list discovered models and exit")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    return parser.parse_args(argv)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return "pha-smoke-" + slug[-36:].strip("-")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args._preloaded = set()

    archive_dir = Path(args.archive_dir).expanduser() if args.archive_dir else None
    config_file = find_config(args.config, archive_dir)
    config = read_embeddings_config(config_file)
    base_url = args.base_url or config.get("base_url") or DEFAULT_BASE_URL
    api_url, server_root = split_base_url(base_url)

    report = Report(emit=not args.json)
    if not args.json:
        print("LM Studio smoke test")
        print(f"  endpoint: {api_url}")

    # --- server -----------------------------------------------------------
    try:
        ids, native = discover_server(api_url, server_root, args.timeout)
    except ProbeError as exc:
        if args.json:
            print(json.dumps({"result": "ERROR", "error": str(exc)}))
        else:
            print(f"  FAIL  server: {exc}")
            print("\nRESULT: ERROR (cannot reach LM Studio)")
        return 2

    lms = lms_executable()
    link = lms_link()
    inventory = lms_inventory(link) if lms else {}
    args._preloaded = {str(entry.get("identifier")) for entry in lms_ps()} if lms else set()
    stores = candidate_stores(args.model_store)
    store = stores[0] if stores else None
    native_type = {model_id: str(meta.get("type") or "") for model_id, meta in (native or {}).items()}

    report.section("[server]", api_url)
    report.check("endpoint", "PASS",
                 f"{len(ids)} models listed, native API {'ok' if native else 'unavailable'}")
    if store:
        mounted = store.exists()
        report.check("model store", "PASS" if mounted else "FAIL",
                     f"{store}" + ("" if mounted else " (does not exist — disk not mounted?)"))
    if lms:
        server_status = lms_json(["server", "status", "--json"]) or {}
        peers = [peer for peer in link.get("peers", []) if peer.get("status") == "connected"]
        report.check(
            "lms", "PASS",
            f"server running={server_status.get('running')}, LM Link peers connected={len(peers)}"
            f" ({', '.join(str(peer.get('deviceName')) for peer in peers) or 'none'})",
        )
    else:
        report.check("lms", "SKIP", "lms CLI not found — locality/on-disk checks limited")

    # --- choose models ----------------------------------------------------
    local_ids = [model_id for model_id in ids if (not lms or model_id in inventory)]

    def select(filters: list[str], wanted_type: str) -> list[str]:
        # Without LM Studio's native API (type per model) and without explicit
        # filters, a model cannot be classified as embedding vs vision: an
        # OpenAI-compatible server only lists ids. Say so instead of guessing.
        if not filters and native is None:
            return []
        pool = ids if filters else local_ids
        chosen = []
        for model_id in pool:
            if filters:
                if not any(f.lower() in model_id.lower() for f in filters):
                    continue
            elif native_type.get(model_id) != wanted_type:
                continue
            chosen.append(model_id)
        return chosen

    embedding_models = [] if args.vision_only else select(args.embedding_model, "embeddings")
    vision_models = [] if args.embedding_only else select(args.vision_model, "vlm")
    if archive_dir and not args.embedding_model and not args.vision_only and config.get("model"):
        configured = config["model"]
        if configured in ids:
            embedding_models = [configured]

    if args.list:
        print("\nDiscovered models")
        for model_id in ids:
            meta = (native or {}).get(model_id, {})
            kind = native_type.get(model_id, "?")
            local = "this device" if model_id in inventory else ("remote/unknown" if lms else "?")
            print(f"  {kind:10s} {model_id:60s} {local:14s} "
                  f"{meta.get('state', '?')} {meta.get('quantization', '')}")
        return 0

    if not embedding_models and not vision_models:
        why = ("native model types unavailable (not LM Studio?) — "
               "pass --embedding-model/--vision-model"
               if native is None and not (args.embedding_model or args.vision_model)
               else "no models selected (see --embedding-model/--vision-model)")
        report.check("selection", "FAIL", why)
        return finish(report, args)

    if not args.json:
        print(f"  embedding models: {', '.join(embedding_models) or '-'}")
        print(f"  vision models:    {', '.join(vision_models) or '-'}")

    tmpdir = Path(tempfile.mkdtemp(prefix="pha-lmstudio-smoke-"))
    try:
        text_png = prepare_text_image(tmpdir)
    except ProbeError as exc:
        report.check("fixtures", "FAIL", str(exc))
        return finish(report, args)
    shapes_png = shapes_image(tmpdir / "shapes.png")
    if not args.json:
        print(f"  fixtures: {tmpdir}")

    # --- optional deterministic local pinning ----------------------------
    aliases: dict[str, str] = {}
    if args.pin_local:
        report.section("[pin-local]")
        if not lms:
            report.check("pin", "FAIL", "--pin-local needs the lms CLI")
        else:
            for model in embedding_models + vision_models:
                if model not in inventory:
                    report.check(f"pin {model}", "FAIL", "not available on this machine")
                    continue
                alias = slugify(model)
                try:
                    proc = run_utf8(
                        [lms, "load", model, "-y", "--identifier", alias, "--ttl", "900"],
                        args.timeout,
                    )
                    pinned = proc.returncode == 0
                except (OSError, subprocess.TimeoutExpired) as exc:
                    pinned, proc = False, None
                    report.check(f"pin {model}", "FAIL", str(exc))
                if pinned:
                    aliases[model] = alias
                    report.check(f"pin {model}", "PASS", f"loaded as {alias!r}")
                elif proc is not None:
                    report.check(f"pin {model}", "FAIL", (proc.stderr or proc.stdout or "")[-300:])

    def served_id(model_id: str) -> str:
        return aliases.get(model_id, model_id)

    # --- embedding stage --------------------------------------------------
    dimensions: dict[str, int | None] = {}
    tested_embeddings: list[str] = []
    for model in embedding_models:
        dimensions[model] = check_embedding(
            report, args, api_url, model, served_id(model), inventory, stores, link
        )
        tested_embeddings.append(model)

    # --- vision stage -----------------------------------------------------
    for model in vision_models:
        check_vision(report, args, api_url, model, served_id(model), inventory, stores, link,
                     text_png, shapes_png)

    # --- archive stage ----------------------------------------------------
    if archive_dir:
        check_archive(report, archive_dir, config_file, config, tested_embeddings,
                      {model: dimensions.get(model) for model in tested_embeddings}, base_url)

    # --- cleanup ----------------------------------------------------------
    if aliases and lms:
        report.section("[cleanup]")
        for model, alias in aliases.items():
            try:
                run_utf8([lms, "unload", alias], 120)
                report.check(f"unload {alias}", "PASS", "")
            except (OSError, subprocess.TimeoutExpired) as exc:
                report.check(f"unload {alias}", "WARN", str(exc))

    return finish(report, args)


def finish(report: Report, args) -> int:
    counts = report.counts
    result = "PASS" if not report.failed else "FAIL"
    if args.json:
        print(json.dumps({"result": result, "counts": counts, "checks": report.checks}, indent=2))
    else:
        print()
        print(f"RESULT: {result}  "
              f"({counts['PASS']} passed, {counts['FAIL']} failed, "
              f"{counts['WARN']} warnings, {counts['SKIP']} skipped)")
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

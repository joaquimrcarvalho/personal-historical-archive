from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Sequence

import httpx


class ModelError(RuntimeError):
    pass


_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
}


def _clean_html_entities(text: str) -> str:
    """Decode common HTML entities that some models emit instead of plain
    characters (e.g. &nbsp; &amp; &lt; &gt; &quot; &#39;). Returns the text
    with those entities replaced by their characters and stray &nbsp; by a
    single space."""
    if not text:
        return text
    return (text
            .replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&#34;", '"'))


def _strip_think(text: str) -> str:
    """Remove a leading reasoning block (<think>...</think>) that some
    reasoning models (e.g. MiniMax-M3) emit before the actual answer.
    If the think block is truncated (no closing tag — e.g. max_tokens cut it
    off), keep whatever text follows the opening tag instead of returning
    empty."""
    t = text.strip()
    if t.startswith("<think>"):
        end = t.find("</think>")
        if end != -1:
            return _clean_html_entities(t[end + len("</think>"):].strip())
        # truncated reasoning: drop the opening tag, keep the rest
        return _clean_html_entities(t[len("<think>"):].strip())
    return _clean_html_entities(t)


def _resize_jpeg_bytes(image_path: Path, max_side: int, jpeg_quality: int) -> bytes:
    """Return the image resized to <=max_side (longest edge) and encoded as
    JPEG at `jpeg_quality`. Tries sips (macOS), then PyMuPDF (cross-platform)."""
    out = image_path.with_name(f"{image_path.stem}__enc.jpg")
    try:
        import subprocess

        subprocess.run(
            ["sips", "-Z", str(max_side), "-s", "format", "jpeg",
             "-s", "formatOptions", str(jpeg_quality), str(image_path), "--out", str(out)],
            capture_output=True, check=True, timeout=60,
        )
        return out.read_bytes()
    except Exception:  # noqa: BLE001 - sips unavailable/failed; try PyMuPDF
        pass
    finally:
        try:
            out.unlink(missing_ok=True)
        except (NameError, OSError):
            pass

    # Cross-platform fallback: re-render via PyMuPDF at the target pixel size.
    import pymupdf as fitz

    pm = fitz.Pixmap(str(image_path))  # intrinsic pixel dims (ignores dpi metadata)
    scale = 1.0
    if max_side and max(pm.width, pm.height) > max_side:
        scale = max_side / max(pm.width, pm.height)
    doc = fitz.open(str(image_path))
    try:
        page = doc[0]
        # page.rect is in points at the image's embedded dpi, while the file's
        # true pixels are pm.width/pm.height. Compensate so the rendered pixel
        # dims equal scale * intrinsic (not scale * points).
        zoom = scale * (pm.width / page.rect.width) if page.rect.width > 0 else scale
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=jpeg_quality)
    finally:
        doc.close()


def _prepare_image_b64(image_path: Path, max_side: int, jpeg_quality: int = 88) -> tuple[str, str]:
    """Return (base64, mime) for a page image, resized to <=`max_side` and
    re-encoded as JPEG at `jpeg_quality`.

    Applied to EVERY vision call (openai and anthropic) so the per-model
    `max_vision_px`/`vision_jpeg_quality` limits hold regardless of wire
    format. Falls back to the original bytes if re-encoding is unavailable.
    """
    mime = _MIME.get(image_path.suffix.lower(), "image/jpeg")
    return _reencode_b64(image_path, max_side, jpeg_quality, mime)


def _reencode_b64(image_path: Path, max_side: int, jpeg_quality: int, mime: str) -> tuple[str, str]:
    """Resize/re-encode to JPEG and base64 it. Falls back to the original
    bytes if resizing is unavailable."""
    try:
        data = _resize_jpeg_bytes(image_path, max_side, jpeg_quality)
        return base64.b64encode(data).decode(), "image/jpeg"
    except Exception:  # noqa: BLE001 - re-encode failed; send original
        return base64.b64encode(image_path.read_bytes()).decode(), mime


class ModelClient:
    """Client for chat/embedding endpoints. `api_style` selects the wire
    format:

    - "openai" (default): /chat/completions with OpenAI-style messages.
      Works with LM Studio, Ollama (/v1), llama.cpp, vLLM, OpenAI,
      OpenRouter, Groq, and any OpenAI-compatible remote.
    - "anthropic": /anthropic/v1/messages (host-root). Used by MiniMax
      (whose OpenAI-compatible endpoint silently drops image_url blocks)
      and any Anthropic-compatible service. Images are embedded as a
      plain-text data URI in content.

    `base_url` is the API root as configured (e.g. http://127.0.0.1:1234/v1
    or https://api.minimax.io/v1); for anthropic style a trailing /v1 is
    stripped to reach the host-root anthropic endpoint."""

    def __init__(
        self,
        base_url: str,
        timeout_s: int = 900,
        retries: int = 2,
        api_key: str | None = None,
        api_style: str = "openai",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        self.api_key = api_key or None
        self.api_style = (api_style or "openai").strip().lower()
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, headers=headers)

    @property
    def _anthropic_root(self) -> str:
        """Host root for Anthropic-style endpoints: base_url minus a
        trailing /v1 (the anthropic API lives at /anthropic/v1/messages)."""
        root = self.base_url
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ModelClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self._http.post(path, json=payload)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                last = e
                if e.response is not None and e.response.status_code < 500:
                    break  # 4xx errors are not retryable
            except httpx.HTTPError as e:
                last = e
            if attempt < self.retries:
                time.sleep(2 * (attempt + 1))
        raise ModelError(f"Request to {self.base_url}{path} failed: {last}")

    def _post_to(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to an ABSOLUTE url (e.g. a host-root endpoint that base_url
        doesn't cover, like MiniMax's /anthropic/v1/messages)."""
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self._http.post(url, json=payload)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                last = e
                if e.response is not None and e.response.status_code < 500:
                    break
            except httpx.HTTPError as e:
                last = e
            if attempt < self.retries:
                time.sleep(2 * (attempt + 1))
        raise ModelError(f"Request to {url} failed: {last}")

    def _get(self, path: str) -> dict[str, Any]:
        try:
            r = self._http.get(path)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise ModelError(f"Request to {self.base_url}{path} failed: {e}") from e

    def list_models(self) -> list[str]:
        try:
            return [m.get("id", "") for m in self._get("/models").get("data", [])]
        except ModelError:
            return []

    def ensure_model(self, model: str) -> None:
        available = self.list_models()
        if model not in available:
            raise ModelError(
                f"Model '{model}' is not served at {self.base_url}.\n"
                f"Available: {available or '(none — is the server running? did you load a model?)'}"
            )

    def _anthropic_chat(self, payload: dict[str, Any]) -> str:
        """POST an Anthropic-format chat payload and return the text."""
        data = self._post_to(f"{self._anthropic_root}/anthropic/v1/messages", payload)
        try:
            return _strip_think(" ".join(
                b.get("text", "") for b in data["content"] if b.get("type") == "text"
            ))
        except (KeyError, TypeError) as e:
            raise ModelError(f"Unexpected anthropic response: {data!r}") from e

    def _openai_chat(self, payload: dict[str, Any]) -> str:
        """POST an OpenAI-format chat payload and return the text."""
        data = self._post("/chat/completions", payload)
        try:
            return _strip_think(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, AttributeError) as e:
            raise ModelError(f"Unexpected chat response: {data!r}") from e

    def chat_vision(
        self,
        model: str,
        prompt: str,
        image_path: str | Path,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        thinking: bool = True,
        max_vision_px: int = 1800,
        jpeg_quality: int = 88,
    ) -> str:
        """Vision call. The wire format follows self.api_style:

        - "openai": /chat/completions with an image_url content block.
          Works with LM Studio, Ollama, vLLM and OpenAI-style remotes.
        - "anthropic": /anthropic/v1/messages with the image embedded as a
          PLAIN-TEXT data URI in content (MiniMax's OpenAI-compatible
          endpoint silently drops image_url blocks, so their vision requires
          this form).

        On BOTH paths the image is resized to <=max_vision_px and re-encoded
        at jpeg_quality before base64, so the model's resolution limits are
        honoured regardless of wire format.
        """
        image_path = Path(image_path)
        b64, mime = _prepare_image_b64(image_path, max_vision_px, jpeg_quality)
        if self.api_style == "anthropic":
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "user", "content": f"data:{mime};base64,{b64}\n{prompt}"}
                ],
            }
            if not thinking:
                payload["thinking"] = {"type": "disabled"}
            return self._anthropic_chat(payload)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if not thinking:
            payload["thinking"] = {"type": "disabled"}  # reasoning models (e.g. MiniMax-M3)
        return self._openai_chat(payload)

    def chat_text(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        thinking: bool = True,
    ) -> str:
        """Text-only completion (no image) — used by editors/encoders to
        transform transcriptions with a DIFFERENT model than the palaeographer.
        Wire format follows self.api_style (openai or anthropic)."""
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if not thinking:
            payload["thinking"] = {"type": "disabled"}
        if self.api_style == "anthropic":
            return self._anthropic_chat(payload)
        payload["temperature"] = temperature
        payload["stream"] = False
        return self._openai_chat(payload)

    def embed(
        self, model: str, texts: Sequence[str], batch_size: int | None = None
    ) -> list[list[float]]:
        """Embed `texts`, returning one vector per input text in order.

        `batch_size` caps how many texts go in each `/embeddings` request.
        Some endpoints reject a request whose `input` array exceeds a fixed
        maximum (e.g. 200 vectors), so a large index (pha reindex can produce
        thousands of chunks) is split into `batch_size`-sized requests instead
        of one giant one. Batches are sent sequentially and concatenated in
        input order. `batch_size <= 0`/None sends everything in one request."""
        if not batch_size or batch_size <= 0:
            batch_size = max(1, len(texts))
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = list(texts[i : i + batch_size])
            data = self._post("/embeddings", {"model": model, "input": batch})
            try:
                embeddings.extend(d["embedding"] for d in data["data"])
            except (KeyError, TypeError) as e:
                raise ModelError(f"Unexpected embeddings response: {data!r}") from e
        return embeddings

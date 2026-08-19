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
            return t[end + len("</think>"):].strip()
        # truncated reasoning: drop the opening tag, keep the rest
        return t[len("<think>"):].strip()
    return t


def _resize_image_b64(image_path: Path, max_side: int) -> tuple[str, str]:
    """Return (base64, mime) for an image, resized so its longest edge is
    <= max_side. MiniMax's M2.x vision context is small — full-resolution
    renders (e.g. 1126x1800) exceed it ("context window exceeds limit").
    Uses `sips` on macOS (stdlib has no image resizing); falls back to the
    original bytes if resizing is unavailable or the image is already small.
    """
    mime = _MIME.get(image_path.suffix.lower(), "image/jpeg")
    try:
        import struct

        with open(image_path, "rb") as f:
            head = f.read(2)
            if head == b"\xff\xd8":  # JPEG: parse SOF for dimensions
                f.seek(2)
                while True:
                    marker = f.read(1)
                    while marker != b"\xff":
                        marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if marker in (b"\xc0", b"\xc1", b"\xc2", b"\xc3"):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        break
                    else:
                        ln = struct.unpack(">H", f.read(2))[0]
                        f.seek(ln - 2, 1)
                longest = max(w, h)
            else:
                longest = max_side  # unknown format: don't resize
    except (OSError, struct.error):
        longest = max_side
    if longest <= max_side:
        return base64.b64encode(image_path.read_bytes()).decode(), mime
    out = image_path.with_name(f"{image_path.stem}__r{max_side}.jpg")
    try:
        import subprocess

        subprocess.run(
            ["sips", "-Z", str(max_side), "-s", "format", "jpeg",
             "-s", "formatOptions", "90", str(image_path), "--out", str(out)],
            capture_output=True, check=True, timeout=60,
        )
        return base64.b64encode(out.read_bytes()).decode(), "image/jpeg"
    except Exception:  # noqa: BLE001 - resize failed; send original
        return base64.b64encode(image_path.read_bytes()).decode(), mime
    finally:
        try:
            out.unlink(missing_ok=True)
        except (NameError, OSError):
            pass


class ModelClient:
    """OpenAI-compatible client — works with LM Studio, Ollama (/v1), llama.cpp,
    vLLM, and remote APIs (OpenAI, OpenRouter, Groq, ...) that need an api_key."""

    def __init__(
        self,
        base_url: str,
        timeout_s: int = 900,
        retries: int = 2,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        self.api_key = api_key or None
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, headers=headers)

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

    def chat_vision(
        self,
        model: str,
        prompt: str,
        image_path: str | Path,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        thinking: bool = True,
        vision_api: str = "openai",
        max_vision_px: int = 1800,
    ) -> str:
        """Vision call. `vision_api` selects how the image is sent:

        - "openai" (default): /chat/completions with an image_url content
          block. Works with LM Studio, Ollama, vLLM and OpenAI-style remotes.
        - "anthropic": the image is embedded as a PLAIN-TEXT data URI in
          `content` and the request goes to /anthropic/v1/messages. MiniMax's
          OpenAI-compatible endpoint silently drops image_url blocks (M2.7
          replies "no image"), so their vision requires this form; the image
          must be small enough for the model's vision context (max_vision_px).
        """
        image_path = Path(image_path)
        if vision_api == "anthropic":
            b64, mime = _resize_image_b64(image_path, max_vision_px)
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "user", "content": f"data:{mime};base64,{b64}\n{prompt}"}
                ],
            }
            if not thinking:
                payload["thinking"] = {"type": "disabled"}
            # Anthropic-style endpoints live at the HOST root
            # (/anthropic/v1/messages), not under the OpenAI /v1 prefix that
            # base_url carries. Strip a trailing /v1 when present.
            root = self.base_url
            if root.endswith("/v1"):
                root = root[: -len("/v1")]
            data = self._post_to(f"{root}/anthropic/v1/messages", payload)
            try:
                return _strip_think(" ".join(
                    b.get("text", "") for b in data["content"] if b.get("type") == "text"
                ))
            except (KeyError, TypeError) as e:
                raise ModelError(f"Unexpected anthropic response: {data!r}") from e

        b64 = base64.b64encode(image_path.read_bytes()).decode()
        mime = _MIME.get(image_path.suffix.lower(), "image/jpeg")
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
        data = self._post("/chat/completions", payload)
        try:
            return _strip_think(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, AttributeError) as e:
            raise ModelError(f"Unexpected chat response: {data!r}") from e

    def chat_text(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        thinking: bool = True,
    ) -> str:
        """Text-only completion (no image) — used by editors to transform
        transcriptions with a DIFFERENT model than the palaeographer."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if not thinking:
            payload["thinking"] = {"type": "disabled"}
        data = self._post("/chat/completions", payload)
        try:
            return _strip_think(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, AttributeError) as e:
            raise ModelError(f"Unexpected chat response: {data!r}") from e

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        data = self._post("/embeddings", {"model": model, "input": list(texts)})
        try:
            return [d["embedding"] for d in data["data"]]
        except (KeyError, TypeError) as e:
            raise ModelError(f"Unexpected embeddings response: {data!r}") from e

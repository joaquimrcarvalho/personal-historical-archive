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
    ) -> str:
        image_path = Path(image_path)
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
        data = self._post("/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as e:
            raise ModelError(f"Unexpected chat response: {data!r}") from e

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        data = self._post("/embeddings", {"model": model, "input": list(texts)})
        try:
            return [d["embedding"] for d in data["data"]]
        except (KeyError, TypeError) as e:
            raise ModelError(f"Unexpected embeddings response: {data!r}") from e

"""Local LLM provider backed by the Ollama client.

Ollama is local and does not require network access once the model is pulled.
The client is imported lazily so the ``llm`` extra stays optional.
"""

from __future__ import annotations

import logging

from save_finder.errors import ClassifierError

logger = logging.getLogger(__name__)


class OllamaLLM:
    """Call a local Ollama model with a system prompt and a user prompt."""

    def __init__(
        self,
        *,
        model: str = "qwen3:8b",
        host: str | None = None,
        temperature: float = 0.0,
        num_ctx: int = 8192,
        num_predict: int = 256,
        timeout: float = 90.0,
        json_mode: bool = True,
        think: bool = False,
        keep_alive: str = "10m",
    ) -> None:
        self.model_name = model
        self.host = host
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.timeout = timeout
        self.json_mode = json_mode
        self.think = think
        self.keep_alive = keep_alive
        self.name = f"ollama:{model}"
        self._client = None

    def _load(self):
        if self._client is not None:
            return self._client
        try:
            from ollama import Client
        except ImportError as exc:
            raise ClassifierError("ollama is not installed; install the 'llm' extra") from exc
        try:
            self._client = Client(host=self.host, timeout=self.timeout)
        except Exception as exc:
            raise ClassifierError(f"could not create Ollama client: {exc}") from exc
        return self._client

    def complete(self, *, system: str, prompt: str) -> str:
        client = self._load()
        options = {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }
        kwargs: dict[str, object] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": options,
            "think": self.think,
            "keep_alive": self.keep_alive,
        }
        if self.json_mode:
            kwargs["format"] = "json"
        try:
            response = client.chat(**kwargs)
            content = _message_content(response)
        except Exception as exc:
            raise ClassifierError(f"Ollama request failed: {exc}") from exc
        if not content:
            raise ClassifierError("Ollama returned an empty response")
        logger.debug("ollama model=%s response_chars=%d", self.model_name, len(content))
        return content


def _message_content(response: object) -> str:
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
    return ""

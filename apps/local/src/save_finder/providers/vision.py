"""Local image/text embedding provider backed by open-clip.

Images and taxonomy descriptions are projected into the same space, which lets
the classifier compare a persisted item embedding with tag descriptions
without downloading the media again.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from save_finder.errors import ClassifierError

logger = logging.getLogger(__name__)

DEFAULT_PRETRAINED = "laion2b_s34b_b79k"


class OpenClipVision:
    """Embed images and tag descriptions with a shared CLIP model."""

    def __init__(
        self,
        *,
        model: str = "ViT-B-32",
        pretrained: str = DEFAULT_PRETRAINED,
        device: str = "auto",
        batch_size: int = 8,
    ) -> None:
        self.model_name = model
        self.pretrained = pretrained
        self.device = device
        self.batch_size = max(1, batch_size)
        self.name = f"open-clip:{model}"
        self._model: Any | None = None
        self._preprocess: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        self._text_cache: dict[tuple[str, ...], list[list[float]]] = {}
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                import open_clip
                import torch
            except ImportError as exc:
                raise ClassifierError(
                    "open-clip-torch is not installed; install the 'vision' extra"
                ) from exc
            try:
                device = self.device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                model, _, preprocess = open_clip.create_model_and_transforms(
                    self.model_name,
                    pretrained=self.pretrained,
                    device=device,
                )
                model.eval()
                self._tokenizer = open_clip.get_tokenizer(self.model_name)
            except Exception as exc:
                raise ClassifierError(f"could not load open-clip model: {exc}") from exc
            self._torch = torch
            self._model = model
            self._preprocess = preprocess
            self._device = device
            return self._model

    def embed_images(self, image_paths: Sequence[Path]) -> list[list[float]]:
        if not image_paths:
            return []
        model = self._load()
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - pillow comes with the extra
            raise ClassifierError("pillow is not installed; install the 'vision' extra") from exc

        torch = self._torch
        assert torch is not None
        vectors: list[list[float]] = []
        for start in range(0, len(image_paths), self.batch_size):
            batch_paths = image_paths[start : start + self.batch_size]
            tensors = []
            for path in batch_paths:
                with Image.open(path) as image:
                    tensors.append(self._preprocess(image.convert("RGB")))
            batch = torch.stack(tensors).to(self._device)
            with torch.no_grad():
                features = model.encode_image(batch)
                features = features / features.norm(dim=-1, keepdim=True)
            vectors.extend(features.detach().cpu().float().tolist())
        logger.debug("embedded %d image(s) with %s", len(vectors), self.name)
        return vectors

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        cache_key = tuple(texts)
        with self._lock:
            cached = self._text_cache.get(cache_key)
        if cached is not None:
            return [list(vector) for vector in cached]

        model = self._load()
        torch = self._torch
        assert torch is not None
        assert self._tokenizer is not None
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), self.batch_size):
                batch = list(texts[start : start + self.batch_size])
                tokens = self._tokenizer(batch).to(self._device)
                with torch.no_grad():
                    features = model.encode_text(tokens)
                    features = features / features.norm(dim=-1, keepdim=True)
                vectors.extend(features.detach().cpu().float().tolist())
        except Exception as exc:
            raise ClassifierError(f"text embedding failed: {exc}") from exc
        with self._lock:
            self._text_cache[cache_key] = vectors
        return [list(vector) for vector in vectors]

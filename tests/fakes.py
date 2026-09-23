"""Deterministic stand-ins for model providers, so tests need no API key or network."""

import hashlib
import re

import numpy as np


class FakeEmbedder:
    """Hashed bag-of-words vectors: texts sharing words get high cosine similarity."""

    model_id = "fake-embedder"

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def embed(self, texts: list[str], task: str) -> np.ndarray:
        out = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                out[row, int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)

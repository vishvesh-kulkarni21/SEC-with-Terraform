"""Gemini implementations of the model interfaces (google-genai SDK).

The same client works against Google AI Studio (API key, local dev) and Vertex AI
(service account via ADC, for the Cloud Run deployment); only construction differs.
"""

import time

import numpy as np
from google import genai
from google.genai import errors, types

TASK_TYPES = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}
EMBED_BATCH = 50
MAX_RETRIES = 6


def make_client(api_key: str | None, vertex_project: str | None = None,
                vertex_location: str = "us-central1") -> genai.Client:
    if vertex_project:
        return genai.Client(vertexai=True, project=vertex_project, location=vertex_location)
    if not api_key:
        raise ValueError("Set GEMINI_API_KEY (AI Studio) or VERTEX_PROJECT (Vertex AI)")
    return genai.Client(api_key=api_key)


def with_retries(call):
    """Retry rate-limit (429) and server errors with exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            return call()
        except errors.APIError as e:
            retryable = e.code == 429 or (e.code or 0) >= 500
            if not retryable or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(min(2 ** attempt * 2, 60))


class GeminiEmbedder:
    def __init__(self, client: genai.Client, model_id: str, dimensions: int = 768):
        self.client = client
        self.model_id = model_id
        self.dimensions = dimensions

    def embed(self, texts: list[str], task: str) -> np.ndarray:
        config = types.EmbedContentConfig(
            task_type=TASK_TYPES[task], output_dimensionality=self.dimensions)
        rows: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i:i + EMBED_BATCH]
            response = with_retries(lambda: self.client.models.embed_content(
                model=self.model_id, contents=batch, config=config))
            rows.extend(e.values for e in response.embeddings)
        matrix = np.asarray(rows, dtype=np.float32)
        # Truncated (<3072-dim) Gemini embeddings are not unit length; normalise for cosine.
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

"""Build model clients from settings. The only place that knows which provider is in use."""

from equity_research.config import Settings
from equity_research.llm.base import Embedder


def make_embedder(settings: Settings) -> Embedder:
    from equity_research.llm.gemini import GeminiEmbedder, make_client

    client = make_client(settings.gemini_api_key, settings.vertex_project, settings.vertex_location)
    return GeminiEmbedder(client, settings.embedding_model)

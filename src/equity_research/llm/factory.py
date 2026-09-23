"""Build model clients from settings. The only place that knows which provider is in use."""

from equity_research.config import Settings
from equity_research.llm.base import ChatModel, Embedder


def make_embedder(settings: Settings) -> Embedder:
    from equity_research.llm.gemini import GeminiEmbedder, make_client

    client = make_client(settings.gemini_api_key, settings.vertex_project, settings.vertex_location)
    return GeminiEmbedder(client, settings.embedding_model)


def make_chat_model(settings: Settings, model_id: str | None = None) -> ChatModel:
    """model_id overrides the configured model (used by the model-comparison eval)."""
    from equity_research.llm.gemini import GeminiChat, make_client

    client = make_client(settings.gemini_api_key, settings.vertex_project, settings.vertex_location)
    return GeminiChat(client, model_id or settings.chat_model)

"""Gemini implementations of the model interfaces (google-genai SDK).

The same client works against Google AI Studio (API key, local dev) and Vertex AI
(service account via ADC, for the Cloud Run deployment); only construction differs.
"""

import time

import numpy as np
from google import genai
from google.genai import errors, types

from equity_research.llm.base import ChatResponse, Message, ToolCall, ToolSpec, Usage

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


class GeminiChat:
    """ChatModel over Gemini with native function calling (automatic calling disabled:
    our own agent loop executes tools, so every call is traced)."""

    def __init__(self, client: genai.Client, model_id: str):
        self.client = client
        self.model_id = model_id

    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 temperature: float = 0.0) -> ChatResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters_json_schema=t.parameters)
                for t in tools])] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = [self._to_content(m) for m in messages]
        response = with_retries(lambda: self.client.models.generate_content(
            model=self.model_id, contents=contents, config=config))

        candidate = response.candidates[0] if response.candidates else None
        content = candidate.content if candidate and candidate.content else types.Content(role="model", parts=[])
        text_parts, calls = [], []
        for i, part in enumerate(content.parts or []):
            if part.function_call:
                fc = part.function_call
                calls.append(ToolCall(fc.id or f"call-{i}", fc.name, dict(fc.args or {})))
            elif part.text and not part.thought:
                text_parts.append(part.text)

        meta = response.usage_metadata
        usage = Usage(
            input_tokens=(meta.prompt_token_count or 0) if meta else 0,
            output_tokens=((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)) if meta else 0,
        )
        message = Message("assistant", "".join(text_parts).strip(), calls, raw=content)
        return ChatResponse(message, usage, self.model_id)

    @staticmethod
    def _to_content(m: Message) -> types.Content:
        if m.role == "assistant":
            if m.raw is not None:
                return m.raw
            parts = [types.Part.from_text(text=m.text)] if m.text else []
            parts += [types.Part.from_function_call(name=c.name, args=c.args) for c in m.tool_calls]
            return types.Content(role="model", parts=parts)
        if m.role == "tool":
            return types.Content(role="user", parts=[
                types.Part.from_function_response(name=r.name, response=r.content) for r in m.tool_results])
        return types.Content(role="user", parts=[types.Part.from_text(text=m.text)])

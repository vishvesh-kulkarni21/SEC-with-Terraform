"""Provider-neutral model interfaces. Agent and retrieval code depend only on these.

Swapping providers or models is a config change (design rule 6): implementations live
in their own modules and are chosen by `equity_research.llm.factory`.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str], task: str) -> np.ndarray:
        """Return one L2-normalised row per text. task is "document" or "query"."""
        ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments object


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    args: dict


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: dict


@dataclass
class Message:
    """One conversation turn. role: "user", "assistant", or "tool"."""

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # Provider-native form of an assistant turn, replayed verbatim on the next call
    # (Gemini needs its thought signatures back). Never inspected by agent code.
    raw: Any = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0  # includes any "thinking" tokens, which are billed as output

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass(frozen=True)
class ChatResponse:
    message: Message
    usage: Usage
    model_id: str


class ChatModel(Protocol):
    model_id: str

    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 temperature: float = 0.0) -> ChatResponse:
        ...

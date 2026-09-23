"""Deterministic stand-ins for model providers, so tests need no API key or network."""

import hashlib
import json
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


class ScriptedChatModel:
    """Returns pre-written assistant turns in order, and records what it was sent."""

    model_id = "scripted-model"

    def __init__(self, turns):
        from equity_research.llm.base import Message  # local: keep module import-light
        self._Message = Message
        self.turns = list(turns)  # each: str (final answer) or list of (tool_name, args)
        self.calls = []

    def generate(self, system, messages, tools, temperature=0.0, json_schema=None):
        from equity_research.llm.base import ChatResponse, ToolCall, Usage
        self.calls.append({"system": system, "messages": list(messages), "tools": [t.name for t in tools],
                           "json_schema": json_schema})
        turn = self.turns.pop(0)
        if callable(turn):  # computes the reply from what the model was sent
            turn = turn(system, messages)
        if isinstance(turn, dict):
            turn = json.dumps(turn)
        if isinstance(turn, str):
            msg = self._Message("assistant", turn)
        else:
            msg = self._Message("assistant", "", [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(turn)])
        return ChatResponse(msg, Usage(100, 20), self.model_id)

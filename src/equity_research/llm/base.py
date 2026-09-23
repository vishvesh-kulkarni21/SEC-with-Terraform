"""Provider-neutral model interfaces. Agent and retrieval code depend only on these.

Swapping providers or models is a config change (design rule 6): implementations live
in their own modules and are chosen by `equity_research.llm.factory`.
"""

from typing import Protocol

import numpy as np


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str], task: str) -> np.ndarray:
        """Return one L2-normalised row per text. task is "document" or "query"."""
        ...

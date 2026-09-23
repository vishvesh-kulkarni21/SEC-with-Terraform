"""In-memory cosine-similarity index over chunk embeddings, persisted to disk.

A 10-K yields a few hundred chunks per strategy, so brute-force numpy search is
exact and instant; a vector database would add a dependency for no gain at this size.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from equity_research.data.edgar_client import EdgarClient
from equity_research.data.sec_api import Filing, ten_k_filings, ten_k_html
from equity_research.llm.base import Embedder
from equity_research.retrieval.chunkers import STRATEGIES, Chunk, chunk
from equity_research.retrieval.parse import parse_10k


@dataclass(frozen=True)
class Passage:
    """A retrieved chunk plus where it came from, so qualitative claims are traceable too."""

    chunk: Chunk
    score: float
    ticker: str
    accn: str
    report_date: str
    url: str

    @property
    def source(self) -> str:
        return f"{self.ticker} 10-K accn {self.accn} (period {self.report_date}), {self.chunk.section}, {self.chunk.chunk_id}"


class VectorIndex:
    def __init__(self, filing: Filing, strategy: str, chunks: list[Chunk], vectors: np.ndarray):
        self.filing, self.strategy, self.chunks, self.vectors = filing, strategy, chunks, vectors

    def search(self, query_vector: np.ndarray, k: int = 5) -> list[Passage]:
        scores = self.vectors @ query_vector
        top = np.argsort(-scores)[:k]
        f = self.filing
        return [Passage(self.chunks[i], float(scores[i]), f.ticker, f.accn, str(f.report_date), f.url)
                for i in top]

    def save(self, path: Path) -> None:
        np.save(path.with_suffix(".npy"), self.vectors)
        path.with_suffix(".json").write_text(
            json.dumps([asdict(c) for c in self.chunks]), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, filing: Filing, strategy: str) -> "VectorIndex | None":
        vec, meta = path.with_suffix(".npy"), path.with_suffix(".json")
        if not (vec.exists() and meta.exists()):
            return None
        chunks = [Chunk(**c) for c in json.loads(meta.read_text(encoding="utf-8"))]
        return cls(filing, strategy, chunks, np.load(vec))


class Retriever:
    """Builds (or loads) one index per chunking strategy for a company's 10-K."""

    def __init__(self, client: EdgarClient, embedder: Embedder, cache_dir: Path):
        self.client, self.embedder = client, embedder
        self.index_dir = cache_dir / "indexes"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._indexes: dict[tuple[str, str], VectorIndex] = {}

    def filing_for(self, ticker: str, fiscal_year: int | None = None) -> Filing:
        """Latest 10-K, or the 10-K covering the fiscal year that ends in `fiscal_year`."""
        filings = ten_k_filings(self.client, ticker)
        if fiscal_year is None:
            return filings[0]
        for f in filings:
            if f.report_date.year == fiscal_year:
                return f
        raise LookupError(f"No 10-K for {ticker} covering FY{fiscal_year}")

    def index(self, ticker: str, strategy: str, filing: Filing | None = None) -> VectorIndex:
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy {strategy!r}")
        filing = filing or self.filing_for(ticker)
        key = (filing.accn, strategy)
        if key in self._indexes:
            return self._indexes[key]

        model = self.embedder.model_id.replace("/", "_")
        path = self.index_dir / f"{filing.ticker}-{filing.accn}-{strategy}-{model}"
        index = VectorIndex.load(path, filing, strategy)
        if index is None:
            chunks = chunk(parse_10k(ten_k_html(self.client, filing)), strategy)
            vectors = self.embedder.embed([c.text for c in chunks], task="document")
            index = VectorIndex(filing, strategy, chunks, vectors)
            index.save(path)
        self._indexes[key] = index
        return index

    def search(self, ticker: str, query: str, strategy: str, k: int = 5,
               filing: Filing | None = None) -> list[Passage]:
        index = self.index(ticker, strategy, filing)
        query_vector = self.embedder.embed([query], task="query")[0]
        return index.search(query_vector, k)

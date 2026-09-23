from datetime import date

from equity_research.data.sec_api import Filing
from equity_research.retrieval.chunkers import Chunk
from equity_research.retrieval.index import VectorIndex
from tests.fakes import FakeEmbedder

FILING = Filing("AAPL", 320193, "10-K", "0000320193-25-000079", date(2025, 9, 27),
                date(2025, 10, 31), "https://example/aapl.htm")
CHUNKS = [
    Chunk("table-0000", "table", "iPhone net sales grew in Greater China", "Item 7", "text"),
    Chunk("table-0001", "table", "Risk factors include supply chain concentration", "Item 1A", "text"),
]


def test_search_ranks_relevant_chunk_first_and_cites_source(tmp_path):
    emb = FakeEmbedder()
    index = VectorIndex(FILING, "table", CHUNKS, emb.embed([c.text for c in CHUNKS], "document"))
    top = index.search(emb.embed(["supply chain risk"], "query")[0], k=1)[0]
    assert top.chunk.chunk_id == "table-0001"
    assert "0000320193-25-000079" in top.source and "Item 1A" in top.source


def test_save_and_load_roundtrip(tmp_path):
    emb = FakeEmbedder()
    index = VectorIndex(FILING, "table", CHUNKS, emb.embed([c.text for c in CHUNKS], "document"))
    index.save(tmp_path / "idx")
    loaded = VectorIndex.load(tmp_path / "idx", FILING, "table")
    assert loaded.chunks == CHUNKS
    assert (loaded.vectors == index.vectors).all()

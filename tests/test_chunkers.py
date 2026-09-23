import pytest

from equity_research.retrieval.chunkers import chunk
from equity_research.retrieval.parse import Block

TABLE = "\n".join(["Year | 2025 | 2024"] + [f"Line item {i} | {i},000 | {i},100" for i in range(40)])
BLOCKS = [
    Block("text", "Item 7. Management's Discussion", "Item 7"),
    Block("text", " ".join(["discussion"] * 30), "Item 7"),
    Block("text", "The following table shows net sales (dollars in millions):", "Item 7"),
    Block("table", TABLE, "Item 7"),
    Block("text", "Item 8. Financial Statements", "Item 8"),
    Block("text", " ".join(["statements"] * 30), "Item 8"),
]


def test_fixed_ignores_boundaries():
    chunks = chunk(BLOCKS, "fixed", size=60, overlap=10)
    assert any("discussion" in c.text and "Line item" in c.text for c in chunks)
    assert any("Line item" in c.text and "statements" in c.text for c in chunks)


def test_section_never_crosses_items():
    for c in chunk(BLOCKS, "section", size=60, overlap=10):
        assert not ("Line item" in c.text and "statements" in c.text)


def test_table_kept_whole_with_caption():
    chunks = chunk(BLOCKS, "table", size=60, overlap=10)
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) == 1
    assert "dollars in millions" in tables[0].text
    assert "Line item 0 |" in tables[0].text and "Line item 39 |" in tables[0].text


def test_huge_table_split_between_rows_with_header(monkeypatch):
    monkeypatch.setattr("equity_research.retrieval.chunkers.MAX_TABLE_WORDS", 60)
    monkeypatch.setattr("equity_research.retrieval.chunkers.TABLE_HEADER_ROWS", 1)
    tables = [c for c in chunk(BLOCKS, "table") if c.kind == "table"]
    assert len(tables) > 1
    for t in tables:
        assert "Year | 2025 | 2024" in t.text
        for line in t.text.split("\n"):
            if line.startswith("Line item"):
                assert line.count("|") == 2  # rows are never cut


def test_unknown_strategy():
    with pytest.raises(ValueError):
        chunk(BLOCKS, "semantic")

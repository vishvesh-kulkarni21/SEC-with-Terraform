"""Three chunking strategies over the same parsed 10-K, so retrieval can be compared.

Size is measured in words, not model tokens (~1.3 tokens per English word), to avoid
a tokenizer dependency. All three strategies use the same target size, so the
comparison isolates *where* chunks are cut, not how big they are.

- fixed:    one long word stream, cut every `size` words with `overlap`. Ignores
            sections and tables entirely. The baseline.
- section:  cut at 10-K Item boundaries; long Items are windowed like `fixed` inside
            the Item. Tables can still be split mid-table.
- table:    Item boundaries, paragraphs packed whole, and every financial table kept as
            its own chunk with its caption (the text just above it, which usually
            states the statement name and "in millions"). A table larger than the
            limit is split between rows with the header rows repeated, never mid-row.
"""

from dataclasses import dataclass

from equity_research.retrieval.parse import Block

STRATEGIES = ("fixed", "section", "table")
DEFAULT_SIZE, DEFAULT_OVERLAP = 350, 50
MAX_TABLE_WORDS = 1200  # stays under the embedding model's input limit
TABLE_HEADER_ROWS = 2
CAPTION_MAX_WORDS = 40


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    strategy: str
    text: str
    section: str
    kind: str  # "text", "table", or "mixed"


def chunk(blocks: list[Block], strategy: str, size: int = DEFAULT_SIZE,
          overlap: int = DEFAULT_OVERLAP) -> list[Chunk]:
    if strategy == "fixed":
        pieces = _fixed(blocks, size, overlap)
    elif strategy == "section":
        pieces = _section(blocks, size, overlap)
    elif strategy == "table":
        pieces = _table(blocks, size)
    else:
        raise ValueError(f"Unknown strategy {strategy!r}; choose from {STRATEGIES}")
    return [Chunk(f"{strategy}-{i:04d}", strategy, text, section, kind)
            for i, (text, section, kind) in enumerate(pieces)]


def _windows(words: list[str], size: int, overlap: int) -> list[list[str]]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    step = size - overlap
    return [words[i:i + size] for i in range(0, max(len(words) - overlap, 1), step)]


def _fixed(blocks, size, overlap):
    words, sections = [], []
    for b in blocks:
        w = b.text.split()
        words += w
        sections += [b.section] * len(w)
    out, step = [], size - overlap
    for i, window in enumerate(_windows(words, size, overlap)):
        out.append((" ".join(window), sections[i * step], "mixed"))
    return out


def _by_section(blocks):
    groups: list[tuple[str, list[Block]]] = []
    for b in blocks:
        if not groups or groups[-1][0] != b.section:
            groups.append((b.section, []))
        groups[-1][1].append(b)
    return groups


def _section(blocks, size, overlap):
    out = []
    for section, group in _by_section(blocks):
        words = " ".join(b.text for b in group).split()
        for window in _windows(words, size, overlap):
            out.append((" ".join(window), section, "mixed"))
    return out


def _table(blocks, size):
    out = []
    for section, group in _by_section(blocks):
        buffer: list[str] = []
        buffer_words = 0

        def flush():
            nonlocal buffer, buffer_words
            if buffer:
                out.append(("\n".join(buffer), section, "text"))
            buffer, buffer_words = [], 0

        for i, b in enumerate(group):
            if b.kind == "table":
                flush()
                caption = _caption(group, i)
                for part in _split_table(b.text):
                    out.append((f"{caption}\n{part}" if caption else part, section, "table"))
                continue
            n = len(b.text.split())
            if n > size:  # one giant paragraph: window it on its own
                flush()
                for window in _windows(b.text.split(), size, DEFAULT_OVERLAP):
                    out.append((" ".join(window), section, "text"))
                continue
            if buffer_words + n > size:
                flush()
            buffer.append(b.text)
            buffer_words += n
        flush()
    return out


def _caption(group: list[Block], table_index: int) -> str:
    """Nearest short text blocks above a table (statement title, "in millions")."""
    lines = []
    for b in reversed(group[max(0, table_index - 3):table_index]):
        if b.kind != "text" or len(b.text.split()) > CAPTION_MAX_WORDS:
            break
        lines.insert(0, b.text)
    return "\n".join(lines)


def _split_table(text: str) -> list[str]:
    rows = text.split("\n")
    if len(text.split()) <= MAX_TABLE_WORDS:
        return [text]
    header, body = rows[:TABLE_HEADER_ROWS], rows[TABLE_HEADER_ROWS:]
    parts, current, words = [], [], 0
    header_words = len(" ".join(header).split())
    for row in body:
        n = len(row.split())
        if current and header_words + words + n > MAX_TABLE_WORDS:
            parts.append("\n".join(header + current))
            current, words = [], 0
        current.append(row)
        words += n
    if current:
        parts.append("\n".join(header + current))
    return parts

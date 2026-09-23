"""Turn 10-K HTML into an ordered list of text and table blocks, each tagged with its Item.

- Hidden inline-XBRL content (ix:header, display:none) is dropped.
- Tables are rendered row by row as "cell | cell | cell", with SEC's split cells
  ("$" | "1,234" and "(5" | ")") merged back together.
- Section = the most recent "Item N." heading seen in a text block. Table-of-contents
  entries live in tables or come before the real headings, and are harmless: the real
  heading resets the section.
"""

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

# 10-Ks are inline XBRL (XHTML); the HTML parser handles them fine and is more forgiving.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCK_TAGS = ["p", "div", "table", "li", "h1", "h2", "h3", "h4", "h5", "h6"]
ITEM_RE = re.compile(r"^item\s*(\d{1,2}[a-c]?)\s*[.:\-\u2014\u2013]?\s*(.*)$", re.IGNORECASE)
NUMERIC_RE = re.compile(r"\d")


@dataclass(frozen=True)
class Block:
    kind: str  # "text" or "table"
    text: str
    section: str  # e.g. "Item 7", or "Front" before the first Item heading


def parse_10k(html: str) -> list[Block]:
    soup = BeautifulSoup(html, "lxml")
    for el in soup.find_all(["script", "style", "ix:header"]):
        el.decompose()
    for el in soup.find_all(style=re.compile(r"display\s*:\s*none", re.IGNORECASE)):
        el.decompose()

    body = soup.body or soup
    blocks: list[Block] = []
    section = "Front"
    for el in body.find_all(BLOCK_TAGS):
        if el.find_parent("table") is not None:
            continue  # handled as part of its table
        if el.name == "table":
            rows = _table_rows(el)
            if _is_data_table(rows):
                blocks.append(Block("table", "\n".join(rows), section))
            elif rows:
                blocks.append(Block("text", " ".join(rows), section))
            continue
        if el.find(BLOCK_TAGS) is not None:
            continue  # not a leaf; its children will be visited
        text = _clean(el.get_text(" ", strip=True))
        if not text:
            continue
        heading = _item_heading(text)
        if heading:
            section = heading
        blocks.append(Block("text", text, section))
    return blocks


def _item_heading(text: str) -> str | None:
    if len(text) > 150:
        return None
    m = ITEM_RE.match(text)
    return f"Item {m.group(1).upper()}" if m else None


def _table_rows(table: Tag) -> list[str]:
    rows = []
    for tr in table.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        cells = _merge_split_cells([c for c in cells if c])
        if cells:
            rows.append(" | ".join(cells))
    return rows


def _merge_split_cells(cells: list[str]) -> list[str]:
    """SEC tables put "$" and ")" / "%" in their own cells; glue them to the number."""
    out: list[str] = []
    for c in cells:
        if out and c in {")", "%", ")%", "%)"}:
            out[-1] += c
        elif out and out[-1] in {"$", "("}:
            out[-1] += c
        else:
            out.append(c)
    return out


def _is_data_table(rows: list[str]) -> bool:
    """A real financial table has several rows and numbers; layout tables don't."""
    return len(rows) >= 2 and sum(bool(NUMERIC_RE.search(r)) for r in rows) >= 2


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", text))

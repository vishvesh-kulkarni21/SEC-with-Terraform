from equity_research.retrieval.parse import parse_10k

HTML = """<html><body>
<div style="display:none"><ix:header>hidden xbrl context</ix:header></div>
<div><span>Item 1A. Risk Factors</span></div>
<p>Supply chain concentration is a risk.</p>
<div><span>Item 8. Financial Statements</span></div>
<p>CONSOLIDATED STATEMENTS OF OPERATIONS (In millions)</p>
<table>
  <tr><td>Net sales</td><td>$</td><td>416,161</td><td>$</td><td>391,035</td></tr>
  <tr><td>Other income</td><td>(321</td><td>)</td><td></td><td>269</td></tr>
</table>
<table><tr><td>Layout only</td></tr></table>
</body></html>"""


def test_sections_tables_and_hidden_content():
    blocks = parse_10k(HTML)
    texts = [b.text for b in blocks]
    assert not any("hidden" in t for t in texts)
    risk = next(b for b in blocks if "Supply chain" in b.text)
    assert risk.section == "Item 1A"
    table = next(b for b in blocks if b.kind == "table")
    assert table.section == "Item 8"
    assert table.text.split("\n") == ["Net sales | $416,161 | $391,035", "Other income | (321) | 269"]
    assert any(b.kind == "text" and b.text == "Layout only" for b in blocks)

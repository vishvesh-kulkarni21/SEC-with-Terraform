from equity_research.agents.pdf_report import render_pdf
from tests.test_debate import GOOD, run


def test_pdf_report_contains_claims_sources_and_planted_error(tmp_path):
    report, _ = run([GOOD, GOOD, GOOD, GOOD], plant_kind="perturb")
    path = render_pdf(report, tmp_path / "AAPL.pdf", "fixed", "fake-model", (2024, 2025), compress=False)
    raw = path.read_bytes()
    assert raw.startswith(b"%PDF")
    for text in [b"AAPL: bull vs bear", b"Revenue reached $416,161 million in fiscal 2025.", b"[F1]",
                 b"Sources", b"CAUGHT", b"FY2025 vs FY2024", report.conversation_id.encode()]:
        assert text in raw, text


def test_pdf_escapes_markup_and_non_latin_characters(tmp_path):
    report, _ = run([GOOD, GOOD])
    report.sides["bull"].claims[0].text = "Margin <b>rose</b> & costs fell \u2212 2% \u2014 \u4e2d"
    path = render_pdf(report, tmp_path / "x.pdf", "fixed", compress=False)
    raw = path.read_bytes()
    # drawn as literal text in the regular font (never parsed as bold markup), unknown glyph -> "?"
    assert b"(1. Margin <) Tj (b) Tj (>) Tj (rose)" in raw
    assert b"(> & costs fell - 2% - ? ) Tj" in raw

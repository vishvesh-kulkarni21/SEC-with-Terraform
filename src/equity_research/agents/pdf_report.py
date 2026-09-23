"""Render a verified debate report as a PDF.

Same content rules as the markdown report: every claim carries its evidence ids, and
every cited id is resolved to its XBRL fact, calculation inputs or 10-K passage in the
Sources table. The renderer only lays out what the pipeline produced; it formats no
new numbers.
"""

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from equity_research.agents.debate import DebateReport, SideResult

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
RULE = colors.HexColor("#cbd2d9")
HEADER_BG = colors.HexColor("#eef2f6")
SIDE_COLOR = {"bull": colors.HexColor("#1b7f4b"), "bear": colors.HexColor("#b3261e")}
PASS, FAIL = colors.HexColor("#1b7f4b"), colors.HexColor("#b3261e")

# Built-in PDF fonts only cover Latin-1; map the characters models and filings commonly use.
_ASCII = str.maketrans({"−": "-", "–": "-", "—": "-", "‘": "'", "’": "'",
                        "“": '"', "”": '"', "…": "...", "•": "-", " ": " "})


def _t(text: object) -> str:
    """Escape for reportlab's mini-markup and drop characters the base fonts cannot draw."""
    s = str(text).translate(_ASCII)
    return escape(s.encode("latin-1", "replace").decode("latin-1"))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5,
                          leading=13, textColor=INK, alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=18, leading=22,
                                spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=body, textColor=MUTED, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=13, leading=17,
                             spaceBefore=12, spaceAfter=6, keepWithNext=1),
        "h3": ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                             spaceBefore=6, spaceAfter=3, keepWithNext=1),
        "body": body,
        "claim": ParagraphStyle("claim", parent=body, leftIndent=16, firstLineIndent=-12, spaceAfter=5),
        "small": ParagraphStyle("small", parent=body, fontSize=8, leading=10.5),
        "muted": ParagraphStyle("muted", parent=body, fontSize=8.5, leading=11, textColor=MUTED),
    }


def _table(rows: list[list], widths: list[float], header: bool = True) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), HEADER_BG))
    table.setStyle(TableStyle(style))
    return table


def _claim_ids(claim) -> list[str]:
    return list(dict.fromkeys([f.evidence_id for f in claim.figures] + claim.evidence_ids))


def _side_section(side: str, result: SideResult, st: dict) -> list:
    color = SIDE_COLOR.get(side, INK).hexval()[2:]
    out = [Paragraph(f'<font color="#{color}">{_t(side.title())} case</font>', st["h2"])]
    if not result.claims:
        out.append(Paragraph("No claim survived verification.", st["muted"]))
    for n, claim in enumerate(result.claims, 1):
        ids = " ".join(f"[{i}]" for i in _claim_ids(claim))
        out.append(Paragraph(f"{n}. {_t(claim.text)} <font color='#616e7c' size='8'>{_t(ids)}</font>",
                             st["claim"]))
    if result.removed:
        out.append(Paragraph(f"{len(result.removed)} claim(s) removed by the critic after "
                             f"{len(result.rounds) - 1} revision round(s); see the critic log.", st["muted"]))
    return out


def _critic_section(report: DebateReport, st: dict) -> list:
    out = [Paragraph("Critic log", st["h2"]),
           Paragraph("Every numeric figure is checked in Python against the evidence it cites (value, "
                     "scale and fiscal year); qualitative statements are checked against the cited 10-K "
                     "passages. Failed claims go back to their author; claims still failing after the last "
                     "revision are removed.", st["muted"]), Spacer(1, 4)]
    rows = [[Paragraph("<b>Side</b>", st["small"]), Paragraph("<b>Round</b>", st["small"]),
             Paragraph("<b>Passed</b>", st["small"]), Paragraph("<b>Failed</b>", st["small"])]]
    for side, result in report.sides.items():
        for n, verdicts in enumerate(result.rounds):
            failed = sum(not v.passed for v in verdicts)
            rows.append([Paragraph(_t(side), st["small"]), Paragraph(str(n), st["small"]),
                         Paragraph(str(len(verdicts) - failed), st["small"]),
                         Paragraph(f"<font color='#{(FAIL if failed else PASS).hexval()[2:]}'>{failed}</font>",
                                   st["small"])])
    out.append(_table(rows, [1.2 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch]))

    for side, result in report.sides.items():
        if result.plant:
            caught = "CAUGHT" if result.plant_caught else "MISSED"
            color = (PASS if result.plant_caught else FAIL).hexval()[2:]
            out.append(Paragraph(f"Planted error ({_t(side)}, {_t(result.plant.kind)}): "
                                 f"<font color='#{color}'><b>{caught}</b></font>", st["h3"]))
            out.append(Paragraph(f"Original: {_t(result.plant.original)}", st["small"]))
            out.append(Paragraph(f"Planted: {_t(result.plant.planted)}", st["small"]))
        for n, verdicts in enumerate(result.rounds):
            failed = [v for v in verdicts if not v.passed]
            if not failed:
                continue
            out.append(Paragraph(f"{_t(side.title())}, round {n}: rejected claims", st["h3"]))
            for v in failed:
                issues = "; ".join(f"{i.code}: {i.detail}" for i in v.issues)
                out.append(Paragraph(f"- {_t(v.claim.text)}<br/><font color='#b3261e'>{_t(issues)}</font>",
                                     st["claim"]))
    return out


def _sources_section(report: DebateReport, st: dict, excerpt_chars: int) -> list:
    cited: list[str] = []
    for result in report.sides.values():
        for claim in result.claims:
            cited = list(dict.fromkeys(cited + _claim_ids(claim)))
    rows = [[Paragraph("<b>ID</b>", st["small"]), Paragraph("<b>Evidence</b>", st["small"]),
             Paragraph("<b>Source</b>", st["small"])]]
    for eid in cited:
        ev = report.ledger.get(eid)
        if ev is None:
            continue
        if ev.kind == "passage":
            text = " ".join((ev.text or "").split())
            excerpt = text[:excerpt_chars] + ("..." if len(text) > excerpt_chars else "")
            what = f"<b>{_t(ev.label)}</b><br/><font color='#616e7c'>{_t(excerpt)}</font>"
        else:
            what = f"<b>{_t(ev.label)}</b>: {_t(ev.display)}"
        rows.append([Paragraph(_t(eid), st["small"]), Paragraph(what, st["small"]),
                     Paragraph(_t(ev.source), st["small"])])
    return [Paragraph("Sources", st["h2"]),
            Paragraph("F = XBRL fact as filed, C = deterministic calculation (inputs listed), "
                      "P = retrieved 10-K passage.", st["muted"]), Spacer(1, 4),
            _table(rows, [0.45 * inch, 3.0 * inch, 3.55 * inch])]


def render_pdf(report: DebateReport, path: Path, strategy: str, model_id: str | None = None,
               fiscal_years: tuple[int, int] | None = None, excerpt_chars: int = 400,
               compress: bool = True) -> Path:
    st = _styles()
    cid = report.conversation_id or "n/a"
    meta = [("Model", model_id or report.brief.model_id or "n/a"), ("Retrieval chunker", strategy),
            ("Fiscal years", f"FY{fiscal_years[1]} vs FY{fiscal_years[0]}" if fiscal_years else "n/a"),
            ("Run date", date.today().isoformat()), ("Conversation id", cid),
            ("Runtime", f"{report.seconds}s"),
            ("Tokens (in / out)", f"{report.usage.input_tokens:,} / {report.usage.output_tokens:,}")]
    for side, result in report.sides.items():
        meta.append((f"{side.title()} claims", f"{len(result.claims)} kept, {len(result.removed)} removed"))

    story = [Paragraph(f"{_t(report.ticker)}: bull vs bear", st["title"]),
             Paragraph("Equity research from SEC filings. Every number below was verified against the "
                       "company's filed XBRL data before it was accepted.", st["subtitle"]),
             _table([[Paragraph(f"<b>{_t(k)}</b>", st["small"]), Paragraph(_t(v), st["small"])] for k, v in meta],
                    [1.6 * inch, 5.4 * inch], header=False)]
    for side, result in report.sides.items():
        story.append(KeepTogether(_side_section(side, result, st)))
    story += _critic_section(report, st)
    story += _sources_section(report, st, excerpt_chars)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 0.5 * inch, f"{report.ticker} | conversation_id {cid}")
        canvas.drawRightString(LETTER[0] - doc.rightMargin, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.8 * inch, pageCompression=int(compress),
                            title=f"{report.ticker}: bull vs bear (verified)", author="equity-research-agent")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path

"""
PDF report generation using ReportLab.

Imports shared color constants and _build_inline_segments from report_service.
Provides:
  generate_pdf_report()   — redline-style full document report
  generate_pdf_summary()  — analytics summary (score, tables, jurisdiction)
"""
import io
import logging

logger = logging.getLogger(__name__)

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether,
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    _REPORTLAB_AVAILABLE = True
except ImportError:
    _REPORTLAB_AVAILABLE = False
    logger.warning("ReportLab not installed — PDF generation unavailable")

from .report_service import (
    _build_inline_segments,
    _get_risk_info,
    CATEGORY_ORDER,
    _CATEGORY_RISK_LEVEL,
    COLOR_RED, COLOR_GREEN, COLOR_ORANGE, COLOR_BLUE,
    COLOR_BLACK, COLOR_GREY, COLOR_DARK, COLOR_RULE,
    BG_RED, BG_GREEN, BG_ORANGE, BG_BLUE,
    COLOR_SCORE_HIGH, COLOR_SCORE_MED, COLOR_SCORE_LOW,
)

# ── Layout constants ──────────────────────────────────────────────────────────
PAGE_USABLE_W = A4[0] - 40 * mm if _REPORTLAB_AVAILABLE else 515  # ~172mm


def _hex(h: str):
    """Convert #rrggbb hex string to ReportLab HexColor."""
    if not _REPORTLAB_AVAILABLE:
        return None
    return colors.HexColor(h)


def _bg_row(table_data: list, row_idx: int, hex_color: str) -> TableStyle:
    """Return a single-row background TableStyle command."""
    return ("BACKGROUND", (0, row_idx), (-1, row_idx), _hex(hex_color))


def _dark_header_table(headers: list, col_widths: list) -> "Table":
    """Create a styled header row table with dark background."""
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "HeaderCell",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.white,
        leading=12,
    )
    row = [Paragraph(h, header_style) for h in headers]
    t = Table([row], colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _hex(COLOR_DARK)),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [_hex(COLOR_DARK)]),
        ("GRID", (0, 0), (-1, -1), 0.5, _hex(COLOR_RULE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _pdf_agreement_block(agreement_meta: dict, styles) -> list:
    """Return a list of Platypus flowables for the Agreement Details block."""
    if not agreement_meta:
        return []

    flowables = []
    h2 = styles["h2"]
    normal = styles["Normal"]

    flowables.append(Paragraph("Agreement Details", h2))
    flowables.append(Spacer(1, 4))

    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}
    parties   = agreement_meta.get("parties") or {}
    prop      = agreement_meta.get("property") or {}

    kv_style = ParagraphStyle(
        "KV", parent=normal, fontSize=10, leading=15,
    )

    if agmt_type:
        flowables.append(Paragraph(f"<b>Agreement Type:</b> {agmt_type}", kv_style))
    if agmt_det.get("agreement_date"):
        flowables.append(Paragraph(f"<b>Agreement Date:</b> {agmt_det['agreement_date']}", kv_style))
    place = ", ".join(v for v in [agmt_det.get("city"), agmt_det.get("state")] if v)
    if place:
        flowables.append(Paragraph(f"<b>Location:</b> {place}", kv_style))

    landlord = parties.get("landlord") or {}
    tenant   = parties.get("tenant") or {}
    if any(landlord.values()) or any(tenant.values()):
        flowables.append(Spacer(1, 6))
        flowables.append(Paragraph("Parties", styles["h3"]))
        if landlord.get("name"):
            flowables.append(Paragraph(f"<b>Landlord:</b> {landlord['name']}", kv_style))
        if landlord.get("address"):
            flowables.append(Paragraph(f"<b>Landlord Address:</b> {landlord['address']}", kv_style))
        if landlord.get("contact"):
            flowables.append(Paragraph(f"<b>Landlord Contact:</b> {landlord['contact']}", kv_style))
        tenant_name = tenant.get("company_name") or tenant.get("name", "")
        if tenant_name:
            flowables.append(Paragraph(f"<b>Tenant:</b> {tenant_name}", kv_style))
        if tenant.get("authorized_signatory"):
            flowables.append(Paragraph(f"<b>Authorized By:</b> {tenant['authorized_signatory']}", kv_style))
        if tenant.get("address"):
            flowables.append(Paragraph(f"<b>Tenant Address:</b> {tenant['address']}", kv_style))
        if tenant.get("contact"):
            flowables.append(Paragraph(f"<b>Tenant Contact:</b> {tenant['contact']}", kv_style))

    if any(prop.values()):
        flowables.append(Spacer(1, 6))
        flowables.append(Paragraph("Property", styles["h3"]))
        if prop.get("type"):
            flowables.append(Paragraph(f"<b>Type:</b> {prop['type']}", kv_style))
        if prop.get("area_sqft"):
            flowables.append(Paragraph(f"<b>Area:</b> {prop['area_sqft']} sq ft", kv_style))
        if prop.get("address"):
            flowables.append(Paragraph(f"<b>Address:</b> {prop['address']}", kv_style))

    flowables.append(HRFlowable(width="100%", thickness=1, color=_hex(COLOR_RULE)))
    flowables.append(Spacer(1, 8))
    return flowables


# ══════════════════════════════════════════════════════════════════════════════
#  PDF REPORT — redline-style full document
# ══════════════════════════════════════════════════════════════════════════════

def generate_pdf_report(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> bytes:
    """
    Generate a redline-style PDF report.

    Returns raw PDF bytes. Raises RuntimeError if ReportLab is not installed.
    """
    if not _REPORTLAB_AVAILABLE:
        raise RuntimeError("ReportLab is not installed. Cannot generate PDF reports.")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(
        "DocNormal", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10, leading=15, textColor=_hex(COLOR_BLACK),
    )
    violation_style = ParagraphStyle(
        "Violation", parent=normal_style,
        backColor=_hex(BG_RED), textColor=_hex(COLOR_RED),
    )
    ai_style = ParagraphStyle(
        "AIAdded", parent=normal_style,
        backColor=_hex(BG_GREEN), textColor=_hex(COLOR_GREEN),
    )
    partial_style = ParagraphStyle(
        "Partial", parent=normal_style,
        backColor=_hex(BG_ORANGE), textColor=_hex(COLOR_ORANGE),
    )
    match_style = ParagraphStyle(
        "Match", parent=normal_style,
        backColor=_hex(BG_GREEN),
    )
    not_found_style = ParagraphStyle(
        "NotFound", parent=normal_style,
        backColor=_hex(BG_BLUE), textColor=_hex(COLOR_BLUE),
    )
    heading_style = ParagraphStyle(
        "DocHeading", parent=styles["h3"],
        fontName="Helvetica-Bold", fontSize=13, spaceAfter=4,
        textColor=_hex(COLOR_DARK),
    )

    segments = _build_inline_segments(full_text, analysis_summary)
    story = []

    # Title
    story.append(Paragraph("Document Analysis Report", styles["h1"]))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=2, color=_hex(COLOR_DARK)))
    story.append(Spacer(1, 12))

    # Agreement block
    if agreement_meta:
        story.extend(_pdf_agreement_block(agreement_meta, styles))

    # Document content with annotations
    story.append(Paragraph("Document with Annotations", styles["h2"]))
    story.append(Spacer(1, 6))

    i = 0
    while i < len(segments):
        seg   = segments[i]
        stype = seg["type"]
        text  = seg["text"].replace("<br>", "\n").replace("<b>", "<b>").replace("</b>", "</b>")

        if stype == "violation":
            block = [Paragraph(f"<strike>{text}</strike>", violation_style)]
            if i + 1 < len(segments) and segments[i + 1]["type"] == "ai":
                i += 1
                block.append(Paragraph(segments[i]["text"], ai_style))
            story.append(KeepTogether(block))
            story.append(Spacer(1, 4))

        elif stype == "partial":
            block = [Paragraph(f"<strike>{text}</strike>", partial_style)]
            if i + 1 < len(segments) and segments[i + 1]["type"] == "ai":
                i += 1
                block.append(Paragraph(segments[i]["text"], ai_style))
            story.append(KeepTogether(block))
            story.append(Spacer(1, 4))

        elif stype == "not_found_ai":
            story.append(Paragraph(f"+ {text}", not_found_style))
            story.append(Spacer(1, 4))

        elif stype == "match":
            story.append(Paragraph(text, match_style))

        elif stype == "heading":
            story.append(Paragraph(text, heading_style))
            story.append(Spacer(1, 4))

        elif stype in ("normal", "bullet"):
            story.append(Paragraph(text, normal_style))

        elif stype == "ai":
            story.append(Paragraph(f"+ {text}", ai_style))
            story.append(Spacer(1, 4))

        elif stype == "blank":
            story.append(Spacer(1, 6))

        i += 1

    doc.build(story)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  PDF SUMMARY — analytics dashboard
# ══════════════════════════════════════════════════════════════════════════════

def generate_pdf_summary(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> bytes:
    """
    Generate a PDF analytics summary with compliance score, tables, and jurisdiction.

    Returns raw PDF bytes. Raises RuntimeError if ReportLab is not installed.
    """
    if not _REPORTLAB_AVAILABLE:
        raise RuntimeError("ReportLab is not installed. Cannot generate PDF reports.")

    from collections import defaultdict

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    h2     = styles["h2"]
    h3     = styles["h3"]

    cell_style = ParagraphStyle(
        "Cell", parent=normal, fontSize=9, leading=12,
    )

    jurisdiction_info = jurisdiction_info or {}

    match_count     = sum(1 for c in analysis_summary if c["result"] == "MATCH")
    violation_count = sum(1 for c in analysis_summary if c["result"] == "VIOLATION")
    partial_count   = sum(1 for c in analysis_summary if c["result"] == "PARTIALLY_SATISFIED")
    not_found_count = sum(1 for c in analysis_summary if c["result"] == "NOT_FOUND")
    total           = len(analysis_summary)
    compliance_score = round((match_count / total) * 100) if total else 0

    if compliance_score >= 80:
        score_color  = COLOR_SCORE_HIGH
        score_status = "Compliant"
    elif compliance_score >= 50:
        score_color  = COLOR_SCORE_MED
        score_status = "Needs Attention"
    else:
        score_color  = COLOR_SCORE_LOW
        score_status = "Critical"

    story = []

    # Title
    story.append(Paragraph("Document Analysis Summary", styles["h1"]))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=2, color=_hex(COLOR_DARK)))
    story.append(Spacer(1, 12))

    # Agreement block
    if agreement_meta:
        story.extend(_pdf_agreement_block(agreement_meta, styles))

    # Compliance Score
    score_para_style = ParagraphStyle(
        "Score", parent=normal,
        fontName="Helvetica-Bold", fontSize=18, textColor=_hex(score_color),
        alignment=TA_CENTER,
    )
    story.append(Paragraph(f"Overall Compliance Score: {compliance_score}% — {score_status}", score_para_style))
    story.append(Spacer(1, 12))

    # Stats table
    col_w = PAGE_USABLE_W / 5
    stats_header = _dark_header_table(
        ["Total", "Match", "Violation", "Partial", "Not Found"],
        [col_w] * 5,
    )
    story.append(stats_header)

    stats_data = [[
        Paragraph(str(total),           cell_style),
        Paragraph(str(match_count),     cell_style),
        Paragraph(str(violation_count), cell_style),
        Paragraph(str(partial_count),   cell_style),
        Paragraph(str(not_found_count), cell_style),
    ]]
    stats_table = Table(stats_data, colWidths=[col_w] * 5)
    stats_table.setStyle(TableStyle([
        ("GRID",         (0, 0), (-1, -1), 0.5, _hex(COLOR_RULE)),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
    ]))
    story.append(stats_table)
    story.append(Spacer(1, 16))

    # Critical issues
    story.append(Paragraph("Critical Issues Requiring Immediate Attention", h2))
    story.append(Spacer(1, 6))
    red_flags = [e for e in analysis_summary if e["result"] in ("VIOLATION", "NOT_FOUND")][:3]

    if not red_flags:
        story.append(Paragraph("No critical issues found.", normal))
    else:
        col_ws = [PAGE_USABLE_W * 0.35, PAGE_USABLE_W * 0.2, PAGE_USABLE_W * 0.45]
        hdr = _dark_header_table(["Clause", "Status", "Issue"], col_ws)
        story.append(hdr)
        rows = []
        for flag in red_flags:
            status_color = COLOR_RED if flag["result"] == "VIOLATION" else COLOR_BLUE
            rows.append([
                Paragraph(flag["clause_title"], cell_style),
                Paragraph(f'<font color="{status_color}"><b>{flag["result"]}</b></font>', cell_style),
                Paragraph(flag.get("reason", ""), cell_style),
            ])
        t = Table(rows, colWidths=col_ws)
        row_cmds = [("GRID", (0, 0), (-1, -1), 0.5, _hex(COLOR_RULE)),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8)]
        for ri, flag in enumerate(red_flags):
            bg = BG_RED if flag["result"] == "VIOLATION" else BG_BLUE
            row_cmds.append(_bg_row([], ri, bg))
        t.setStyle(TableStyle(row_cmds))
        story.append(t)

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex(COLOR_RULE)))
    story.append(Spacer(1, 8))

    # Risk category breakdown
    story.append(Paragraph("Risk Category Breakdown", h2))
    story.append(Spacer(1, 6))

    category_stats: dict = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    cat_col_ws = [
        PAGE_USABLE_W * 0.35, PAGE_USABLE_W * 0.2,
        PAGE_USABLE_W * 0.15, PAGE_USABLE_W * 0.15, PAGE_USABLE_W * 0.15,
    ]
    hdr = _dark_header_table(["Category", "Risk Level", "Total", "Compliant", "Issues"], cat_col_ws)
    story.append(hdr)

    cat_rows = []
    for cat in CATEGORY_ORDER:
        if cat not in category_stats:
            continue
        risk_lv, risk_color = _CATEGORY_RISK_LEVEL[cat]
        s = category_stats[cat]
        cat_rows.append([
            Paragraph(cat, cell_style),
            Paragraph(f'<font color="{risk_color}"><b>{risk_lv}</b></font>', cell_style),
            Paragraph(str(s["total"]), cell_style),
            Paragraph(str(s["compliant"]), cell_style),
            Paragraph(str(s["issues"]), cell_style),
        ])

    if cat_rows:
        ct = Table(cat_rows, colWidths=cat_col_ws)
        ct.setStyle(TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.5, _hex(COLOR_RULE)),
            ("ALIGN",         (2, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_hex("#ffffff"), _hex("#f8f9fa")]),
        ]))
        story.append(ct)

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex(COLOR_RULE)))
    story.append(Spacer(1, 8))

    # Jurisdiction
    story.append(Paragraph("Jurisdiction & Compliance Checklist", h2))
    story.append(Spacer(1, 6))
    if jurisdiction_info:
        juris     = jurisdiction_info.get("jurisdiction", "Unknown")
        agmt_type = jurisdiction_info.get("agreement_type", "Unknown")
        laws      = ", ".join(jurisdiction_info.get("applicable_laws", []))
        kv = ParagraphStyle("KV", parent=normal, fontSize=10, leading=15)
        story.append(Paragraph(f"<b>Jurisdiction:</b> {juris} &nbsp;&nbsp; <b>Agreement Type:</b> {agmt_type}", kv))
        if laws:
            story.append(Paragraph(f"<b>Applicable Laws:</b> {laws}", kv))
        story.append(Spacer(1, 8))
        checklist = jurisdiction_info.get("checklist", [])
        if checklist:
            jcol_ws = [PAGE_USABLE_W * 0.8, PAGE_USABLE_W * 0.2]
            hdr = _dark_header_table(["Requirement", "Status"], jcol_ws)
            story.append(hdr)
            jrows = []
            for item in checklist:
                req_label = "Required" if item.get("required") else "Optional"
                req_color = COLOR_RED if item.get("required") else COLOR_GREEN
                jrows.append([
                    Paragraph(item.get("item", ""), cell_style),
                    Paragraph(f'<font color="{req_color}"><b>{req_label}</b></font>', cell_style),
                ])
            jt = Table(jrows, colWidths=jcol_ws)
            jt.setStyle(TableStyle([
                ("GRID",          (0, 0), (-1, -1), 0.5, _hex(COLOR_RULE)),
                ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING",    (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_hex("#ffffff"), _hex("#f8f9fa")]),
            ]))
            story.append(jt)
    else:
        story.append(Paragraph("Jurisdiction information not available.", normal))

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex(COLOR_RULE)))
    story.append(Spacer(1, 8))

    # Contract Timeline
    story.append(Paragraph("Contract Timeline", h2))
    story.append(Spacer(1, 6))
    timeline_rows = [
        (entry["clause_title"], dt)
        for entry in analysis_summary
        for dt in entry.get("key_dates_durations", [])
        if dt
    ]
    if not timeline_rows:
        story.append(Paragraph("No key dates or durations identified.", normal))
    else:
        tl_col_ws = [PAGE_USABLE_W * 0.4, PAGE_USABLE_W * 0.6]
        hdr = _dark_header_table(["Clause", "Timeline Item"], tl_col_ws)
        story.append(hdr)
        tl_data = [
            [Paragraph(ct, cell_style), Paragraph(ti, cell_style)]
            for ct, ti in timeline_rows
        ]
        tl_table = Table(tl_data, colWidths=tl_col_ws)
        tl_table.setStyle(TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.5, _hex(COLOR_RULE)),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [_hex("#ffffff"), _hex("#f8f9fa")]),
        ]))
        story.append(tl_table)

    doc.build(story)
    return buf.getvalue()

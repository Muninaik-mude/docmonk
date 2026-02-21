import io
import logging
from collections import defaultdict
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ── Color constants ─────────────────────────────────────────────────────────────
COLOR_RED    = "#dc3545"
COLOR_GREEN  = "#28a745"
COLOR_ORANGE = "#fd7e14"
COLOR_BLACK  = "#212529"
COLOR_GREY   = "#6c757d"
COLOR_DARK   = "#1a1a2e"
COLOR_RULE   = "#dee2e6"

# Status background colors
BG_RED    = "#fde8e8"
BG_GREEN  = "#d4edda"
BG_ORANGE = "#fff3cd"

# Risk level colors (text)
COLOR_RISK = {
    "HIGH":   "#dc3545",
    "MEDIUM": "#fd7e14",
    "LOW":    "#28a745",
}

# Compliance score band colors
COLOR_SCORE_HIGH = "#28a745"   # >= 80%
COLOR_SCORE_MED  = "#fd7e14"   # 50–79%
COLOR_SCORE_LOW  = "#dc3545"   # < 50%

# A4 usable width = 595.28 - 2 × 20mm margins
PAGE_USABLE_W = A4[0] - 2 * (20 * mm)


# ── Risk categorization ─────────────────────────────────────────────────────────

_LEGAL_KEYWORDS = [
    "governing law", "arbitration", "dispute", "indemnif", "terminat",
    "subletting", "assignment", "permitted use", "entire agreement",
    "force majeure", "jurisdiction", "liabilit",
]
_OPERATIONAL_KEYWORDS = [
    "rent", "lease term", "security deposit", "maintenance", "repair",
    "utilities", "insurance", "renewal", "alteration", "property",
    "payment",
]
_PROCESS_KEYWORDS = [
    "witness", "registration", "stamp duty", "parties", "notarization",
    "signatory", "execution",
]

CATEGORY_ORDER = ["Legal Risk", "Operational Risk", "Process Risk", "General"]

_CATEGORY_RISK_LEVEL = {
    "Legal Risk":      ("HIGH",   COLOR_RISK["HIGH"]),
    "Operational Risk": ("MEDIUM", COLOR_RISK["MEDIUM"]),
    "Process Risk":    ("LOW",    COLOR_RISK["LOW"]),
    "General":         ("MEDIUM", COLOR_RISK["MEDIUM"]),
}


def _get_risk_info(clause_title: str) -> tuple[str, str]:
    """
    Returns (category, risk_level) based on clause title keyword matching.
      Legal Risk      → HIGH
      Operational Risk → MEDIUM
      Process Risk    → LOW
    """
    t = clause_title.lower()
    for kw in _LEGAL_KEYWORDS:
        if kw in t:
            return "Legal Risk", "HIGH"
    for kw in _OPERATIONAL_KEYWORDS:
        if kw in t:
            return "Operational Risk", "MEDIUM"
    for kw in _PROCESS_KEYWORDS:
        if kw in t:
            return "Process Risk", "LOW"
    return "General", "MEDIUM"


# ── Helper ──────────────────────────────────────────────────────────────────────

def _bg_row(text: str, bg_hex: str, style: ParagraphStyle) -> Table:
    """Wrap a Paragraph in a full-width single-cell Table with a background color."""
    tbl = Table([[Paragraph(text, style)]], colWidths=[PAGE_USABLE_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor(bg_hex)),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


# ── PDF Report ──────────────────────────────────────────────────────────────────

def generate_pdf_report(analysis_summary: list) -> bytes:
    """
    Generate a color-coded PDF compliance report with:
      - Overall compliance score
      - Risk category breakdown table (Legal / Operational / Process)
      - Per-clause risk level badge + status badge
      - Colored backgrounds for VIOLATION / NOT_FOUND / MATCH
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    S = getSampleStyleSheet()

    # ── Paragraph styles ──────────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "RTitle", parent=S["Title"],
        fontSize=16, fontName="Helvetica-Bold",
        textColor=colors.HexColor(COLOR_DARK),
        alignment=TA_CENTER, spaceAfter=3,
    )
    meta_style = ParagraphStyle(
        "RMeta", parent=S["Normal"],
        fontSize=8, textColor=colors.HexColor(COLOR_GREY),
        alignment=TA_CENTER, spaceAfter=10,
    )
    summary_style = ParagraphStyle(
        "RSummary", parent=S["Normal"],
        fontSize=9, leading=14, spaceAfter=6,
    )
    score_big_style = ParagraphStyle(
        "RScoreBig", parent=S["Normal"],
        fontSize=32, fontName="Helvetica-Bold",
        alignment=TA_RIGHT, leading=36,
    )
    score_label_left_style = ParagraphStyle(
        "RScoreLabelLeft", parent=S["Normal"],
        fontSize=11, fontName="Helvetica-Bold",
        textColor=colors.HexColor(COLOR_DARK),
        leading=16,
    )
    section_header_style = ParagraphStyle(
        "RSectionHeader", parent=S["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        textColor=colors.HexColor(COLOR_DARK),
        spaceBefore=6, spaceAfter=4,
    )
    clause_header_style = ParagraphStyle(
        "RClauseHeader", parent=S["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        textColor=colors.HexColor(COLOR_DARK),
        spaceBefore=0, spaceAfter=2,
    )
    risk_badge_style = ParagraphStyle(
        "RRisk", parent=S["Normal"],
        fontSize=8, fontName="Helvetica-Bold",
        alignment=TA_CENTER, spaceBefore=0, spaceAfter=2,
    )
    status_label_style = ParagraphStyle(
        "RStatus", parent=S["Normal"],
        fontSize=9, fontName="Helvetica-Bold",
        alignment=TA_RIGHT, spaceBefore=0, spaceAfter=2,
    )
    inner_style = ParagraphStyle(
        "RInner", parent=S["Normal"],
        fontSize=9, leading=13,
        textColor=colors.HexColor(COLOR_BLACK),
    )
    inner_italic_style = ParagraphStyle(
        "RInnerItalic", parent=S["Normal"],
        fontSize=9, leading=13, fontName="Helvetica-Oblique",
        textColor=colors.HexColor(COLOR_BLACK),
    )
    plain_text_style = ParagraphStyle(
        "RPlainText", parent=S["Normal"],
        fontSize=9, leading=13, leftIndent=8,
        spaceAfter=3, textColor=colors.HexColor(COLOR_BLACK),
    )
    tbl_header_style = ParagraphStyle(
        "RTblHeader", parent=S["Normal"],
        fontSize=8, fontName="Helvetica-Bold",
        textColor=colors.white, alignment=TA_CENTER,
    )
    tbl_cell_style = ParagraphStyle(
        "RTblCell", parent=S["Normal"],
        fontSize=8, alignment=TA_CENTER,
    )

    story = []

    # ── Pre-compute stats ─────────────────────────────────────────────────────
    match_count     = sum(1 for c in analysis_summary if c["result"] == "MATCH")
    violation_count = sum(1 for c in analysis_summary if c["result"] == "VIOLATION")
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

    # Build per-category stats
    category_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    # ── Title block ───────────────────────────────────────────────────────────
    story.append(Paragraph("CLAUSE COMPLIANCE ANALYSIS REPORT", title_style))
    story.append(Paragraph(
        f"Generated on {datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')}",
        meta_style,
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor(COLOR_DARK)))
    story.append(Spacer(1, 8))

    # ── Clause counts summary line ────────────────────────────────────────────
    story.append(Paragraph(
        f'<b>Clause Summary</b> &nbsp;&nbsp; Total: <b>{total}</b> &nbsp;|&nbsp; '
        f'<font color="{COLOR_GREEN}">Match: <b>{match_count}</b></font> &nbsp;|&nbsp; '
        f'<font color="{COLOR_RED}">Violation: <b>{violation_count}</b></font> &nbsp;|&nbsp; '
        f'<font color="{COLOR_ORANGE}">Not Found: <b>{not_found_count}</b></font>',
        summary_style,
    ))

    # ── Overall compliance score (side-by-side: label left, number right) ──────
    score_tbl = Table(
        [[
            Paragraph(
                f'<b>Overall Compliance Score</b><br/>'
                f'<font color="{score_color}">{score_status}</font>',
                score_label_left_style,
            ),
            Paragraph(
                f'<font color="{score_color}"><b>{compliance_score}%</b></font>',
                score_big_style,
            ),
        ]],
        colWidths=[PAGE_USABLE_W * 0.65, PAGE_USABLE_W * 0.35],
    )
    score_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(score_tbl)
    story.append(Spacer(1, 8))

    # ── Risk Category Breakdown table ─────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(COLOR_RULE)))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Risk Category Breakdown", section_header_style))

    cat_data = [[
        Paragraph("Category",      tbl_header_style),
        Paragraph("Risk Level",    tbl_header_style),
        Paragraph("Total Clauses", tbl_header_style),
        Paragraph("Compliant",     tbl_header_style),
        Paragraph("Issues",        tbl_header_style),
    ]]

    for cat in CATEGORY_ORDER:
        if cat not in category_stats:
            continue
        stats = category_stats[cat]
        risk_lv, risk_col = _CATEGORY_RISK_LEVEL[cat]
        cat_data.append([
            Paragraph(cat, tbl_cell_style),
            Paragraph(f'<font color="{risk_col}"><b>{risk_lv}</b></font>', tbl_cell_style),
            Paragraph(str(stats["total"]),     tbl_cell_style),
            Paragraph(
                f'<font color="{COLOR_GREEN}"><b>{stats["compliant"]}</b></font>',
                tbl_cell_style,
            ),
            Paragraph(
                f'<font color="{COLOR_RED}"><b>{stats["issues"]}</b></font>',
                tbl_cell_style,
            ),
        ])

    cat_tbl = Table(
        cat_data,
        colWidths=[
            PAGE_USABLE_W * 0.30,
            PAGE_USABLE_W * 0.18,
            PAGE_USABLE_W * 0.17,
            PAGE_USABLE_W * 0.17,
            PAGE_USABLE_W * 0.18,
        ],
    )
    cat_tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor(COLOR_DARK)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor(COLOR_RULE)),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(cat_tbl)
    story.append(Spacer(1, 10))

    # ── Clause details section ────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(COLOR_RULE)))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Clause Details", section_header_style))
    story.append(Spacer(1, 4))

    for idx, entry in enumerate(analysis_summary, start=1):
        result         = entry.get("result", "NOT_FOUND")
        clause_title   = entry.get("clause_title", "")
        clause_content = entry.get("clause_content", "")
        ai_text        = entry.get("ai_added_text", "")

        category, risk_level = _get_risk_info(clause_title)
        risk_color = COLOR_RISK[risk_level]

        if result == "VIOLATION":
            status_color = COLOR_RED
            status_label = "VIOLATION"
            clause_bg    = BG_RED
            ai_bg        = BG_GREEN
            ai_label     = "Corrective Action"
        elif result == "NOT_FOUND":
            status_color = COLOR_ORANGE
            status_label = "NOT FOUND"
            clause_bg    = None
            ai_bg        = BG_ORANGE
            ai_label     = "Recommended Addition"
        else:
            status_color = COLOR_GREEN
            status_label = "MATCH"
            clause_bg    = None
            ai_bg        = None
            ai_label     = None

        # ── Header row: "N. Title"  |  [RISK LEVEL]  |  [STATUS] ──────────────
        header_tbl = Table(
            [[
                Paragraph(f"<b>{idx}. {clause_title}</b>", clause_header_style),
                Paragraph(
                    f'<font color="{risk_color}"><b>[{risk_level} RISK]</b></font>',
                    risk_badge_style,
                ),
                Paragraph(
                    f'<font color="{status_color}"><b>[{status_label}]</b></font>',
                    status_label_style,
                ),
            ]],
            colWidths=[
                PAGE_USABLE_W * 0.55,
                PAGE_USABLE_W * 0.22,
                PAGE_USABLE_W * 0.23,
            ],
        )
        header_tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        story.append(header_tbl)

        # ── Clause content row ────────────────────────────────────────────────
        if clause_content:
            if clause_bg:
                story.append(_bg_row(f"Clause: {clause_content}", clause_bg, inner_style))
            else:
                story.append(Paragraph(f"Clause: {clause_content}", plain_text_style))
            story.append(Spacer(1, 2))

        # ── AI recommendation row ─────────────────────────────────────────────
        if ai_text and ai_bg:
            story.append(_bg_row(f"{ai_label}: {ai_text}", ai_bg, inner_italic_style))
            story.append(Spacer(1, 2))

        story.append(HRFlowable(width="100%", thickness=0.3, color=colors.HexColor(COLOR_RULE)))
        story.append(Spacer(1, 6))

    doc.build(story)
    return buffer.getvalue()


# ── Markdown Report ─────────────────────────────────────────────────────────────

def generate_markdown_report(analysis_summary: list) -> str:
    """
    Generate a Markdown compliance report from analysis_summary.
    Includes compliance score and risk category breakdown.
    """
    match_count     = sum(1 for c in analysis_summary if c["result"] == "MATCH")
    violation_count = sum(1 for c in analysis_summary if c["result"] == "VIOLATION")
    not_found_count = sum(1 for c in analysis_summary if c["result"] == "NOT_FOUND")
    total           = len(analysis_summary)
    compliance_score = round((match_count / total) * 100) if total else 0
    generated_at     = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    if compliance_score >= 80:
        score_status = "✅ Compliant"
    elif compliance_score >= 50:
        score_status = "⚠️ Needs Attention"
    else:
        score_status = "🔴 Critical"

    # Category stats
    category_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    lines = [
        "# Clause Compliance Analysis Report",
        "",
        f"> Generated on {generated_at}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"**Overall Compliance Score: {compliance_score}% — {score_status}**",
        "",
        "| Total | Match | Violation | Not Found |",
        "|:---:|:---:|:---:|:---:|",
        f"| **{total}** | **{match_count}** | **{violation_count}** | **{not_found_count}** |",
        "",
        "## Risk Category Breakdown",
        "",
        "| Category | Risk Level | Total | Compliant | Issues |",
        "|:---|:---:|:---:|:---:|:---:|",
    ]

    risk_badges = {"HIGH": "🔴 HIGH", "MEDIUM": "🟠 MEDIUM", "LOW": "🟢 LOW"}
    for cat in CATEGORY_ORDER:
        if cat not in category_stats:
            continue
        risk_lv, _ = _CATEGORY_RISK_LEVEL[cat]
        s = category_stats[cat]
        lines.append(
            f"| {cat} | {risk_badges[risk_lv]} | {s['total']} | {s['compliant']} | {s['issues']} |"
        )

    lines += ["", "---", "", "## Clause Details", ""]

    for idx, entry in enumerate(analysis_summary, start=1):
        result         = entry.get("result", "NOT_FOUND")
        clause_title   = entry.get("clause_title", "")
        clause_id      = entry.get("clause_id", "")
        clause_content = entry.get("clause_content", "")
        ai_text        = entry.get("ai_added_text", "")
        category, risk_level = _get_risk_info(clause_title)

        if result == "VIOLATION":
            badge    = "🔴 VIOLATION"
            ai_label = "Corrective Action"
        elif result == "NOT_FOUND":
            badge    = "🟠 NOT FOUND"
            ai_label = "Recommended Addition"
        else:
            badge    = "✅ MATCH"
            ai_label = None

        lines.append(f"### {idx}. {clause_title} — {badge} | {risk_badges[risk_level]}")
        lines.append("")
        lines.append(f"**ID:** `{clause_id}` &nbsp; **Category:** {category}")

        if clause_content:
            lines.append(f"**Clause:** {clause_content}")

        if ai_text and ai_label:
            lines.append("")
            lines.append(f"> **{ai_label}:** {ai_text}")

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)

import io
import logging
from collections import defaultdict
from datetime import datetime, timezone

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

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
BG_BLUE   = "#e8f0fe"   # Document Evidence excerpt background

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
_FINANCIAL_KEYWORDS = [
    "penalty", "deposit", "escalation", "fine", "compensation",
    "damages", "payment schedule", "late fee", "interest",
    "advance", "refund", "forfeit",
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

CATEGORY_ORDER = ["Legal Risk", "Financial Risk", "Operational Risk", "Process Risk", "General"]

_CATEGORY_RISK_LEVEL = {
    "Legal Risk":       ("HIGH",   COLOR_RISK["HIGH"]),
    "Financial Risk":   ("HIGH",   COLOR_RISK["HIGH"]),
    "Operational Risk": ("MEDIUM", COLOR_RISK["MEDIUM"]),
    "Process Risk":     ("LOW",    COLOR_RISK["LOW"]),
    "General":          ("MEDIUM", COLOR_RISK["MEDIUM"]),
}


def _get_risk_info(clause_title: str) -> tuple[str, str]:
    """
    Returns (category, risk_level) based on clause title keyword matching.
      Legal Risk      → HIGH
      Financial Risk  → HIGH
      Operational Risk → MEDIUM
      Process Risk    → LOW
    """
    t = clause_title.lower()
    for kw in _LEGAL_KEYWORDS:
        if kw in t:
            return "Legal Risk", "HIGH"
    for kw in _FINANCIAL_KEYWORDS:
        if kw in t:
            return "Financial Risk", "HIGH"
    for kw in _OPERATIONAL_KEYWORDS:
        if kw in t:
            return "Operational Risk", "MEDIUM"
    for kw in _PROCESS_KEYWORDS:
        if kw in t:
            return "Process Risk", "LOW"
    return "General", "MEDIUM"


# ── ReportLab helpers ────────────────────────────────────────────────────────────

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


def _dark_header_table(data: list, col_widths: list) -> Table:
    """Build a table with a dark header row and alternating body rows."""
    tbl = Table(data, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor(COLOR_DARK)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor(COLOR_RULE)),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return tbl


# ── PDF Report ──────────────────────────────────────────────────────────────────

def generate_pdf_report(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
) -> bytes:
    """
    Generate a color-coded PDF compliance report with:
      - Overall compliance score
      - Red Flag summary (top 3 critical issues)
      - Risk category breakdown table (Legal / Financial / Operational / Process)
      - Jurisdiction & compliance checklist
      - Clause conflict detection results
      - Contract timeline (aggregated key dates/durations)
      - Per-clause details: risk/status badges, obligations, binding strength,
        missing values warning, clause content, AI recommendation
    """
    conflicts = conflicts or []
    jurisdiction_info = jurisdiction_info or {}

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
    warning_style = ParagraphStyle(
        "RWarning", parent=S["Normal"],
        fontSize=8, leading=12, fontName="Helvetica-Oblique",
        textColor=colors.HexColor(COLOR_ORANGE),
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

    # ── Overall compliance score ──────────────────────────────────────────────
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

    # ── Red Flag Summary ──────────────────────────────────────────────────────
    red_flags = [e for e in analysis_summary if e["result"] == "VIOLATION"]
    red_flags += [e for e in analysis_summary if e["result"] == "NOT_FOUND"]
    red_flags = red_flags[:3]

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(COLOR_RULE)))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Critical Issues Requiring Immediate Attention", section_header_style))

    if not red_flags:
        story.append(Paragraph(
            f'<font color="{COLOR_GREEN}">No critical issues found.</font>',
            inner_style,
        ))
    else:
        flag_data = [[
            Paragraph("Clause", tbl_header_style),
            Paragraph("Status", tbl_header_style),
            Paragraph("Issue", tbl_header_style),
        ]]
        for flag in red_flags:
            flag_color = COLOR_RED if flag["result"] == "VIOLATION" else COLOR_ORANGE
            flag_data.append([
                Paragraph(flag["clause_title"], tbl_cell_style),
                Paragraph(
                    f'<font color="{flag_color}"><b>{flag["result"]}</b></font>',
                    tbl_cell_style,
                ),
                Paragraph(flag.get("reason", ""), tbl_cell_style),
            ])
        flag_tbl = Table(
            flag_data,
            colWidths=[PAGE_USABLE_W * 0.30, PAGE_USABLE_W * 0.18, PAGE_USABLE_W * 0.52],
        )
        flag_tbl.setStyle(TableStyle([
            ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor(COLOR_DARK)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor(BG_RED), colors.HexColor("#fff5f5")]),
            ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor(COLOR_RULE)),
            ("TOPPADDING",     (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
            ("LEFTPADDING",    (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
            ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(flag_tbl)
    story.append(Spacer(1, 10))

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
            PAGE_USABLE_W * 0.28,
            PAGE_USABLE_W * 0.18,
            PAGE_USABLE_W * 0.18,
            PAGE_USABLE_W * 0.18,
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

    # ── Jurisdiction & Compliance Checklist ───────────────────────────────────
    if jurisdiction_info:
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(COLOR_RULE)))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Jurisdiction & Compliance Checklist", section_header_style))

        juris     = jurisdiction_info.get("jurisdiction", "Unknown")
        agmt_type = jurisdiction_info.get("agreement_type", "Unknown")
        laws      = ", ".join(jurisdiction_info.get("applicable_laws", []))

        story.append(Paragraph(
            f'<b>Jurisdiction:</b> {juris} &nbsp;&nbsp; <b>Agreement Type:</b> {agmt_type}',
            inner_style,
        ))
        if laws:
            story.append(Paragraph(f'<b>Applicable Laws:</b> {laws}', inner_style))
        story.append(Spacer(1, 4))

        checklist = jurisdiction_info.get("checklist", [])
        if checklist:
            chk_data = [[
                Paragraph("Requirement", tbl_header_style),
                Paragraph("Status",      tbl_header_style),
            ]]
            for item in checklist:
                is_required = item.get("required", False)
                req_label   = "Required" if is_required else "Optional"
                req_color   = COLOR_RED if is_required else COLOR_GREEN
                chk_data.append([
                    Paragraph(item.get("item", ""), tbl_cell_style),
                    Paragraph(
                        f'<font color="{req_color}"><b>{req_label}</b></font>',
                        tbl_cell_style,
                    ),
                ])
            story.append(_dark_header_table(
                chk_data,
                [PAGE_USABLE_W * 0.75, PAGE_USABLE_W * 0.25],
            ))
        story.append(Spacer(1, 10))

    # ── Clause Conflicts ──────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(COLOR_RULE)))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Clause Conflicts", section_header_style))

    if not conflicts:
        story.append(Paragraph(
            f'<font color="{COLOR_GREEN}">No contradictions detected.</font>',
            inner_style,
        ))
    else:
        conf_data = [[
            Paragraph("Clause A",            tbl_header_style),
            Paragraph("Clause B",            tbl_header_style),
            Paragraph("Conflict Description", tbl_header_style),
        ]]
        for conflict in conflicts:
            conf_data.append([
                Paragraph(conflict.get("clause_a", ""),  tbl_cell_style),
                Paragraph(conflict.get("clause_b", ""),  tbl_cell_style),
                Paragraph(conflict.get("conflict", ""),  tbl_cell_style),
            ])
        conf_tbl = Table(
            conf_data,
            colWidths=[PAGE_USABLE_W * 0.27, PAGE_USABLE_W * 0.27, PAGE_USABLE_W * 0.46],
        )
        conf_tbl.setStyle(TableStyle([
            ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor(COLOR_DARK)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor(BG_ORANGE), colors.HexColor("#fffdf5")]),
            ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor(COLOR_RULE)),
            ("TOPPADDING",     (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
            ("LEFTPADDING",    (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
            ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(conf_tbl)
    story.append(Spacer(1, 10))

    # ── Contract Timeline ─────────────────────────────────────────────────────
    timeline_rows = [
        (entry["clause_title"], dt)
        for entry in analysis_summary
        for dt in entry.get("key_dates_durations", [])
        if dt
    ]

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(COLOR_RULE)))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Contract Timeline", section_header_style))

    if not timeline_rows:
        story.append(Paragraph("No key dates or durations identified.", inner_style))
    else:
        tl_data = [[
            Paragraph("Clause",        tbl_header_style),
            Paragraph("Timeline Item", tbl_header_style),
        ]]
        for clause_title, tl_item in timeline_rows:
            tl_data.append([
                Paragraph(clause_title, tbl_cell_style),
                Paragraph(tl_item,      tbl_cell_style),
            ])
        story.append(_dark_header_table(
            tl_data,
            [PAGE_USABLE_W * 0.40, PAGE_USABLE_W * 0.60],
        ))
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
        relevant_text  = entry.get("relevant_text") or ""
        parties        = entry.get("parties_obligated", [])
        binding        = entry.get("binding_strength", "VAGUE")
        missing        = entry.get("missing_values", [])

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

        # Binding strength color
        if binding == "MUST/SHALL":
            binding_color = COLOR_GREEN
        elif binding == "SHOULD":
            binding_color = COLOR_ORANGE
        else:
            binding_color = COLOR_RED

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

        # ── Obligations + Binding Strength ────────────────────────────────────
        parties_str = " / ".join(parties) if parties else "—"
        meta_tbl = Table(
            [[
                Paragraph(
                    f'<font color="{COLOR_GREY}">Obligations: <b>{parties_str}</b></font>',
                    inner_style,
                ),
                Paragraph(
                    f'<font color="{binding_color}"><b>Binding: {binding}</b></font>',
                    risk_badge_style,
                ),
            ]],
            colWidths=[PAGE_USABLE_W * 0.60, PAGE_USABLE_W * 0.40],
        )
        meta_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        story.append(meta_tbl)

        # ── Missing Values warning ────────────────────────────────────────────
        if missing:
            warning_text = "Missing values: " + "; ".join(missing)
            story.append(_bg_row(f"\u26a0  {warning_text}", BG_ORANGE, warning_style))
            story.append(Spacer(1, 2))

        # ── Clause content row ────────────────────────────────────────────────
        if clause_content:
            if clause_bg:
                story.append(_bg_row(f"Clause: {clause_content}", clause_bg, inner_style))
            else:
                story.append(Paragraph(f"Clause: {clause_content}", plain_text_style))
            story.append(Spacer(1, 2))

        # ── Document Evidence row (relevant_text from PDF) ────────────────────
        if relevant_text:
            doc_evidence_style = ParagraphStyle(
                f"RDocEvidence_{idx}", parent=inner_italic_style,
                textColor=colors.HexColor("#1a3a5c"),
            )
            story.append(_bg_row(
                f'<font color="#1a3a5c"><b>Document Evidence:</b></font> {relevant_text}',
                BG_BLUE,
                doc_evidence_style,
            ))
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

def generate_markdown_report(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
) -> str:
    """Generate a Markdown compliance report with all 8 enhancement sections."""
    conflicts = conflicts or []
    jurisdiction_info = jurisdiction_info or {}

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
    ]

    # ── Red Flag Summary ──────────────────────────────────────────────────────
    lines += ["## Critical Issues Requiring Immediate Attention", ""]
    red_flags = [e for e in analysis_summary if e["result"] == "VIOLATION"]
    red_flags += [e for e in analysis_summary if e["result"] == "NOT_FOUND"]
    red_flags = red_flags[:3]
    if not red_flags:
        lines.append("No critical issues found. ✓")
    else:
        lines += [
            "| Clause | Status | Issue |",
            "|:---|:---:|:---|",
        ]
        for flag in red_flags:
            lines.append(f"| {flag['clause_title']} | **{flag['result']}** | {flag.get('reason', '')} |")
    lines += ["", "---", ""]

    # ── Risk Category Breakdown ───────────────────────────────────────────────
    lines += [
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
    lines += ["", "---", ""]

    # ── Jurisdiction & Compliance Checklist ───────────────────────────────────
    lines += ["## Jurisdiction & Compliance Checklist", ""]
    if jurisdiction_info:
        juris     = jurisdiction_info.get("jurisdiction", "Unknown")
        agmt_type = jurisdiction_info.get("agreement_type", "Unknown")
        laws      = ", ".join(jurisdiction_info.get("applicable_laws", []))
        lines.append(f"**Jurisdiction:** {juris} &nbsp; **Agreement Type:** {agmt_type}")
        if laws:
            lines.append(f"**Applicable Laws:** {laws}")
        lines.append("")
        checklist = jurisdiction_info.get("checklist", [])
        if checklist:
            lines += [
                "| Requirement | Status |",
                "|:---|:---:|",
            ]
            for item in checklist:
                req_label = "**Required**" if item.get("required") else "Optional"
                lines.append(f"| {item.get('item', '')} | {req_label} |")
    else:
        lines.append("Jurisdiction information not available.")
    lines += ["", "---", ""]

    # ── Clause Conflicts ──────────────────────────────────────────────────────
    lines += ["## Clause Conflicts", ""]
    if not conflicts:
        lines.append("No contradictions detected. ✓")
    else:
        lines += [
            "| Clause A | Clause B | Conflict Description |",
            "|:---|:---|:---|",
        ]
        for c in conflicts:
            lines.append(
                f"| {c.get('clause_a', '')} | {c.get('clause_b', '')} | {c.get('conflict', '')} |"
            )
    lines += ["", "---", ""]

    # ── Contract Timeline ─────────────────────────────────────────────────────
    lines += ["## Contract Timeline", ""]
    timeline_rows = [
        (entry["clause_title"], dt)
        for entry in analysis_summary
        for dt in entry.get("key_dates_durations", [])
        if dt
    ]
    if not timeline_rows:
        lines.append("No key dates or durations identified.")
    else:
        lines += [
            "| Clause | Timeline Item |",
            "|:---|:---|",
        ]
        for clause_title, tl_item in timeline_rows:
            lines.append(f"| {clause_title} | {tl_item} |")
    lines += ["", "---", "", "## Clause Details", ""]

    # ── Clause Details ────────────────────────────────────────────────────────
    for idx, entry in enumerate(analysis_summary, start=1):
        result         = entry.get("result", "NOT_FOUND")
        clause_title   = entry.get("clause_title", "")
        clause_id      = entry.get("clause_id", "")
        clause_content = entry.get("clause_content", "")
        ai_text        = entry.get("ai_added_text", "")
        relevant_text  = entry.get("relevant_text") or ""
        parties        = entry.get("parties_obligated", [])
        binding        = entry.get("binding_strength", "VAGUE")
        missing        = entry.get("missing_values", [])
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

        parties_str = " / ".join(parties) if parties else "—"
        lines.append(f"**Obligations:** {parties_str} &nbsp; **Binding:** `{binding}`")

        if missing:
            lines.append(f"> ⚠️ **Missing values:** {'; '.join(missing)}")

        if clause_content:
            lines.append(f"**Clause:** {clause_content}")

        if relevant_text:
            lines.append(f"> 📄 **Document Evidence:** *{relevant_text}*")

        if ai_text and ai_label:
            lines.append("")
            lines.append(f"> **{ai_label}:** {ai_text}")

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ── DOCX helpers ─────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> RGBColor:
    """Convert '#RRGGBB' to docx RGBColor."""
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _set_cell_shading(cell, hex_color: str):
    """Set background shading on a docx table cell."""
    from docx.oxml.ns import qn
    from lxml import etree
    shading = etree.SubElement(cell._element.get_or_add_tcPr(), qn("w:shd"))
    shading.set(qn("w:fill"), hex_color.lstrip("#"))
    shading.set(qn("w:val"), "clear")


def _set_paragraph_shading(paragraph, hex_color: str):
    """Set background shading on a docx paragraph."""
    from docx.oxml.ns import qn
    from lxml import etree
    pPr = paragraph._element.get_or_add_pPr()
    shd = etree.SubElement(pPr, qn("w:shd"))
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color.lstrip("#"))


def _docx_dark_header_table(doc, headers: list, rows: list, col_widths_pct: list = None):
    """
    Build a docx table with a dark header row.
    headers: list of str
    rows: list of list of str (or list of (str, color_hex) tuples for colored cells)
    col_widths_pct: optional list of fractional widths (must sum to 1.0)
    Returns the table object.
    """
    tbl = doc.add_table(rows=1, cols=len(headers))
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, hdr in enumerate(headers):
        cell = tbl.rows[0].cells[i]
        cell.text = hdr
        _set_cell_shading(cell, COLOR_DARK)
        for p in cell.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                r.bold = True
                r.font.color.rgb = RGBColor(255, 255, 255)
                r.font.size = Pt(9)
    for row_data in rows:
        row = tbl.add_row()
        for i, cell_data in enumerate(row_data):
            if isinstance(cell_data, tuple):
                text, color_hex = cell_data
                cell = row.cells[i]
                cell.text = ""
                p = cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = p.add_run(text)
                r.bold = True
                r.font.size = Pt(9)
                r.font.color.rgb = _hex_to_rgb(color_hex)
            else:
                row.cells[i].text = str(cell_data)
                for p in row.cells[i].paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9)
    return tbl


# ── DOCX Report ──────────────────────────────────────────────────────────────────

def generate_docx_report(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
) -> bytes:
    """
    Generate a DOCX compliance report with all 8 enhancement sections.
    Includes the 'reason' field (not shown in PDF).
    """
    conflicts = conflicts or []
    jurisdiction_info = jurisdiction_info or {}

    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    # ── Pre-compute stats ────────────────────────────────────────────────────
    match_count     = sum(1 for c in analysis_summary if c["result"] == "MATCH")
    violation_count = sum(1 for c in analysis_summary if c["result"] == "VIOLATION")
    not_found_count = sum(1 for c in analysis_summary if c["result"] == "NOT_FOUND")
    total           = len(analysis_summary)
    compliance_score = round((match_count / total) * 100) if total else 0
    generated_at     = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    if compliance_score >= 80:
        score_status = "Compliant"
        score_color  = COLOR_SCORE_HIGH
    elif compliance_score >= 50:
        score_status = "Needs Attention"
        score_color  = COLOR_SCORE_MED
    else:
        score_status = "Critical"
        score_color  = COLOR_SCORE_LOW

    # Category stats
    category_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    # ── Title ────────────────────────────────────────────────────────────────
    title = doc.add_heading("CLAUSE COMPLIANCE ANALYSIS REPORT", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run(f"Generated on {generated_at}")
    run.font.size = Pt(9)
    run.font.color.rgb = _hex_to_rgb(COLOR_GREY)

    doc.add_paragraph("_" * 80)

    # ── Summary table ────────────────────────────────────────────────────────
    doc.add_heading("Summary", level=1)

    summary_tbl = doc.add_table(rows=2, cols=4)
    summary_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["Total", "Match", "Violation", "Not Found"]
    values  = [str(total), str(match_count), str(violation_count), str(not_found_count)]
    val_colors = [None, COLOR_GREEN, COLOR_RED, COLOR_ORANGE]

    for i, header in enumerate(headers):
        cell = summary_tbl.rows[0].cells[i]
        cell.text = header
        for p in cell.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(9)

    for i, val in enumerate(values):
        cell = summary_tbl.rows[1].cells[i]
        cell.text = val
        for p in cell.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                r.font.size = Pt(10)
                r.bold = True
                if val_colors[i]:
                    r.font.color.rgb = _hex_to_rgb(val_colors[i])

    # Score line
    score_para = doc.add_paragraph()
    score_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = score_para.add_run(f"\nOverall Compliance Score: {compliance_score}% — {score_status}")
    run.bold = True
    run.font.size = Pt(14)
    run.font.color.rgb = _hex_to_rgb(score_color)

    # ── Red Flag Summary ──────────────────────────────────────────────────────
    doc.add_heading("Critical Issues Requiring Immediate Attention", level=1)

    red_flags = [e for e in analysis_summary if e["result"] == "VIOLATION"]
    red_flags += [e for e in analysis_summary if e["result"] == "NOT_FOUND"]
    red_flags = red_flags[:3]

    if not red_flags:
        p = doc.add_paragraph()
        run = p.add_run("No critical issues found.")
        run.font.color.rgb = _hex_to_rgb(COLOR_GREEN)
        run.font.size = Pt(10)
    else:
        flag_rows = []
        for flag in red_flags:
            flag_color = COLOR_RED if flag["result"] == "VIOLATION" else COLOR_ORANGE
            flag_rows.append([
                flag["clause_title"],
                (flag["result"], flag_color),
                flag.get("reason", ""),
            ])
        _docx_dark_header_table(doc, ["Clause", "Status", "Issue"], flag_rows)

    # ── Risk Category Breakdown ───────────────────────────────────────────────
    doc.add_heading("Risk Category Breakdown", level=1)

    cat_tbl = doc.add_table(rows=1, cols=5)
    cat_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cat_headers = ["Category", "Risk Level", "Total", "Compliant", "Issues"]
    for i, hdr in enumerate(cat_headers):
        cell = cat_tbl.rows[0].cells[i]
        cell.text = hdr
        _set_cell_shading(cell, COLOR_DARK)
        for p in cell.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(9)
                r.font.color.rgb = RGBColor(255, 255, 255)

    for cat in CATEGORY_ORDER:
        if cat not in category_stats:
            continue
        stats = category_stats[cat]
        risk_lv, risk_col = _CATEGORY_RISK_LEVEL[cat]
        row = cat_tbl.add_row()
        row.cells[0].text = cat
        row.cells[2].text = str(stats["total"])
        row.cells[3].text = str(stats["compliant"])
        row.cells[4].text = str(stats["issues"])

        risk_cell = row.cells[1]
        risk_cell.text = ""
        rp = risk_cell.paragraphs[0]
        rp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rr = rp.add_run(risk_lv)
        rr.bold = True
        rr.font.size = Pt(9)
        rr.font.color.rgb = _hex_to_rgb(risk_col)

        for i in [0, 2, 3, 4]:
            for p in row.cells[i].paragraphs:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for r in p.runs:
                    r.font.size = Pt(9)

    # ── Jurisdiction & Compliance Checklist ───────────────────────────────────
    doc.add_heading("Jurisdiction & Compliance Checklist", level=1)

    if jurisdiction_info:
        juris_para = doc.add_paragraph()
        run = juris_para.add_run("Jurisdiction: ")
        run.bold = True
        run.font.size = Pt(10)
        run = juris_para.add_run(jurisdiction_info.get("jurisdiction", "Unknown"))
        run.font.size = Pt(10)
        run = juris_para.add_run("    Agreement Type: ")
        run.bold = True
        run.font.size = Pt(10)
        run = juris_para.add_run(jurisdiction_info.get("agreement_type", "Unknown"))
        run.font.size = Pt(10)

        laws = jurisdiction_info.get("applicable_laws", [])
        if laws:
            laws_para = doc.add_paragraph()
            run = laws_para.add_run("Applicable Laws: ")
            run.bold = True
            run.font.size = Pt(10)
            run = laws_para.add_run(", ".join(laws))
            run.font.size = Pt(10)

        checklist = jurisdiction_info.get("checklist", [])
        if checklist:
            chk_rows = []
            for item in checklist:
                is_req = item.get("required", False)
                req_label = "Required" if is_req else "Optional"
                req_color = COLOR_RED if is_req else COLOR_GREEN
                chk_rows.append([item.get("item", ""), (req_label, req_color)])
            _docx_dark_header_table(doc, ["Requirement", "Status"], chk_rows)
    else:
        p = doc.add_paragraph()
        p.add_run("Jurisdiction information not available.").font.size = Pt(10)

    # ── Clause Conflicts ──────────────────────────────────────────────────────
    doc.add_heading("Clause Conflicts", level=1)

    if not conflicts:
        p = doc.add_paragraph()
        run = p.add_run("No contradictions detected.")
        run.font.color.rgb = _hex_to_rgb(COLOR_GREEN)
        run.font.size = Pt(10)
    else:
        conf_rows = [
            [c.get("clause_a", ""), c.get("clause_b", ""), c.get("conflict", "")]
            for c in conflicts
        ]
        _docx_dark_header_table(doc, ["Clause A", "Clause B", "Conflict Description"], conf_rows)

    # ── Contract Timeline ─────────────────────────────────────────────────────
    doc.add_heading("Contract Timeline", level=1)

    timeline_rows = [
        (entry["clause_title"], dt)
        for entry in analysis_summary
        for dt in entry.get("key_dates_durations", [])
        if dt
    ]
    if not timeline_rows:
        doc.add_paragraph("No key dates or durations identified.")
    else:
        tl_rows = [[ct, ti] for ct, ti in timeline_rows]
        _docx_dark_header_table(doc, ["Clause", "Timeline Item"], tl_rows)

    # ── Clause Details ───────────────────────────────────────────────────────
    doc.add_heading("Clause Details", level=1)

    for idx, entry in enumerate(analysis_summary, start=1):
        result         = entry.get("result", "NOT_FOUND")
        clause_title   = entry.get("clause_title", "")
        clause_id      = entry.get("clause_id", "")
        clause_content = entry.get("clause_content", "")
        ai_text        = entry.get("ai_added_text", "")
        reason         = entry.get("reason", "")
        relevant_text  = entry.get("relevant_text") or ""
        parties        = entry.get("parties_obligated", [])
        binding        = entry.get("binding_strength", "VAGUE")
        missing        = entry.get("missing_values", [])

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

        # Binding strength color
        if binding == "MUST/SHALL":
            binding_color = COLOR_GREEN
        elif binding == "SHOULD":
            binding_color = COLOR_ORANGE
        else:
            binding_color = COLOR_RED

        # Clause heading with risk + status badges
        heading_para = doc.add_paragraph()
        run = heading_para.add_run(f"{idx}. {clause_title}  ")
        run.bold = True
        run.font.size = Pt(11)

        run = heading_para.add_run(f"[{risk_level} RISK]  ")
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = _hex_to_rgb(risk_color)

        run = heading_para.add_run(f"[{status_label}]")
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = _hex_to_rgb(status_color)

        # ID + Category
        id_para = doc.add_paragraph()
        run = id_para.add_run(f"ID: {clause_id}    Category: {category}")
        run.font.size = Pt(9)
        run.font.color.rgb = _hex_to_rgb(COLOR_GREY)

        # Obligations + Binding Strength
        parties_str = " / ".join(parties) if parties else "—"
        meta_para = doc.add_paragraph()
        run = meta_para.add_run("Obligations: ")
        run.bold = True
        run.font.size = Pt(9)
        run = meta_para.add_run(parties_str + "    ")
        run.font.size = Pt(9)
        run = meta_para.add_run("Binding: ")
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = _hex_to_rgb(binding_color)
        run = meta_para.add_run(binding)
        run.font.size = Pt(9)
        run.bold = True
        run.font.color.rgb = _hex_to_rgb(binding_color)

        # Missing Values warning
        if missing:
            warn_para = doc.add_paragraph()
            run = warn_para.add_run("\u26a0  Missing values: " + "; ".join(missing))
            run.font.size = Pt(9)
            run.italic = True
            run.font.color.rgb = _hex_to_rgb(COLOR_ORANGE)
            _set_paragraph_shading(warn_para, BG_ORANGE)

        # Clause content — red background for VIOLATION
        if clause_content:
            cp = doc.add_paragraph()
            run = cp.add_run("Clause: ")
            run.bold = True
            run.font.size = Pt(10)
            run = cp.add_run(clause_content)
            run.font.size = Pt(10)
            if clause_bg:
                _set_paragraph_shading(cp, clause_bg)

        # REASON field — DOCX only, not shown in PDF
        if reason:
            rp = doc.add_paragraph()
            run = rp.add_run("Reason: ")
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(COLOR_DARK)
            run = rp.add_run(reason)
            run.font.size = Pt(10)
            run.italic = True

        # Document Evidence — actual text extracted from the PDF
        if relevant_text:
            ep = doc.add_paragraph()
            run = ep.add_run("Document Evidence: ")
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb("#1a3a5c")
            run = ep.add_run(relevant_text)
            run.font.size = Pt(9)
            run.italic = True
            run.font.color.rgb = _hex_to_rgb("#1a3a5c")
            _set_paragraph_shading(ep, BG_BLUE)

        # AI recommendation — green background for VIOLATION, orange for NOT_FOUND
        if ai_text and ai_label:
            ap = doc.add_paragraph()
            run = ap.add_run(f"{ai_label}: ")
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(status_color)
            run = ap.add_run(ai_text)
            run.font.size = Pt(10)
            run.italic = True
            if ai_bg:
                _set_paragraph_shading(ap, ai_bg)

        doc.add_paragraph("_" * 80)

    # Save to bytes
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()

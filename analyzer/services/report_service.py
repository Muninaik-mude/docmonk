import io
import logging
import re
from collections import defaultdict

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
COLOR_BLUE   = "#0d6efd"
COLOR_BLACK  = "#212529"
COLOR_GREY   = "#6c757d"
COLOR_DARK   = "#1a1a2e"
COLOR_RULE   = "#dee2e6"

# Status background colors
BG_RED    = "#fde8e8"
BG_GREEN  = "#d4edda"
BG_ORANGE = "#fff3cd"
BG_BLUE   = "#e8f0fe"

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
    """Returns (category, risk_level) based on clause title keyword matching."""
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


# ══════════════════════════════════════════════════════════════════════════════════
#  PDF REPORT (Redline-style) — full PDF text + per-clause AI suggestions
# ══════════════════════════════════════════════════════════════════════════════════

def _pdf_agreement_block(story, agreement_meta: dict, styles: dict):
    """Render the Agreement Details block into a ReportLab story list."""
    if not agreement_meta:
        return

    section_header_style = styles["section_header"]
    inner_style          = styles["inner"]
    tbl_header_style     = styles["tbl_header"]
    tbl_cell_style       = styles["tbl_cell"]

    story.append(Paragraph("<b>Agreement Details</b>", section_header_style))
    story.append(Spacer(1, 4))

    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}
    parties   = agreement_meta.get("parties") or {}
    prop      = agreement_meta.get("property") or {}

    # Agreement type + date/city/state
    info_rows = []
    if agmt_type:
        info_rows.append(("Agreement Type", agmt_type))
    if agmt_det.get("agreement_date"):
        info_rows.append(("Agreement Date", agmt_det["agreement_date"]))
    if agmt_det.get("city") or agmt_det.get("state"):
        place = ", ".join(v for v in [agmt_det.get("city"), agmt_det.get("state")] if v)
        info_rows.append(("Location", place))

    if info_rows:
        tbl_data = [[Paragraph("Field", tbl_header_style), Paragraph("Details", tbl_header_style)]]
        for label, value in info_rows:
            tbl_data.append([Paragraph(label, tbl_cell_style), Paragraph(value, tbl_cell_style)])
        story.append(_dark_header_table(tbl_data, [PAGE_USABLE_W * 0.30, PAGE_USABLE_W * 0.70]))
        story.append(Spacer(1, 6))

    # Parties
    landlord = parties.get("landlord") or {}
    tenant   = parties.get("tenant") or {}
    party_rows = []
    if landlord.get("name"):
        party_rows.append(("Landlord Name",    landlord["name"]))
    if landlord.get("address"):
        party_rows.append(("Landlord Address", landlord["address"]))
    if landlord.get("contact"):
        party_rows.append(("Landlord Contact", landlord["contact"]))

    tenant_name = tenant.get("company_name") or tenant.get("name", "")
    if tenant_name:
        party_rows.append(("Tenant",           tenant_name))
    if tenant.get("authorized_signatory"):
        party_rows.append(("Authorized By",    tenant["authorized_signatory"]))
    if tenant.get("address"):
        party_rows.append(("Tenant Address",   tenant["address"]))
    if tenant.get("contact"):
        party_rows.append(("Tenant Contact",   tenant["contact"]))

    if party_rows:
        story.append(Paragraph("<b>Parties</b>", section_header_style))
        story.append(Spacer(1, 2))
        tbl_data = [[Paragraph("Field", tbl_header_style), Paragraph("Details", tbl_header_style)]]
        for label, value in party_rows:
            tbl_data.append([Paragraph(label, tbl_cell_style), Paragraph(value, tbl_cell_style)])
        story.append(_dark_header_table(tbl_data, [PAGE_USABLE_W * 0.30, PAGE_USABLE_W * 0.70]))
        story.append(Spacer(1, 6))

    # Property
    prop_rows = []
    if prop.get("type"):
        prop_rows.append(("Property Type",    prop["type"]))
    if prop.get("area_sqft"):
        prop_rows.append(("Area (sq ft)",     str(prop["area_sqft"])))
    if prop.get("address"):
        prop_rows.append(("Property Address", prop["address"]))

    if prop_rows:
        story.append(Paragraph("<b>Property</b>", section_header_style))
        story.append(Spacer(1, 2))
        tbl_data = [[Paragraph("Field", tbl_header_style), Paragraph("Details", tbl_header_style)]]
        for label, value in prop_rows:
            tbl_data.append([Paragraph(label, tbl_cell_style), Paragraph(value, tbl_cell_style)])
        story.append(_dark_header_table(tbl_data, [PAGE_USABLE_W * 0.30, PAGE_USABLE_W * 0.70]))
        story.append(Spacer(1, 6))

    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor(COLOR_DARK)))
    story.append(Spacer(1, 10))


def _build_inline_segments(full_text: str, analysis_summary: list) -> list:
    """
    Merge the full document text with per-clause AI answers inline.

    Segment types returned:
      {"type": "normal",       "text": str}  — unmatched line, no colour
      {"type": "violation",    "text": str}  — VIOLATION original text  → red bg + strikethrough
      {"type": "ai",           "text": str}  — AI correction/addition   → green bg
      {"type": "partial",      "text": str}  — PARTIALLY_SATISFIED text → orange bg
      {"type": "match",        "text": str}  — MATCH relevant_text      → no colour
      {"type": "not_found_ai", "text": str}  — NOT_FOUND AI (no rt)    → blue bg, appended at end
    """
    violation_map  = {}   # text → (ai_text, reason)  VIOLATION
    partial_map    = {}   # text → (ai_text, reason)  PARTIALLY_SATISFIED
    match_set      = []   # list of relevant/clause text keys (MATCH)
    not_found_list = []   # (ai_text, reason) for NOT_FOUND

    for entry in analysis_summary:
        result       = entry.get("result", "")
        rt           = (entry.get("relevant_text") or "").strip()
        cc           = (entry.get("clause_value", "")).strip()
        clause_title = (entry.get("clause_title", "") or "").strip()
        ai_text      = entry.get("ai_added_text", "") or ""
        reason       = entry.get("reason", "") or ""

        if result == "VIOLATION":
            # Primary key: relevant_text (verbatim doc text).  Fallback: clause_title.
            key = rt or clause_title
            if key:
                violation_map[key] = (ai_text, reason)
            # Always also index by title so the clause heading line is guaranteed to match
            # even when rt is long multi-sentence text that doesn't fit a single line.
            if clause_title and clause_title not in violation_map:
                violation_map[clause_title] = (ai_text, reason)

        elif result == "PARTIALLY_SATISFIED":
            key = rt or clause_title
            if key:
                partial_map[key] = (ai_text, reason)
            if clause_title and clause_title not in partial_map:
                partial_map[clause_title] = (ai_text, reason)

        elif result == "NOT_FOUND":
            if ai_text:
                not_found_list.append((ai_text, reason))

        elif result == "MATCH":
            key = rt or cc
            if key:
                match_set.append(key)

    # Skip lines that are only junk: digits, dots, dashes, underscores,
    # bullet characters (•·◦◉), whitespace — e.g. "1.", "•", "...", "___"
    _junk = re.compile(r'^[\d\.\-_\s\u2022\u00b7\u25cf\u25cb\u25cc\u25e6\*]+$')

    # Strips leading "16. " / "*   16. " / "16. Registration: " style prefixes so that
    # a line like "*   1\. Lease Term: This Agreement..." still matches a key like
    # "Lease Term" or "This Agreement..."
    _num_prefix = re.compile(r'^[\*\s]*\d+[\.\\)]\s*(?:[A-Z][A-Za-z ,&]+:\s*)?')

    # Converts markdown bullet markers to bullet character:
    #   "* some item"  →  "• some item"
    _md_bullet   = re.compile(r'^\*\s+')
    _md_heading  = re.compile(r'^#+\s*')
    _table_line  = re.compile(r'^\|.+\|$')
    _table_sep   = re.compile(r'^\|[\s\-:|\+]+\|$')

    def _clean_display(s: str) -> str:
        """Convert markdown artifacts to display-friendly format."""
        # Bullet lines get indentation via non-breaking spaces for PDF paragraphs
        s = _md_bullet.sub('&nbsp;&nbsp;&nbsp;&nbsp;\u2022 ', s).replace('\\.', '.')
        # Convert markdown headings → bold: "# TITLE" → "<b>TITLE</b>"
        if s.startswith('#'):
            s = '<b>' + _md_heading.sub('', s) + '</b>'
        return s

    def _line_matches(s: str, key: str) -> bool:
        """Return True if document line s matches violation/partial/match key."""
        if not key or not s:
            return False
        # Case-sensitive substring check
        if key in s or s in key:
            return True
        # Case-insensitive substring check
        s_lower, key_lower = s.lower(), key.lower()
        if key_lower in s_lower or s_lower in key_lower:
            return True
        # Try again after stripping leading bullet/section-number prefix
        clean = _num_prefix.sub('', s).strip()
        if clean and len(clean) > 6 and (clean in key or key in clean):
            return True
        if clean and len(clean) > 6 and (clean.lower() in key_lower or key_lower in clean.lower()):
            return True
        return False

    segments = []

    # Accumulators: consecutive lines from the same block are joined into ONE
    # paragraph so they share a single background row and AI shows only once.
    pending_v_key    = None
    pending_v_lines  = []
    pending_v_ai     = None
    pending_v_reason = ""

    pending_p_key    = None
    pending_p_lines  = []
    pending_p_ai     = None
    pending_p_reason = ""

    pending_m_key    = None
    pending_m_lines  = []

    def _flush_violation():
        nonlocal pending_v_key, pending_v_lines, pending_v_ai, pending_v_reason
        if pending_v_lines:
            display = "<br>".join(_clean_display(l) for l in pending_v_lines)
            segments.append({"type": "violation", "text": display, "reason": pending_v_reason})
            if pending_v_ai:
                segments.append({"type": "ai", "text": pending_v_ai})
        pending_v_key    = None
        pending_v_lines  = []
        pending_v_ai     = None
        pending_v_reason = ""

    def _flush_partial():
        nonlocal pending_p_key, pending_p_lines, pending_p_ai, pending_p_reason
        if pending_p_lines:
            display = "<br>".join(_clean_display(l) for l in pending_p_lines)
            segments.append({"type": "partial", "text": display, "reason": pending_p_reason})
            if pending_p_ai:
                segments.append({"type": "ai", "text": pending_p_ai})
        pending_p_key    = None
        pending_p_lines  = []
        pending_p_ai     = None
        pending_p_reason = ""

    def _flush_match():
        nonlocal pending_m_key, pending_m_lines
        if pending_m_lines:
            display = "<br>".join(_clean_display(l) for l in pending_m_lines)
            segments.append({"type": "match", "text": display})
        pending_m_key   = None
        pending_m_lines = []

    pending_table_lines = []

    def _flush_table():
        nonlocal pending_table_lines
        if not pending_table_lines:
            return
        html_rows = []
        is_first_data = True
        for tl in pending_table_lines:
            if _table_sep.match(tl):
                continue
            cells = [c.strip() for c in tl.strip('|').split('|')]
            if is_first_data:
                html_cells = "".join(f"<th>{c}</th>" for c in cells)
                html_rows.append(f"<tr>{html_cells}</tr>")
                is_first_data = False
            else:
                html_cells = "".join(f"<td>{c}</td>" for c in cells)
                html_rows.append(f"<tr>{html_cells}</tr>")
        if html_rows:
            table_html = '<table class="doc-table">' + "".join(html_rows) + "</table>"
            segments.append({"type": "table", "text": table_html})
        pending_table_lines = []

    for line in (full_text or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            _flush_violation()
            _flush_partial()
            _flush_match()
            _flush_table()
            if segments and segments[-1]["type"] != "blank":
                segments.append({"type": "blank", "text": ""})
            continue
        if _junk.match(stripped):
            continue

        # ── 1. Violation check ─────────────────────────────────────────────
        matched_v_key    = None
        matched_v_ai     = None
        matched_v_reason = ""
        for key, (ai_text, reason) in violation_map.items():
            if _line_matches(stripped, key):
                matched_v_key    = key
                matched_v_ai     = ai_text
                matched_v_reason = reason
                break

        if matched_v_key is not None:
            _flush_partial()
            _flush_match()
            if matched_v_key == pending_v_key:
                pending_v_lines.append(stripped)
            else:
                _flush_violation()
                pending_v_key    = matched_v_key
                pending_v_lines  = [stripped]
                pending_v_ai     = matched_v_ai
                pending_v_reason = matched_v_reason
            continue

        _flush_violation()
        _flush_match()

        # ── 2. Partial (PARTIALLY_SATISFIED) check ────────────────────────
        matched_p_key    = None
        matched_p_ai     = None
        matched_p_reason = ""
        for key, (ai_text, reason) in partial_map.items():
            if _line_matches(stripped, key):
                matched_p_key    = key
                matched_p_ai     = ai_text
                matched_p_reason = reason
                break

        if matched_p_key is not None:
            _flush_match()
            if matched_p_key == pending_p_key:
                pending_p_lines.append(stripped)
            else:
                _flush_partial()
                pending_p_key    = matched_p_key
                pending_p_lines  = [stripped]
                pending_p_ai     = matched_p_ai
                pending_p_reason = matched_p_reason
            continue

        _flush_partial()

        # ── 3. Match check ─────────────────────────────────────────────────
        matched_m_key = None
        for key in match_set:
            if _line_matches(stripped, key):
                matched_m_key = key
                break

        if matched_m_key is not None:
            if matched_m_key == pending_m_key:
                pending_m_lines.append(stripped)
            else:
                _flush_match()
                pending_m_key   = matched_m_key
                pending_m_lines = [stripped]
            continue

        _flush_match()

        # ── 4. Table check ────────────────────────────────────────────────
        if _table_line.match(stripped):
            pending_table_lines.append(stripped)
            continue
        _flush_table()

        # ── 5. Heading / Normal ────────────────────────────────────────────
        if stripped.startswith('#'):
            # Markdown heading → H1 bold segment (strip # chars, keep plain text)
            heading_text = _md_heading.sub('', stripped).strip()
            segments.append({"type": "heading", "text": heading_text})
        elif _md_bullet.match(stripped):
            # Markdown bullet → dedicated indented bullet segment
            bullet_text = _md_bullet.sub('\u2022 ', stripped).replace('\\.', '.')
            segments.append({"type": "bullet", "text": bullet_text})
        else:
            segments.append({"type": "normal", "text": _clean_display(stripped)})

    _flush_violation()
    _flush_partial()
    _flush_match()
    _flush_table()

    for ai_text, reason in not_found_list:
        segments.append({"type": "not_found_ai", "text": ai_text, "reason": reason})

    return segments


def generate_pdf_report(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> bytes:
    """
    Generate a redline-style PDF report:
      - Agreement details block
      - Original document rendered inline:
          violation lines: red bg + strikethrough + − icon
          AI answer:       green bg + + icon immediately after
          normal lines:    plain text
          NOT_FOUND:       green/orange addition appended at end
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

    inner_style = ParagraphStyle(
        "RInner", parent=S["Normal"],
        fontSize=9, leading=14,
        textColor=colors.HexColor(COLOR_BLACK),
    )
    violation_style = ParagraphStyle(
        "RViolation", parent=S["Normal"],
        fontSize=9, leading=14,
        textColor=colors.HexColor("#6b1015"),
    )
    ai_style = ParagraphStyle(
        "RAi", parent=S["Normal"],
        fontSize=9, leading=14,
        textColor=colors.HexColor("#155724"),
    )
    partial_style = ParagraphStyle(
        "RPartial", parent=S["Normal"],
        fontSize=9, leading=14,
        textColor=colors.HexColor("#856404"),
    )
    nf_style = ParagraphStyle(
        "RNf", parent=S["Normal"],
        fontSize=9, leading=14,
        textColor=colors.HexColor("#0a3577"),
    )
    heading_style = ParagraphStyle(
        "RHeading", parent=S["Normal"],
        fontSize=14, fontName="Helvetica-Bold",
        textColor=colors.HexColor(COLOR_DARK),
        spaceBefore=10, spaceAfter=4,
    )
    bullet_style = ParagraphStyle(
        "RBullet", parent=S["Normal"],
        fontSize=9, leading=14,
        textColor=colors.HexColor(COLOR_BLACK),
        leftIndent=16,
        firstLineIndent=0,
    )

    story = []

    # Inline redline document — no title, no header, no agreement block
    segments = _build_inline_segments(full_text, analysis_summary)

    for seg in segments:
        stype = seg["type"]
        text  = seg["text"]

        if stype == "violation":
            # Red bg + strikethrough
            story.append(_bg_row(
                f'<font color="{COLOR_RED}"><b>\u2212</b></font> <strike>{text}</strike>',
                BG_RED, violation_style,
            ))
            story.append(Spacer(1, 1))

        elif stype == "ai":
            # Green bg — AI correction/addition
            story.append(_bg_row(
                f'<font color="{COLOR_GREEN}"><b>+</b></font> {text}',
                BG_GREEN, ai_style,
            ))
            story.append(Spacer(1, 4))

        elif stype == "partial":
            # Orange bg — PARTIALLY_SATISFIED original text
            story.append(_bg_row(
                f'<font color="{COLOR_ORANGE}"><b>\u2212</b></font> <strike>{text}</strike>',
                BG_ORANGE, partial_style,
            ))
            story.append(Spacer(1, 1))

        elif stype == "not_found_ai":
            # Blue bg — NOT_FOUND AI suggestion
            story.append(_bg_row(
                f'<font color="{COLOR_BLUE}"><b>+</b></font> {text}',
                BG_BLUE, nf_style,
            ))
            story.append(Spacer(1, 4))

        elif stype == "match":
            # No color — satisfied/match clause (plain text)
            story.append(Paragraph(text, inner_style))
            story.append(Spacer(1, 1))

        elif stype == "heading":
            # H1 bold heading — large dark title
            story.append(Paragraph(text, heading_style))
            story.append(Spacer(1, 2))

        elif stype == "bullet":
            # Indented bullet point
            story.append(Paragraph(text, bullet_style))
            story.append(Spacer(1, 1))

        else:  # normal
            story.append(Paragraph(text, inner_style))
            story.append(Spacer(1, 1))

    doc.build(story)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════════
#  PDF SUMMARY (Analytics) — score, tables, jurisdiction, timeline
#  (No Clause Conflicts section)
# ══════════════════════════════════════════════════════════════════════════════════

def generate_pdf_summary(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> bytes:
    """
    Generate a PDF analytics summary with:
      - Agreement details block
      - Overall compliance score
      - Critical Issues (Red Flag) table
      - Risk Category Breakdown table
      - Jurisdiction & Compliance Checklist
      - Contract Timeline
    """
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
    inner_style = ParagraphStyle(
        "RInner", parent=S["Normal"],
        fontSize=9, leading=13,
        textColor=colors.HexColor(COLOR_BLACK),
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

    _styles = {
        "section_header": section_header_style,
        "inner": inner_style,
        "tbl_header": tbl_header_style,
        "tbl_cell": tbl_cell_style,
    }

    story = []

    # ── Pre-compute stats ─────────────────────────────────────────────────────
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

    category_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    # ── Clause counts summary line ────────────────────────────────────────────
    story.append(Paragraph(
        f'<b>Clause Summary</b> &nbsp;&nbsp; Total: <b>{total}</b> &nbsp;|&nbsp; '
        f'<font color="{COLOR_GREEN}">Match: <b>{match_count}</b></font> &nbsp;|&nbsp; '
        f'<font color="{COLOR_RED}">Violation: <b>{violation_count}</b></font> &nbsp;|&nbsp; '
        f'<font color="{COLOR_ORANGE}">Partial: <b>{partial_count}</b></font> &nbsp;|&nbsp; '
        f'<font color="{COLOR_BLUE}">Not Found: <b>{not_found_count}</b></font>',
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

    doc.build(story)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════════
#  MARKDOWN REPORT (Redline-style) — no risk titles, full text + AI suggestions
# ══════════════════════════════════════════════════════════════════════════════════

def _md_agreement_block(agreement_meta: dict) -> list:
    """Return Markdown lines for the Agreement Details block."""
    if not agreement_meta:
        return []

    lines = ["## Agreement Details", ""]

    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}
    parties   = agreement_meta.get("parties") or {}
    prop      = agreement_meta.get("property") or {}

    if agmt_type:
        lines.append(f"**Agreement Type:** {agmt_type}")
    if agmt_det.get("agreement_date"):
        lines.append(f"**Agreement Date:** {agmt_det['agreement_date']}")
    place = ", ".join(v for v in [agmt_det.get("city"), agmt_det.get("state")] if v)
    if place:
        lines.append(f"**Location:** {place}")

    landlord = parties.get("landlord") or {}
    tenant   = parties.get("tenant") or {}
    if any(landlord.values()) or any(tenant.values()):
        lines += ["", "### Parties", ""]
        if landlord.get("name"):
            lines.append(f"**Landlord:** {landlord['name']}")
        if landlord.get("address"):
            lines.append(f"**Landlord Address:** {landlord['address']}")
        if landlord.get("contact"):
            lines.append(f"**Landlord Contact:** {landlord['contact']}")
        tenant_name = tenant.get("company_name") or tenant.get("name", "")
        if tenant_name:
            lines.append(f"**Tenant:** {tenant_name}")
        if tenant.get("authorized_signatory"):
            lines.append(f"**Authorized By:** {tenant['authorized_signatory']}")
        if tenant.get("address"):
            lines.append(f"**Tenant Address:** {tenant['address']}")
        if tenant.get("contact"):
            lines.append(f"**Tenant Contact:** {tenant['contact']}")

    if any(prop.values()):
        lines += ["", "### Property", ""]
        if prop.get("type"):
            lines.append(f"**Type:** {prop['type']}")
        if prop.get("area_sqft"):
            lines.append(f"**Area:** {prop['area_sqft']} sq ft")
        if prop.get("address"):
            lines.append(f"**Address:** {prop['address']}")

    lines += ["", "---", ""]
    return lines


_MD_DIFF_CSS = """\
<style>
.diff-container { font-family: 'Segoe UI', Roboto, sans-serif; font-size: 14px; line-height: 1.6; color: #000000 !important; background: #ffffff; padding: 16px; }
.diff-group { border-radius: 6px; margin: 12px 0; overflow: visible; position: relative; }
.diff-line { display: flex; align-items: flex-start; padding: 6px 10px; color: #000000 !important; }
.diff-line .gutter { flex: 0 0 24px; font-weight: bold; text-align: center; }
.diff-line .line-content { flex: 1; color: #000000 !important; }

/* Reason info icon — top-right corner of each group */
.reason-icon {
  position: absolute; top: 6px; right: 8px;
  width: 20px; height: 20px; border-radius: 50%;
  background: #6c757d; color: #fff !important;
  font-size: 12px; font-weight: bold; font-style: normal;
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer; z-index: 2; flex-shrink: 0;
}
.reason-icon:hover { background: #495057; }
.reason-icon .reason-tooltip {
  display: none; position: absolute; top: 28px; right: 0;
  background: #212529; color: #fff !important; padding: 10px 20px;
  border-radius: 6px; font-size: 12px; font-weight: normal;
  white-space: normal; width: 500px; line-height: 1.3;
  box-shadow: 0 4px 12px rgba(0,0,0,0.25); z-index: 10;
}
.reason-icon:hover .reason-tooltip { display: block; }

/* Violation (modified) — deleted = red, added = green */
.diff-group[data-type="modified"] .diff-line.deleted { background-color: #fde8e8; }
.diff-group[data-type="modified"] .diff-line.deleted .gutter { color: #dc3545 !important; }
.diff-group[data-type="modified"] .diff-line.deleted .old-text { color: #6b1015 !important; text-decoration: line-through; }
.diff-group[data-type="modified"] .diff-line.added { background-color: #d4edda; }
.diff-group[data-type="modified"] .diff-line.added .gutter { color: #28a745 !important; }
.diff-group[data-type="modified"] .diff-line.added .line-content { color: #155724 !important; }

/* Partially satisfied — deleted = orange, added = green */
.diff-group[data-type="partial"] .diff-line.deleted { background-color: #fff3cd; }
.diff-group[data-type="partial"] .diff-line.deleted .gutter { color: #fd7e14 !important; }
.diff-group[data-type="partial"] .diff-line.deleted .old-text { color: #856404 !important; text-decoration: line-through; }
.diff-group[data-type="partial"] .diff-line.added { background-color: #d4edda; }
.diff-group[data-type="partial"] .diff-line.added .gutter { color: #28a745 !important; }
.diff-group[data-type="partial"] .diff-line.added .line-content { color: #155724 !important; }

/* Not found — new clause = blue */
.diff-group[data-type="new"] .diff-line.new-clause { background-color: #e8f0fe; }
.diff-group[data-type="new"] .diff-line.new-clause .gutter { color: #0d6efd !important; }
.diff-group[data-type="new"] .diff-line.new-clause .line-content { color: #0a3577 !important; }

/* Unchanged — match and normal: no bg, pure black text */
.diff-group[data-type="unchanged"] .diff-line .line-content { color: #000000 !important; }

/* Tables */
.doc-table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
.doc-table th, .doc-table td { border: 1px solid #dee2e6; padding: 8px 12px; text-align: left; }
.doc-table th { font-weight: 700; }
</style>
"""


def generate_markdown_report(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> str:
    """
    Generate a redline-style Markdown report using semantic diff HTML.

    Each segment is wrapped in a .diff-group container with:
      data-type="modified|partial|new|unchanged"
      data-tooltip="<reason from AI>"
    Inside: .diff-line.deleted / .added / .new-clause / .unchanged
    """
    segments = _build_inline_segments(full_text, analysis_summary)
    lines = [_MD_DIFF_CSS, '<div class="diff-container">', ""]

    def _icon_html(reason: str) -> str:
        """Return the hover info-icon HTML if reason exists, else empty."""
        if not reason:
            return ""
        safe = reason.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
        return (
            f'<span class="reason-icon">i'
            f'<span class="reason-tooltip">{safe}</span>'
            f'</span>'
        )

    i = 0
    while i < len(segments):
        seg   = segments[i]
        stype = seg["type"]
        text  = seg["text"]
        reason = seg.get("reason", "")
        icon   = _icon_html(reason)

        if stype == "violation":
            # Modified group: deleted (red) + added (green)
            lines.append(f'<div class="diff-group" data-type="modified">')
            lines.append(icon) if icon else None
            lines.append('  <div class="diff-line deleted">')
            lines.append('    <div class="gutter">&minus;</div>')
            lines.append(f'    <div class="line-content"><span class="old-text" style="text-decoration:line-through">{text}</span></div>')
            lines.append("  </div>")
            if i + 1 < len(segments) and segments[i + 1]["type"] == "ai":
                i += 1
                ai_text = segments[i]["text"]
                lines.append('  <div class="diff-line added">')
                lines.append('    <div class="gutter">+</div>')
                lines.append(f'    <div class="line-content">{ai_text}</div>')
                lines.append("  </div>")
            lines.append("</div>")
            lines.append("")

        elif stype == "partial":
            # Partial group: deleted (orange) + added (green)
            lines.append(f'<div class="diff-group" data-type="partial">')
            lines.append(icon) if icon else None
            lines.append('  <div class="diff-line deleted">')
            lines.append('    <div class="gutter">&minus;</div>')
            lines.append(f'    <div class="line-content"><span class="old-text" style="text-decoration:line-through">{text}</span></div>')
            lines.append("  </div>")
            if i + 1 < len(segments) and segments[i + 1]["type"] == "ai":
                i += 1
                ai_text = segments[i]["text"]
                lines.append('  <div class="diff-line added">')
                lines.append('    <div class="gutter">+</div>')
                lines.append(f'    <div class="line-content">{ai_text}</div>')
                lines.append("  </div>")
            lines.append("</div>")
            lines.append("")

        elif stype == "not_found_ai":
            # New clause group (blue)
            lines.append(f'<div class="diff-group" data-type="new">')
            lines.append(icon) if icon else None
            lines.append('  <div class="diff-line new-clause">')
            lines.append('    <div class="gutter">+</div>')
            lines.append(f'    <div class="line-content">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")
            lines.append("")

        elif stype == "match":
            # Match — no bg, no tick, plain black text
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "normal":
            # Normal — no background
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "heading":
            # H1 bold heading
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content" style="font-size:18px;font-weight:bold;color:#1a1a2e;margin-top:12px;">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "bullet":
            # Indented bullet point — unchanged original content
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content" style="padding-left:20px;">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "ai":
            # Orphan AI
            lines.append('<div class="diff-group" data-type="modified">')
            lines.append('  <div class="diff-line added">')
            lines.append('    <div class="gutter">+</div>')
            lines.append(f'    <div class="line-content">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")
            lines.append("")

        elif stype == "table":
            lines.append(text)
            lines.append("")

        elif stype == "blank":
            lines.append('<div style="height: 8px;"></div>')

        i += 1

    lines.append("</div>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════════
#  MARKDOWN SUMMARY (Analytics) — no Clause Conflicts
# ══════════════════════════════════════════════════════════════════════════════════

def generate_markdown_summary(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> str:
    """Generate a Markdown analytics summary — score, tables, jurisdiction, timeline.
    No Clause Conflicts section."""
    jurisdiction_info = jurisdiction_info or {}

    match_count     = sum(1 for c in analysis_summary if c["result"] == "MATCH")
    violation_count = sum(1 for c in analysis_summary if c["result"] == "VIOLATION")
    partial_count   = sum(1 for c in analysis_summary if c["result"] == "PARTIALLY_SATISFIED")
    not_found_count = sum(1 for c in analysis_summary if c["result"] == "NOT_FOUND")
    total           = len(analysis_summary)
    compliance_score = round((match_count / total) * 100) if total else 0

    if compliance_score >= 80:
        score_status = "Compliant"
    elif compliance_score >= 50:
        score_status = "Needs Attention"
    else:
        score_status = "Critical"

    category_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    lines = [
        "## Summary",
        "",
        f"**Overall Compliance Score: {compliance_score}% — {score_status}**",
        "",
        "| Total | Match | Violation | Partial | Not Found |",
        "|:---:|:---:|:---:|:---:|:---:|",
        f"| **{total}** | **{match_count}** | **{violation_count}** | **{partial_count}** | **{not_found_count}** |",
        "",
    ]

    # ── Red Flag Summary ──────────────────────────────────────────────────────
    lines += ["## Critical Issues Requiring Immediate Attention", ""]
    red_flags = [e for e in analysis_summary if e["result"] == "VIOLATION"]
    red_flags += [e for e in analysis_summary if e["result"] == "NOT_FOUND"]
    red_flags = red_flags[:3]
    if not red_flags:
        lines.append("No critical issues found.")
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
    for cat in CATEGORY_ORDER:
        if cat not in category_stats:
            continue
        risk_lv, _ = _CATEGORY_RISK_LEVEL[cat]
        s = category_stats[cat]
        lines.append(
            f"| {cat} | {risk_lv} | {s['total']} | {s['compliant']} | {s['issues']} |"
        )
    lines += ["", "---", ""]

    # ── Jurisdiction & Compliance Checklist ───────────────────────────────────
    lines += ["## Jurisdiction & Compliance Checklist", ""]
    if jurisdiction_info:
        juris     = jurisdiction_info.get("jurisdiction", "Unknown")
        agmt_type = jurisdiction_info.get("agreement_type", "Unknown")
        laws      = ", ".join(jurisdiction_info.get("applicable_laws", []))
        lines.append(f"**Jurisdiction:** {juris}   **Agreement Type:** {agmt_type}")
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
    lines += [""]

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
    """Build a docx table with a dark header row."""
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


# ══════════════════════════════════════════════════════════════════════════════════
#  DOCX REPORT (Redline-style) — full PDF text + per-clause AI suggestions
# ══════════════════════════════════════════════════════════════════════════════════

def _docx_agreement_block(doc, agreement_meta: dict):
    """Render Agreement Details block into a DOCX document."""
    if not agreement_meta:
        return

    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}
    parties   = agreement_meta.get("parties") or {}
    prop      = agreement_meta.get("property") or {}

    doc.add_heading("Agreement Details", level=1)

    # Basic info table
    info_rows = []
    if agmt_type:
        info_rows.append(("Agreement Type", agmt_type))
    if agmt_det.get("agreement_date"):
        info_rows.append(("Agreement Date", agmt_det["agreement_date"]))
    place = ", ".join(v for v in [agmt_det.get("city"), agmt_det.get("state")] if v)
    if place:
        info_rows.append(("Location", place))

    if info_rows:
        _docx_dark_header_table(doc, ["Field", "Details"],
                                [[lbl, val] for lbl, val in info_rows])

    # Parties
    landlord = parties.get("landlord") or {}
    tenant   = parties.get("tenant") or {}
    party_rows = []
    if landlord.get("name"):
        party_rows.append(("Landlord Name",    landlord["name"]))
    if landlord.get("address"):
        party_rows.append(("Landlord Address", landlord["address"]))
    if landlord.get("contact"):
        party_rows.append(("Landlord Contact", landlord["contact"]))
    tenant_name = tenant.get("company_name") or tenant.get("name", "")
    if tenant_name:
        party_rows.append(("Tenant",           tenant_name))
    if tenant.get("authorized_signatory"):
        party_rows.append(("Authorized By",    tenant["authorized_signatory"]))
    if tenant.get("address"):
        party_rows.append(("Tenant Address",   tenant["address"]))
    if tenant.get("contact"):
        party_rows.append(("Tenant Contact",   tenant["contact"]))

    if party_rows:
        doc.add_heading("Parties", level=2)
        _docx_dark_header_table(doc, ["Field", "Details"],
                                [[lbl, val] for lbl, val in party_rows])

    # Property
    prop_rows = []
    if prop.get("type"):
        prop_rows.append(("Property Type",    prop["type"]))
    if prop.get("area_sqft"):
        prop_rows.append(("Area (sq ft)",     str(prop["area_sqft"])))
    if prop.get("address"):
        prop_rows.append(("Property Address", prop["address"]))

    if prop_rows:
        doc.add_heading("Property", level=2)
        _docx_dark_header_table(doc, ["Field", "Details"],
                                [[lbl, val] for lbl, val in prop_rows])

    doc.add_paragraph("_" * 80)


def generate_docx_report(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> bytes:
    """
    Generate a redline-style DOCX report:
      - Agreement details block
      - Full original PDF text (red bg)
      - Per clause: title + status, PDF data (red bg), AI suggestion (green bg)
    """
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    # ── Inline redline: original text + AI answers merged — no header/agreement ─
    segments = _build_inline_segments(full_text, analysis_summary)
    for seg in segments:
        stype = seg["type"]
        text  = seg["text"]

        if stype == "violation":
            # Red bg + strikethrough (VIOLATION)
            vp = doc.add_paragraph()
            run = vp.add_run("\u2212 ")
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(COLOR_RED)
            run = vp.add_run(text)
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(COLOR_RED)
            run.font.strikethrough = True
            _set_paragraph_shading(vp, BG_RED)

        elif stype == "ai":
            # Green bg — AI correction/addition
            ap = doc.add_paragraph()
            run = ap.add_run("+ ")
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(COLOR_GREEN)
            run = ap.add_run(text)
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb("#155724")
            _set_paragraph_shading(ap, BG_GREEN)

        elif stype == "partial":
            # Orange bg — PARTIALLY_SATISFIED original text
            pp = doc.add_paragraph()
            run = pp.add_run("\u2212 ")
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(COLOR_ORANGE)
            run = pp.add_run(text)
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(COLOR_ORANGE)
            run.font.strikethrough = True
            _set_paragraph_shading(pp, BG_ORANGE)

        elif stype == "not_found_ai":
            # Blue bg — NOT_FOUND AI (no anchor found in doc)
            nfp = doc.add_paragraph()
            run = nfp.add_run("+ ")
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb(COLOR_BLUE)
            run = nfp.add_run(text)
            run.font.size = Pt(10)
            run.font.color.rgb = _hex_to_rgb("#0a3577")
            _set_paragraph_shading(nfp, BG_BLUE)

        elif stype == "match":
            # No color — satisfied/match clause (plain text)
            mp = doc.add_paragraph()
            run = mp.add_run(text)
            run.font.size = Pt(10)

        elif stype == "heading":
            # H1 bold heading
            hp = doc.add_paragraph()
            run = hp.add_run(text)
            run.bold = True
            run.font.size = Pt(16)
            run.font.color.rgb = _hex_to_rgb(COLOR_DARK)

        elif stype == "bullet":
            # Indented bullet point
            bp = doc.add_paragraph()
            run = bp.add_run(text)
            run.font.size = Pt(10)
            bp.paragraph_format.left_indent = Pt(16)

        else:  # normal
            np_ = doc.add_paragraph()
            run = np_.add_run(text)
            run.font.size = Pt(10)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════════
#  DOCX SUMMARY (Analytics) — no Clause Conflicts
# ══════════════════════════════════════════════════════════════════════════════════

def generate_docx_summary(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> bytes:
    """
    Generate a DOCX analytics summary with score, critical issues,
    risk breakdown, jurisdiction checklist, and timeline.
    No Clause Conflicts section.
    """
    jurisdiction_info = jurisdiction_info or {}

    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    # ── Pre-compute stats ────────────────────────────────────────────────────
    match_count     = sum(1 for c in analysis_summary if c["result"] == "MATCH")
    violation_count = sum(1 for c in analysis_summary if c["result"] == "VIOLATION")
    partial_count   = sum(1 for c in analysis_summary if c["result"] == "PARTIALLY_SATISFIED")
    not_found_count = sum(1 for c in analysis_summary if c["result"] == "NOT_FOUND")
    total           = len(analysis_summary)
    compliance_score = round((match_count / total) * 100) if total else 0

    if compliance_score >= 80:
        score_status = "Compliant"
        score_color  = COLOR_SCORE_HIGH
    elif compliance_score >= 50:
        score_status = "Needs Attention"
        score_color  = COLOR_SCORE_MED
    else:
        score_status = "Critical"
        score_color  = COLOR_SCORE_LOW

    category_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    # ── Summary table ────────────────────────────────────────────────────────
    doc.add_heading("Summary", level=1)

    summary_tbl = doc.add_table(rows=2, cols=5)
    summary_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["Total", "Match", "Violation", "Partial", "Not Found"]
    values  = [str(total), str(match_count), str(violation_count), str(partial_count), str(not_found_count)]
    val_colors = [None, COLOR_GREEN, COLOR_RED, COLOR_ORANGE, COLOR_BLUE]

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

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()

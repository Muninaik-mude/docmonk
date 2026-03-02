import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

# ── Shared color constants ────────────────────────────────────────────────────────
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


# ── Risk categorization ──────────────────────────────────────────────────────────

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


# ── Shared segment builder (used by MD, PDF, and DOCX reports) ───────────────────

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
            key = rt or clause_title
            if key:
                violation_map[key] = (ai_text, reason)
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

    _junk       = re.compile(r'^[\d\.\-_\s\u2022\u00b7\u25cf\u25cb\u25cc\u25e6\*]+$')
    _num_prefix = re.compile(r'^[\*\s]*\d+[\.\)]\\s*(?:[A-Z][A-Za-z ,&]+:\\s*)?')
    _md_bullet  = re.compile(r'^\*\s+')
    _md_heading = re.compile(r'^#+\s*')
    _table_line = re.compile(r'^\|.+\|$')
    _table_sep  = re.compile(r'^\|[\s\-:|\\+]+\|$')

    def _clean_display(s: str) -> str:
        s = _md_bullet.sub('&nbsp;&nbsp;&nbsp;&nbsp;\u2022 ', s).replace('\\.', '.')
        if s.startswith('#'):
            s = '<b>' + _md_heading.sub('', s) + '</b>'
        return s

    def _line_matches(s: str, key: str) -> bool:
        if not key or not s:
            return False
        if key in s or s in key:
            return True
        s_lower, key_lower = s.lower(), key.lower()
        if key_lower in s_lower or s_lower in key_lower:
            return True
        clean = _num_prefix.sub('', s).strip()
        if clean and len(clean) > 6 and (clean in key or key in clean):
            return True
        if clean and len(clean) > 6 and (clean.lower() in key_lower or key_lower in clean.lower()):
            return True
        return False

    segments = []

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

        # ── 2. Partial check ───────────────────────────────────────────────
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
            heading_text = _md_heading.sub('', stripped).strip()
            segments.append({"type": "heading", "text": heading_text})
        elif _md_bullet.match(stripped):
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


# ══════════════════════════════════════════════════════════════════════════════════
#  MARKDOWN REPORT (Redline-style) — full text + AI suggestions as styled HTML/MD
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
    Inside: .diff-line.deleted / .added / .new-clause / .unchanged
    """
    segments = _build_inline_segments(full_text, analysis_summary)
    lines = [_MD_DIFF_CSS, '<div class="diff-container">', ""]

    def _icon_html(reason: str) -> str:
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
        seg    = segments[i]
        stype  = seg["type"]
        text   = seg["text"]
        reason = seg.get("reason", "")
        icon   = _icon_html(reason)

        if stype == "violation":
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
            lines.append(f'<div class="diff-group" data-type="new">')
            lines.append(icon) if icon else None
            lines.append('  <div class="diff-line new-clause">')
            lines.append('    <div class="gutter">+</div>')
            lines.append(f'    <div class="line-content">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")
            lines.append("")

        elif stype == "match":
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "normal":
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "heading":
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content" style="font-size:18px;font-weight:bold;color:#1a1a2e;margin-top:12px;">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "bullet":
            lines.append('<div class="diff-group" data-type="unchanged">')
            lines.append('  <div class="diff-line normal">')
            lines.append(f'    <div class="line-content" style="padding-left:20px;">{text}</div>')
            lines.append("  </div>")
            lines.append("</div>")

        elif stype == "ai":
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
#  MARKDOWN SUMMARY (Analytics) — score, tables, jurisdiction, timeline
# ══════════════════════════════════════════════════════════════════════════════════

def generate_markdown_summary(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> str:
    """Generate a Markdown analytics summary — score, tables, jurisdiction, timeline."""
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

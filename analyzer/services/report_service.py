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
            key = rt or cc or clause_title
            if key:
                violation_map[key] = (ai_text, reason)

        elif result == "PARTIALLY_SATISFIED":
            key = rt or cc or clause_title
            if key:
                partial_map[key] = (ai_text, reason)

        elif result == "NOT_FOUND":
            if ai_text:
                not_found_list.append((ai_text, reason))

        elif result == "MATCH":
            key = rt or cc
            if key:
                match_set.append(key)

    _junk       = re.compile(r'^[\d\.\-_\s\u2022\u00b7\u25cf\u25cb\u25cc\u25e6\*]+$')
    _num_prefix = re.compile(r'^[\s\u2022\u00b7\u25cf\u25cb\u25cc\u25e6\*\-]*\d+[\.\)]\s*')
    _md_bullet  = re.compile(r'^\*\s+')
    _md_heading = re.compile(r'^#+\s*')
    _table_line = re.compile(r'^\|.+\|$')
    _table_sep  = re.compile(r'^\|[\s\-:|\\+]+\|$')
    _punct_re   = re.compile(r'[^\w\s]')
    _ws_re      = re.compile(r'\s+')
    _html_tag   = re.compile(r'<[^>]+>')

    def _clean_display(s: str) -> str:
        s = _md_bullet.sub('&nbsp;&nbsp;&nbsp;&nbsp;\u2022 ', s).replace('\\.', '.')
        if s.startswith('#'):
            s = '<b>' + _md_heading.sub('', s) + '</b>'
        return s

    def _normalize(s: str) -> str:
        """Strip HTML tags, bullets/numbering, punctuation, collapse whitespace, lowercase."""
        s = _html_tag.sub(' ', s)
        s = _num_prefix.sub('', s.lstrip())
        s = _punct_re.sub(' ', s)
        return _ws_re.sub(' ', s).strip().lower()

    def _line_matches(s: str, key: str) -> bool:
        if not key or not s:
            return False

        # Pass 1 — exact substring (case-sensitive, then case-insensitive)
        if key in s or s in key:
            return True
        sl, kl = s.lower(), key.lower()
        if kl in sl or sl in kl:
            return True

        # Pass 2 — normalize both sides: strip bullets/numbering/punctuation, lowercase
        sn, kn = _normalize(s), _normalize(key)
        if sn and kn and (kn in sn or sn in kn):
            return True

        # Pass 3 — word overlap >= 80% on normalized strings
        # relevant_text was extracted FROM this document — if AI rephrased slightly,
        # word overlap guarantees we still locate it in the right place.
        if sn and kn and len(sn) > 15 and len(kn) > 15:
            sw, kw = set(sn.split()), set(kn.split())
            shorter = sw if len(sw) <= len(kw) else kw
            longer  = sw if len(sw) >  len(kw) else kw
            if shorter and len(shorter & longer) / len(shorter) >= 0.80:
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
/* Reason info icon */
.reason-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 50%;
  background: #6c757d; color: #fff !important;
  font-size: 11px; font-weight: bold; font-style: normal;
  text-decoration: none !important;
  cursor: pointer; margin-left: 6px; vertical-align: middle;
  position: relative; z-index: 2; flex-shrink: 0;
}
.reason-icon:hover { background: #495057; }
.reason-icon .reason-tooltip {
  display: none; position: absolute; top: 24px; right: 0;
  background: #212529; color: #fff !important; padding: 10px 14px;
  border-radius: 6px; font-size: 12px; font-weight: normal;
  white-space: normal; width: 440px; line-height: 1.4;
  box-shadow: 0 4px 12px rgba(0,0,0,0.25); z-index: 10;
  text-decoration: none !important;
}
.reason-icon:hover .reason-tooltip { display: block; }

/* Diff structure */
.diff-group { margin: 2px 0; position: relative; }
.diff-line { display: flex; align-items: baseline; }
.diff-line .gutter {
  width: 20px; flex-shrink: 0; text-align: center;
  font-weight: bold; user-select: none; padding: 4px 2px;
}
.diff-line .line-content { flex: 1; padding: 4px 8px; }

/* Deleted lines — violation (red) */
.diff-line.deleted .line-content { background-color: #fde8e8; }
.diff-line.deleted .gutter { color: #dc3545; }
.diff-line.deleted .old-text { text-decoration: line-through; }

/* Deleted lines — partial (orange) */
.diff-group[data-type="partial"] .diff-line.deleted .line-content { background-color: #fff3cd; }
.diff-group[data-type="partial"] .diff-line.deleted .gutter { color: #fd7e14; }

/* Added lines — suggestion (green) */
.diff-line.added .line-content { background-color: #d4edda; }
.diff-line.added .gutter { color: #28a745; }

/* Added lines — missing clause (blue) */
.diff-group[data-type="new"] .diff-line.added .line-content { background-color: #e8f0fe; }
.diff-group[data-type="new"] .diff-line.added .gutter { color: #0d6efd; }

/* Normal / unchanged lines — no background change */
.diff-line.normal .line-content { }

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
    lines = [_MD_DIFF_CSS]

    # ── Extract document base style and heading span style from full_text ─────
    _outer_div_re  = re.compile(r'<div\s+style="([^"]*)"')
    # Group 1 = span style, Group 2 = heading text (e.g. "4. Rent")
    _heading_sp_re = re.compile(r'<span\s+style="([^"]+)">\s*(\d+[\.\)][^<]+)</span>')
    _section_re    = re.compile(r'^\d+[\.\)]\s+\S')

    doc_base_style    = ""
    doc_heading_style = ""
    if full_text:
        m = _outer_div_re.search(full_text)
        if m:
            doc_base_style = m.group(1)
        m = _heading_sp_re.search(full_text)
        if m:
            doc_heading_style = m.group(1)


    def _format_ai_text(ai_text: str, source_html: str = "") -> str:
        """
        Format the AI suggestion text preserving document styling:
        - Lines matching a numbered section pattern (e.g. "4. Rent") get wrapped
          with the heading span style extracted from source_html (or doc_heading_style).
        - All other lines are wrapped in <p> tags.
        """
        if not ai_text:
            return ""
        heading_style = doc_heading_style
        heading_text = ""
        if source_html:
            m = _heading_sp_re.search(source_html)
            if m:
                heading_style = m.group(1)
                heading_text = m.group(2).strip()
        parts = []
        for line in ai_text.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            if heading_style and _section_re.match(stripped):
                # If the AI concatenated heading + body on one line, split them
                if heading_text and stripped.lower().startswith(heading_text.lower()) and len(stripped) > len(heading_text) + 2:
                    rest = stripped[len(heading_text):].strip()
                    parts.append(f'<p><span style="{heading_style}">{heading_text}</span></p>')
                    if rest:
                        parts.append(f'<p>{rest}</p>')
                else:
                    parts.append(f'<p><span style="{heading_style}">{stripped}</span></p>')
            else:
                parts.append(f'<p>{stripped}</p>')
        return ''.join(parts)

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
            ai_text = ""
            if i + 1 < len(segments) and segments[i + 1]["type"] == "ai":
                i += 1
                ai_text = segments[i]["text"]
            html = f'<div class="diff-group" data-type="modified">\n'
            if icon:
                html += f'{icon}\n'
            html += (
                f'  <div class="diff-line deleted">\n'
                f'    <div class="gutter">&minus;</div>\n'
                f'    <div class="line-content" style="background-color:#fde8e8;text-decoration:line-through;"><span class="old-text">{text}</span></div>\n'
                f'  </div>\n'
            )
            if ai_text:
                html += (
                    f'  <div class="diff-line added">\n'
                    f'    <div class="gutter">+</div>\n'
                    f'    <div class="line-content" style="background-color:#d4edda;">{_format_ai_text(ai_text, text)}</div>\n'
                    f'  </div>\n'
                )
            html += '</div>'
            lines.append(html)

        elif stype == "partial":
            ai_text = ""
            if i + 1 < len(segments) and segments[i + 1]["type"] == "ai":
                i += 1
                ai_text = segments[i]["text"]
            html = f'<div class="diff-group" data-type="partial">\n'
            if icon:
                html += f'{icon}\n'
            html += (
                f'  <div class="diff-line deleted">\n'
                f'    <div class="gutter">&minus;</div>\n'
                f'    <div class="line-content" style="background-color:#fff3cd;text-decoration:line-through;"><span class="old-text">{text}</span></div>\n'
                f'  </div>\n'
            )
            if ai_text:
                html += (
                    f'  <div class="diff-line added">\n'
                    f'    <div class="gutter">+</div>\n'
                    f'    <div class="line-content" style="background-color:#d4edda;">{_format_ai_text(ai_text, text)}</div>\n'
                    f'  </div>\n'
                )
            html += '</div>'
            lines.append(html)

        elif stype == "not_found_ai":
            html = f'<div class="diff-group" data-type="new">\n'
            if icon:
                html += f'{icon}\n'
            html += (
                f'  <div class="diff-line added">\n'
                f'    <div class="gutter">+</div>\n'
                f'    <div class="line-content" style="background-color:#e8f0fe;">{_format_ai_text(text)}</div>\n'
                f'  </div>\n'
                f'</div>'
            )
            lines.append(html)

        elif stype in ("match", "normal", "heading", "bullet"):
            lines.append(
                f'<div class="diff-group" data-type="unchanged">'
                f'<div class="diff-line normal">'
                f'<div class="line-content">{text}</div>'
                f'</div></div>'
            )

        elif stype == "ai":
            # orphaned ai segment (no preceding violation/partial) — render as suggestion
            lines.append(
                f'<div class="diff-group" data-type="new">'
                f'<div class="diff-line added">'
                f'<div class="gutter">+</div>'
                f'<div class="line-content" style="background-color:#d4edda;">{_format_ai_text(text)}</div>'
                f'</div></div>'
            )

        elif stype == "table":
            lines.append(text)

        elif stype == "blank":
            pass  # original HTML <p> tags handle spacing naturally

        i += 1

    content = "\n".join(lines)
    if doc_base_style:
        content = f'<div style="{doc_base_style}">\n{content}\n</div>'
    return content


# ══════════════════════════════════════════════════════════════════════════════════
#  MARKDOWN SUMMARY (Analytics) — score, tables, jurisdiction, timeline
# ══════════════════════════════════════════════════════════════════════════════════

_SUMMARY_CSS = """\
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
.sr { font-family: 'Segoe UI', Roboto, Arial, sans-serif; font-size: 14px;
      line-height: 1.6; color: #1a1a2e; background: #f4f6fb; padding: 24px; }

/* ── Section card ── */
.sr-card { background: #fff; border-radius: 10px; padding: 20px 24px;
           margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.sr-card-title { font-size: 13px; font-weight: 700; letter-spacing: .06em;
                 text-transform: uppercase; color: #6c757d; margin-bottom: 14px;
                 padding-bottom: 10px; border-bottom: 1px solid #e9ecef; }

/* ── Header banner (score circle + report title) ── */
.sr-header { display: flex; align-items: center; gap: 28px;
             background: #fff; border-radius: 12px; padding: 28px 32px;
             margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.sr-header-text { flex: 1; }
.sr-header-title { font-size: 22px; font-weight: 800; color: #1a1a2e; margin-bottom: 10px; }
.sr-header-sub   { font-size: 13px; color: #6c757d; display: flex;
                   gap: 8px; align-items: center; flex-wrap: wrap; }
.sr-header-sub .dot { color: #ced4da; font-size: 16px; line-height: 1; }

/* ── Stats row (separate cards below header) ── */
.sr-stats-row { display: flex; gap: 12px; margin-bottom: 20px; }
.sr-stat-card { flex: 1; background: #fff; border-radius: 10px; padding: 20px 16px;
                text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,0.08);
                border-top: 3px solid transparent; }
.sr-stat-card.total { border-top-color: #495057; }
.sr-stat-card.match { border-top-color: #28a745; }
.sr-stat-card.viol  { border-top-color: #dc3545; }
.sr-stat-card.part  { border-top-color: #fd7e14; }
.sr-stat-card.nf    { border-top-color: #0d6efd; }
.sr-stat-num { font-size: 32px; font-weight: 800; line-height: 1; }
.sr-stat-lbl { font-size: 10px; font-weight: 700; letter-spacing: .06em;
               text-transform: uppercase; margin-top: 6px; color: #6c757d; }
.sr-stat-card.total .sr-stat-num { color: #212529; }
.sr-stat-card.match .sr-stat-num { color: #28a745; }
.sr-stat-card.viol  .sr-stat-num { color: #dc3545; }
.sr-stat-card.part  .sr-stat-num { color: #fd7e14; }
.sr-stat-card.nf    .sr-stat-num { color: #0d6efd; }

/* ── Badges ── */
.badge { display: inline-block; padding: 2px 10px; border-radius: 20px;
         font-size: 11px; font-weight: 700; white-space: nowrap; }
.badge-match   { background: #d4edda; color: #155724; }
.badge-viol    { background: #fde8e8; color: #7b0d14; }
.badge-part    { background: #fff3cd; color: #7d4e00; }
.badge-nf      { background: #e8f0fe; color: #0a3577; }
.badge-req     { background: #fde8e8; color: #7b0d14; }
.badge-opt     { background: #e9ecef; color: #495057; }
.badge-high    { background: #fde8e8; color: #7b0d14; }
.badge-medium  { background: #fff3cd; color: #7d4e00; }
.badge-low     { background: #d4edda; color: #155724; }

/* ── Tables ── */
.sr-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.sr-table th { background: #f8f9fa; font-weight: 700; font-size: 11px;
               letter-spacing: .05em; text-transform: uppercase; color: #6c757d;
               padding: 10px 12px; text-align: left; border-bottom: 2px solid #e9ecef; }
.sr-table td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0;
               vertical-align: top; }
.sr-table tr:last-child td { border-bottom: none; }
.sr-table tr:hover td { background: #fafbff; }

/* ── Critical issues ── */
.sr-flag { border-left: 4px solid #dc3545; padding: 10px 14px;
           background: #fffafa; border-radius: 0 6px 6px 0; margin-bottom: 10px; }
.sr-flag.nf { border-left-color: #0d6efd; background: #f5f8ff; }
.sr-flag-title { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
.sr-flag-reason { font-size: 12px; color: #555; line-height: 1.5; }

/* ── Progress bar ── */
.sr-bar-wrap { background: #e9ecef; border-radius: 4px; height: 6px;
               margin-top: 6px; overflow: hidden; }
.sr-bar      { height: 6px; border-radius: 4px; background: #28a745; }

/* ── Checklist ── */
.sr-check-item { display: flex; justify-content: space-between;
                 align-items: center; padding: 9px 0;
                 border-bottom: 1px solid #f0f0f0; font-size: 13px; }
.sr-check-item:last-child { border-bottom: none; }

/* ── Meta pills ── */
.sr-meta { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.sr-meta-pill { background: #f0f4ff; border: 1px solid #d0dcff; border-radius: 20px;
                padding: 3px 12px; font-size: 12px; color: #1a3a8f; font-weight: 500; }

/* ── Timeline ── */
.sr-tl-clause { font-weight: 600; font-size: 13px; }
.sr-tl-item   { font-size: 12px; color: #495057; }

/* ── Section header ── */
.sr-section-hdr { font-size: 15px; font-weight: 700; color: #1a1a2e;
                  margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
.sr-section-hdr::before { content: ''; display: inline-block; width: 4px;
                           height: 18px; border-radius: 2px; background: currentColor;
                           opacity: .35; }
</style>
"""


def _badge(result: str) -> str:
    cls = {"MATCH": "badge-match", "VIOLATION": "badge-viol",
           "PARTIALLY_SATISFIED": "badge-part", "NOT_FOUND": "badge-nf"}.get(result, "badge-opt")
    label = {"MATCH": "Match", "VIOLATION": "Violation",
              "PARTIALLY_SATISFIED": "Partial", "NOT_FOUND": "Not Found"}.get(result, result)
    return f'<span class="badge {cls}">{label}</span>'


def _risk_badge(level: str) -> str:
    cls = {"HIGH": "badge-high", "MEDIUM": "badge-medium", "LOW": "badge-low"}.get(level, "badge-opt")
    return f'<span class="badge {cls}">{level}</span>'


def generate_markdown_summary(
    analysis_summary: list,
    *,
    conflicts: list = None,
    jurisdiction_info: dict = None,
    full_text: str = "",
    agreement_meta: dict = None,
) -> str:
    """Generate a premium HTML analytics summary — score, tables, jurisdiction, timeline."""
    jurisdiction_info = jurisdiction_info or {}
    agreement_meta    = agreement_meta or {}

    match_count     = sum(1 for c in analysis_summary if c["result"] == "MATCH")
    violation_count = sum(1 for c in analysis_summary if c["result"] == "VIOLATION")
    partial_count   = sum(1 for c in analysis_summary if c["result"] == "PARTIALLY_SATISFIED")
    not_found_count = sum(1 for c in analysis_summary if c["result"] == "NOT_FOUND")
    total           = len(analysis_summary)
    compliance_score = round((match_count / total) * 100) if total else 0

    if compliance_score >= 80:
        score_status, score_color, score_bg = "Compliant",       "#28a745", "#d4edda"
    elif compliance_score >= 50:
        score_status, score_color, score_bg = "Needs Attention", "#fd7e14", "#fff3cd"
    else:
        score_status, score_color, score_bg = "Critical",        "#dc3545", "#fde8e8"

    category_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "compliant": 0, "issues": 0})
    for entry in analysis_summary:
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        category_stats[cat]["total"] += 1
        if entry["result"] == "MATCH":
            category_stats[cat]["compliant"] += 1
        else:
            category_stats[cat]["issues"] += 1

    h = [_SUMMARY_CSS, '<div class="sr">']

    # ── Agreement meta header ─────────────────────────────────────────────────
    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}
    parties   = agreement_meta.get("parties") or {}
    if agmt_type or agmt_det or parties:
        h.append('<div class="sr-card">')
        if agmt_type:
            h.append(f'<div style="font-size:18px;font-weight:800;color:#1a1a2e;margin-bottom:10px;">{agmt_type}</div>')
        pills = []
        if agmt_det.get("agreement_date"):
            pills.append(agmt_det["agreement_date"])
        place = ", ".join(v for v in [agmt_det.get("city"), agmt_det.get("state")] if v)
        if place:
            pills.append(place)
        landlord = (parties.get("landlord") or {}).get("name", "")
        tenant   = (parties.get("tenant") or {}).get("company_name") or (parties.get("tenant") or {}).get("name", "")
        if landlord:
            pills.append(f"Landlord: {landlord}")
        if tenant:
            pills.append(f"Tenant: {tenant}")
        if pills:
            h.append('<div class="sr-meta">')
            for p in pills:
                h.append(f'<span class="sr-meta-pill">{p}</span>')
            h.append('</div>')
        h.append('</div>')

    # ── Header banner: circular score + report title ─────────────────────────
    circ = 251.33  # 2 * pi * 40
    dash = round(circ * compliance_score / 100, 2)
    h.append(f'''
<div class="sr-header">
  <svg width="110" height="110" viewBox="0 0 100 100" style="flex-shrink:0;">
    <circle cx="50" cy="50" r="40" fill="none" stroke="#e9ecef" stroke-width="8"/>
    <circle cx="50" cy="50" r="40" fill="none" stroke="{score_color}" stroke-width="8"
            stroke-dasharray="{dash} {circ}" stroke-linecap="round"
            transform="rotate(-90 50 50)"/>
    <text x="50" y="46" text-anchor="middle" font-size="19" font-weight="800"
          fill="{score_color}" font-family="Segoe UI,Roboto,Arial,sans-serif">{compliance_score}%</text>
    <text x="50" y="62" text-anchor="middle" font-size="9" font-weight="600"
          fill="#6c757d" font-family="Segoe UI,Roboto,Arial,sans-serif" letter-spacing="0.04em">SCORE</text>
  </svg>
  <div class="sr-header-text">
    <div class="sr-header-title">Compliance Analysis Report</div>
    <div class="sr-header-sub">
      Overall status:&nbsp;<span style="color:{score_color};font-weight:700;">{score_status}</span>
      <span class="dot">·</span> {total} clauses reviewed
      <span class="dot">·</span> {violation_count} violation{"s" if violation_count != 1 else ""} found
    </div>
  </div>
</div>''')

    # ── Stats row ─────────────────────────────────────────────────────────────
    h.append('<div class="sr-stats-row">')
    for css, num, lbl in [
        ("total", total,           "Total"),
        ("match", match_count,     "Match"),
        ("viol",  violation_count, "Violation"),
        ("part",  partial_count,   "Partial"),
        ("nf",    not_found_count, "Not Found"),
    ]:
        h.append(f'''
<div class="sr-stat-card {css}">
  <div class="sr-stat-num">{num}</div>
  <div class="sr-stat-lbl">{lbl}</div>
</div>''')
    h.append('</div>')  # sr-stats-row

    # ── Critical Issues ───────────────────────────────────────────────────────
    red_flags = [e for e in analysis_summary if e["result"] == "VIOLATION"]
    red_flags += [e for e in analysis_summary if e["result"] == "NOT_FOUND"]
    if red_flags:
        h.append('<div class="sr-card">')
        h.append('<div class="sr-card-title">Critical Issues Requiring Immediate Attention</div>')
        for flag in red_flags:
            css_extra = " nf" if flag["result"] == "NOT_FOUND" else ""
            reason = (flag.get("reason") or "").replace("<", "&lt;").replace(">", "&gt;")
            h.append(f'''
<div class="sr-flag{css_extra}">
  <div class="sr-flag-title">{flag["clause_title"]} &nbsp; {_badge(flag["result"])}</div>
  <div class="sr-flag-reason">{reason}</div>
</div>''')
        h.append('</div>')

    # ── Full Clause Results ───────────────────────────────────────────────────
    h.append('<div class="sr-card">')
    h.append('<div class="sr-card-title">All Clause Results</div>')
    h.append('<table class="sr-table"><thead><tr>')
    h.append('<th>#</th><th>Clause</th><th>Category</th><th>Status</th><th>Finding</th>')
    h.append('</tr></thead><tbody>')
    for i, entry in enumerate(analysis_summary, 1):
        reason = (entry.get("reason") or "").replace("<", "&lt;").replace(">", "&gt;")
        cat, _ = _get_risk_info(entry.get("clause_title", ""))
        h.append(f'''<tr>
  <td style="color:#adb5bd;font-size:12px;">{i}</td>
  <td style="font-weight:600;">{entry["clause_title"]}</td>
  <td style="font-size:12px;color:#6c757d;">{cat}</td>
  <td>{_badge(entry["result"])}</td>
  <td style="font-size:12px;color:#555;">{reason}</td>
</tr>''')
    h.append('</tbody></table></div>')

    # ── Risk Category Breakdown ───────────────────────────────────────────────
    h.append('<div class="sr-card">')
    h.append('<div class="sr-card-title">Risk Category Breakdown</div>')
    h.append('<table class="sr-table"><thead><tr>')
    h.append('<th>Category</th><th>Risk Level</th><th>Total</th><th>Compliant</th><th>Issues</th><th>Pass Rate</th>')
    h.append('</tr></thead><tbody>')
    for cat in CATEGORY_ORDER:
        if cat not in category_stats:
            continue
        risk_lv, _ = _CATEGORY_RISK_LEVEL[cat]
        s = category_stats[cat]
        pass_pct = round((s["compliant"] / s["total"]) * 100) if s["total"] else 0
        bar_color = "#28a745" if pass_pct >= 80 else "#fd7e14" if pass_pct >= 50 else "#dc3545"
        h.append(f'''<tr>
  <td style="font-weight:600;">{cat}</td>
  <td>{_risk_badge(risk_lv)}</td>
  <td style="text-align:center;">{s["total"]}</td>
  <td style="text-align:center;color:#28a745;font-weight:700;">{s["compliant"]}</td>
  <td style="text-align:center;color:#dc3545;font-weight:700;">{s["issues"]}</td>
  <td style="min-width:100px;">
    <div style="font-size:11px;font-weight:700;color:{bar_color};margin-bottom:3px;">{pass_pct}%</div>
    <div class="sr-bar-wrap"><div class="sr-bar" style="width:{pass_pct}%;background:{bar_color};"></div></div>
  </td>
</tr>''')
    h.append('</tbody></table></div>')

    # ── Jurisdiction & Compliance Checklist ───────────────────────────────────
    h.append('<div class="sr-card">')
    h.append('<div class="sr-card-title">Jurisdiction &amp; Compliance Checklist</div>')
    if jurisdiction_info:
        juris     = jurisdiction_info.get("jurisdiction", "Unknown")
        agmt_type_j = jurisdiction_info.get("agreement_type", "")
        laws      = jurisdiction_info.get("applicable_laws", [])
        pills_j = [juris]
        if agmt_type_j:
            pills_j.append(agmt_type_j)
        h.append('<div class="sr-meta">')
        for p in pills_j:
            h.append(f'<span class="sr-meta-pill">{p}</span>')
        h.append('</div>')
        if laws:
            h.append('<div style="font-size:12px;color:#6c757d;margin-bottom:12px;">')
            h.append('<strong>Applicable Laws:</strong> ' + " &nbsp;·&nbsp; ".join(laws))
            h.append('</div>')
        checklist = jurisdiction_info.get("checklist", [])
        for item in checklist:
            req     = item.get("required", False)
            lbl_cls = "badge-req" if req else "badge-opt"
            lbl_txt = "Required" if req else "Optional"
            item_text = (item.get("item") or "").replace("<", "&lt;").replace(">", "&gt;")
            h.append(f'''
<div class="sr-check-item">
  <span>{item_text}</span>
  <span class="badge {lbl_cls}">{lbl_txt}</span>
</div>''')
    else:
        h.append('<p style="color:#6c757d;font-size:13px;">Jurisdiction information not available.</p>')
    h.append('</div>')

    # ── Contract Timeline ─────────────────────────────────────────────────────
    timeline_rows = [
        (entry["clause_title"], dt)
        for entry in analysis_summary
        for dt in entry.get("key_dates_durations", [])
        if dt
    ]
    if timeline_rows:
        h.append('<div class="sr-card">')
        h.append('<div class="sr-card-title">Contract Timeline &amp; Key Dates</div>')
        h.append('<table class="sr-table"><thead><tr><th>Clause</th><th>Timeline Item</th></tr></thead><tbody>')
        for clause_title, tl_item in timeline_rows:
            tl_safe = tl_item.replace("<", "&lt;").replace(">", "&gt;")
            h.append(f'''<tr>
  <td class="sr-tl-clause">{clause_title}</td>
  <td class="sr-tl-item">{tl_safe}</td>
</tr>''')
        h.append('</tbody></table></div>')

    h.append('</div>')  # .sr
    return "\n".join(h)
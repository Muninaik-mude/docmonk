import io
import logging
import math
import re

from analyzer.services.report_service import _MD_DIFF_CSS, _SUMMARY_CSS

logger = logging.getLogger(__name__)

# Status → background colour
_STATUS_BG = {
    "SATISFIES":     "#d4edda",
    "VIOLATES":      "#fde8e8",
    "RISKY":         "#fff3cd",
    "NOT_ADDRESSED": None,
}

# Priority when multiple requirements overlap the same text
_STATUS_PRIORITY = {"VIOLATES": 0, "RISKY": 1, "SATISFIES": 2, "NOT_ADDRESSED": 3}

# ── Global-tooltip JS (mouse-following, always stays in viewport) ──────────────
_TOOLTIP_JS = """
<div id="g-tip" style="
  display:none; position:fixed; background:#1e2430; color:#f0f0f0;
  padding:10px 14px; border-radius:7px; width:300px; max-width:90vw;
  font-size:12px; line-height:1.5; z-index:99999; pointer-events:none;
  box-shadow:0 4px 16px rgba(0,0,0,0.35);
"></div>
<script>
(function(){
  var tip = document.getElementById('g-tip');
  function pos(e){
    var w=tip.offsetWidth||300, h=tip.offsetHeight||80;
    var vw=window.innerWidth, vh=window.innerHeight;
    var x=e.clientX+14, y=e.clientY+14;
    if(x+w>vw-8) x=e.clientX-w-10;
    if(y+h>vh-8) y=e.clientY-h-10;
    tip.style.left=x+'px'; tip.style.top=y+'px';
  }
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mouseenter',function(e){
      tip.innerHTML=this.getAttribute('data-tip');
      tip.style.display='block'; pos(e);
    });
    el.addEventListener('mousemove',pos);
    el.addEventListener('mouseleave',function(){tip.style.display='none';});
  });
})();
</script>
"""

_DOC_CSS = """
<style>
.pdv-wrap {
  font-family: 'Segoe UI', Roboto, Arial, sans-serif;
  font-size: 13px; line-height: 1.6; color: #212529;
}
.pdv-title {
  font-size: 16px; font-weight: 800; text-align: center;
  color: #1a1a2e; margin: 6px 0 2px;
}
.pdv-subtitle {
  font-size: 12px; text-align: center; color: #6c757d; margin-bottom: 8px;
}
.pdv-heading {
  font-weight: 700; font-size: 13px; color: #1a1a2e;
  margin: 12px 0 4px; padding: 4px 0;
  border-bottom: 1px solid #dee2e6;
}
.pdv-para { margin: 3px 0; padding: 2px 0; }
.pdv-table {
  border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 13px;
}
.pdv-table td {
  border: 1px solid #dee2e6; padding: 5px 10px; vertical-align: top;
}
.pdv-table td.lbl {
  width: 40%; font-weight: 600; background: #f8f9fa; color: #343a40;
}
.pdv-table td.val { width: 60%; }
/* highlight cursor */
[data-tip] { cursor: help; border-radius: 3px; }

/* legend badges */
.leg-sat { background:#d4edda; padding:2px 8px; border-radius:3px; margin-right:5px; font-size:12px; }
.leg-vio { background:#fde8e8; padding:2px 8px; border-radius:3px; margin-right:5px; font-size:12px; }
.leg-rsk { background:#fff3cd; padding:2px 8px; border-radius:3px; margin-right:5px; font-size:12px; }
.leg-na  { background:#e8f0fe; padding:2px 8px; border-radius:3px; margin-right:5px; font-size:12px; }

/* NOT_ADDRESSED / Risk / Conditions panels */
.na-panel  { border:1px solid #b8d0fb; border-radius:6px; background:#f0f4ff; padding:12px 16px; margin-top:14px; }
.na-title  { font-weight:700; font-size:13px; color:#0a3577; margin-bottom:7px; }
.na-item   { font-size:13px; color:#1a3a6b; padding:3px 0 3px 14px; position:relative; }
.na-item::before { content:"•"; position:absolute; left:0; color:#0d6efd; }
.risk-panel { border:1px solid #f5c04a; border-radius:6px; background:#fffbf0; padding:12px 16px; margin-top:12px; }
.risk-title { font-weight:700; font-size:13px; color:#7d4e00; margin-bottom:6px; }
.risk-item  { font-size:13px; color:#6b4000; padding:3px 0 3px 16px; position:relative; }
.risk-item::before { content:"⚠"; position:absolute; left:0; font-size:11px; }
.cond-panel { border:1px solid #b0e0c8; border-radius:6px; background:#f0fbf5; padding:12px 16px; margin-top:12px; }
.cond-title { font-weight:700; font-size:13px; color:#155724; margin-bottom:6px; }
.cond-item  { font-size:13px; color:#155724; padding:3px 0 3px 14px; position:relative; }
.cond-item::before { content:"›"; position:absolute; left:0; font-weight:900; }
</style>
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_highlight_map(requirements: list) -> list:
    """Returns [(relevant_text_lower, status, requirement, reason)] sorted by priority."""
    result = []
    for req in requirements:
        st = req.get("status", "NOT_ADDRESSED")
        rt = (req.get("relevant_text") or "").strip()
        if not rt or st == "NOT_ADDRESSED":
            continue
        result.append((rt.lower(), st, req.get("requirement", ""), req.get("reason", "")))
    result.sort(key=lambda x: _STATUS_PRIORITY.get(x[1], 99))
    return result


def _match_highlight(text: str, highlight_map: list):
    """Return (status, requirement, reason) if text matches any highlight, else None."""
    if not text or len(text.strip()) < 4:
        return None
    t = text.lower().strip()
    for hl_text, st, req, reason in highlight_map:
        if t in hl_text:
            return (st, req, reason)
        # Word-overlap fallback: ≥60% of meaningful words in common
        t_words = set(re.findall(r'\w{4,}', t))
        h_words = set(re.findall(r'\w{4,}', hl_text))
        if t_words and h_words and len(t_words & h_words) / len(t_words) >= 0.60:
            return (st, req, reason)
    return None


def _tip_attr(status: str, requirement: str, reason: str) -> str:
    """Build the data-tip HTML string (used as attribute value — safe HTML inside JS innerHTML)."""
    badge_map = {
        "SATISFIES": ("&#x2714;", "#28a745"),
        "VIOLATES":  ("&#x2716;", "#dc3545"),
        "RISKY":     ("&#x26A0;", "#fd7e14"),
    }
    icon, color = badge_map.get(status, ("&#x2022;", "#6c757d"))
    req_s    = _esc(requirement)
    reason_s = _esc(reason)
    label    = status.replace("_", " ").title()
    tip_html = (
        f'<span style="background:{color};color:#fff;padding:1px 7px;border-radius:10px;'
        f'font-size:11px;font-weight:700;letter-spacing:.04em;">{icon} {label}</span>'
        f'<br><strong style="font-size:12px;">{req_s}</strong>'
        + (f'<br><span style="opacity:.85;font-size:11px;">{reason_s}</span>' if reason_s else "")
    )
    # Escape for use as HTML attribute value (single-quoted on the element)
    return tip_html.replace("'", "&#39;")


# ── DOCX → highlighted HTML ────────────────────────────────────────────────────

def _docx_to_html(doc_bytes: bytes, highlight_map: list) -> str:
    """
    Convert a DOCX file to HTML preserving paragraphs AND tables in document order.
    Applies inline background highlights and data-tip attributes where text matches
    a policy requirement.
    """
    try:
        from docx import Document as DocxDocument
        from docx.text.paragraph import Paragraph as DocxPara
        from docx.table import Table as DocxTable
    except ImportError:
        return "<p><em>python-docx not available for document rendering.</em></p>"

    doc   = DocxDocument(io.BytesIO(doc_bytes))
    parts = ['<div class="pdv-wrap">']

    for child in doc.element.body:
        raw_tag = child.tag
        tag     = raw_tag.split("}")[-1] if "}" in raw_tag else raw_tag

        # ── Paragraph ────────────────────────────────────────────────────────
        if tag == "p":
            para = DocxPara(child, doc)
            text = para.text.strip()
            if not text:
                continue

            esc_text = _esc(text)
            is_heading = bool(
                re.match(r"^(\d+\.\s|\bExhibit\b|[IVXLC]+\.\s)", text, re.I)
                or (len(text) < 80 and text == text.upper() and len(text.split()) > 1)
            )

            match = _match_highlight(text, highlight_map)
            if is_heading:
                tag_open = f'<div class="pdv-heading'
                if match:
                    bg  = _STATUS_BG.get(match[0], "")
                    tip = _tip_attr(*match)
                    tag_open += f'" style="background-color:{bg};" data-tip=\'{tip}\''
                else:
                    tag_open += '"'
                parts.append(f'{tag_open}>{esc_text}</div>')
            else:
                if match:
                    bg  = _STATUS_BG.get(match[0], "")
                    tip = _tip_attr(*match)
                    parts.append(
                        f'<p class="pdv-para" style="background-color:{bg};" data-tip=\'{tip}\'>'
                        f'{esc_text}</p>'
                    )
                else:
                    parts.append(f'<p class="pdv-para">{esc_text}</p>')

        # ── Table ─────────────────────────────────────────────────────────────
        elif tag == "tbl":
            table = DocxTable(child, doc)
            parts.append('<table class="pdv-table">')

            for row in table.rows:
                # Deduplicate merged cells (python-docx repeats merged cell text)
                seen_vals  = []
                seen_set   = set()
                for cell in row.cells:
                    cv = cell.text.strip()
                    if cv not in seen_set:
                        seen_vals.append(cv)
                        seen_set.add(cv)

                if not any(seen_vals):
                    continue

                # Determine highlight: check full row text against highlight map
                row_text = " | ".join(seen_vals)
                match    = _match_highlight(row_text, highlight_map)
                # Also try each individual cell
                if not match:
                    for cv in seen_vals:
                        match = _match_highlight(cv, highlight_map)
                        if match:
                            break

                bg_style  = f"background-color:{_STATUS_BG[match[0]]};" if match and _STATUS_BG.get(match[0]) else ""
                tip_attr  = f" data-tip='{_tip_attr(*match)}'" if match else ""

                parts.append("<tr>")
                for ci, cv in enumerate(seen_vals):
                    esc_cv = _esc(cv)
                    if ci == 0:
                        # Label cell
                        parts.append(f'<td class="lbl" style="{bg_style}"{tip_attr}>{esc_cv}</td>')
                    else:
                        # Value cell
                        parts.append(f'<td class="val" style="{bg_style}"{tip_attr}>{esc_cv}</td>')
                parts.append("</tr>")

            parts.append("</table>")

    parts.append("</div>")
    return "\n".join(parts)


def _md_strip(text: str) -> str:
    """Strip Markdown syntax to get plain text for highlight matching."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*",     r"\1", text)
    text = re.sub(r"\\(.)",           r"\1", text)
    return text.strip()


def _md_inline(text: str) -> str:
    """Escape HTML then apply inline Markdown (bold, italic, escape sequences)."""
    # Strip markdown escape backslashes first
    text = re.sub(r"\\(.)", r"\1", text)
    # HTML-escape
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return text


def _is_md_separator(row: str) -> bool:
    """Return True for Markdown table separator rows like |---|---| or |  |  |."""
    cells = [c.strip() for c in row.split("|") if c.strip()]
    if not cells:
        return True
    return all(re.match(r"^[-:\s]*$", c) for c in cells)


def _markdown_to_html(md_text: str, highlight_map: list) -> str:
    """
    Convert Markdown document to HTML, preserving tables and bold text,
    and applying inline background highlights where text matches a policy requirement.
    """
    parts = ['<div class="pdv-wrap">']
    lines = md_text.split("\n")
    i = 0

    while i < len(lines):
        raw     = lines[i]
        stripped = raw.strip()
        i += 1

        if not stripped:
            continue

        # ── Markdown table block ─────────────────────────────────────────────
        if stripped.startswith("|"):
            table_lines = [stripped]
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1

            # Filter separator rows
            data_rows = [l for l in table_lines if not _is_md_separator(l)]
            if not data_rows:
                continue

            parts.append('<table class="pdv-table">')
            for row_line in data_rows:
                cells_raw = [c.strip() for c in row_line.split("|")]
                # Remove empty strings caused by leading/trailing |
                cells_raw = [c for c in cells_raw if c]
                if not cells_raw:
                    continue

                # Plain text of entire row for matching
                row_plain = " | ".join(_md_strip(c) for c in cells_raw)
                match = _match_highlight(row_plain, highlight_map)
                if not match:
                    for c in cells_raw:
                        match = _match_highlight(_md_strip(c), highlight_map)
                        if match:
                            break

                bg_style = f"background-color:{_STATUS_BG[match[0]]};" if match and _STATUS_BG.get(match[0]) else ""
                tip_attr = f" data-tip='{_tip_attr(*match)}'" if match else ""

                parts.append("<tr>")
                for ci, cell_raw in enumerate(cells_raw):
                    cell_html = _md_inline(cell_raw)
                    cls       = "lbl" if ci == 0 else "val"
                    parts.append(f'<td class="{cls}" style="{bg_style}"{tip_attr}>{cell_html}</td>')
                parts.append("</tr>")

            parts.append("</table>")
            continue

        # ── Heading-style bold line: **N. Section** or all-caps title ────────
        plain = _md_strip(stripped)
        is_title   = bool(re.match(r"^(YOUR |HOUSING |FOR OFFICE)", plain, re.I))
        is_heading = (
            bool(re.match(r"^\*\*\d+\.", stripped))          # **1. Section**
            or bool(re.match(r"^\*\*[A-Z][^*]{2,}\*\*$", stripped))  # **ALL CAPS TITLE**
            or stripped.startswith("#")
        )

        inline_html = _md_inline(stripped)
        match       = _match_highlight(plain, highlight_map)
        bg_style    = f"background-color:{_STATUS_BG[match[0]]};" if match and _STATUS_BG.get(match[0]) else ""
        tip_attr    = f" data-tip='{_tip_attr(*match)}'" if match else ""

        if is_title:
            parts.append(f'<div class="pdv-title" style="{bg_style}"{tip_attr}>{inline_html}</div>')
        elif is_heading:
            parts.append(f'<div class="pdv-heading" style="{bg_style}"{tip_attr}>{inline_html}</div>')
        else:
            parts.append(f'<p class="pdv-para" style="{bg_style}"{tip_attr}>{inline_html}</p>')

    parts.append("</div>")
    return "\n".join(parts)


def _plain_text_to_html(document_text: str, highlight_map: list) -> str:
    """Render plain text with highlights (fallback when no DOCX bytes)."""
    parts = ['<div class="pdv-wrap">']
    for raw in document_text.split("\n"):
        text = raw.strip()
        if not text:
            continue
        esc_text = _esc(text)
        if "\t" in text or " | " in text:
            sep  = "\t" if "\t" in text else " | "
            segs = text.split(sep, 1)
            lbl  = _esc(segs[0].strip())
            val  = _esc(segs[1].strip()) if len(segs) > 1 else ""
            match = _match_highlight(text, highlight_map)
            bg    = f"background-color:{_STATUS_BG[match[0]]};" if match and _STATUS_BG.get(match[0]) else ""
            tip   = f" data-tip='{_tip_attr(*match)}'" if match else ""
            parts.append(
                f'<table class="pdv-table"><tr>'
                f'<td class="lbl" style="{bg}"{tip}>{lbl}</td>'
                f'<td class="val" style="{bg}"{tip}>{val}</td>'
                f'</tr></table>'
            )
        elif re.match(r"^(\d+\.\s|\bExhibit\b|[IVXLC]+\.\s)", text, re.I) or (len(text) < 80 and text == text.upper()):
            match = _match_highlight(text, highlight_map)
            bg    = f"background-color:{_STATUS_BG[match[0]]};" if match and _STATUS_BG.get(match[0]) else ""
            tip   = f" data-tip='{_tip_attr(*match)}'" if match else ""
            parts.append(f'<div class="pdv-heading" style="{bg}"{tip}>{esc_text}</div>')
        else:
            match = _match_highlight(text, highlight_map)
            bg    = f"background-color:{_STATUS_BG[match[0]]};" if match and _STATUS_BG.get(match[0]) else ""
            tip   = f" data-tip='{_tip_attr(*match)}'" if match else ""
            parts.append(f'<p class="pdv-para" style="{bg}"{tip}>{esc_text}</p>')
    parts.append("</div>")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════════
#  POLICY REPORT — document-as-is with inline compliance highlights
# ══════════════════════════════════════════════════════════════════════════════════

def generate_policy_report(
    policy_analysis: dict,
    *,
    policy_type: str = "",
    agreement_meta: dict = None,
    document_text: str = None,
    doc_bytes: bytes = None,
    file_type: str = None,
) -> str:
    agreement_meta = agreement_meta or {}
    reqs      = policy_analysis.get("policy_requirements", [])
    verdict   = policy_analysis.get("overall_verdict", "PARTIALLY_COMPLIANT")
    rec       = policy_analysis.get("approval_recommendation", "CONDITIONAL_APPROVE")
    score     = policy_analysis.get("compliance_score", 0)
    summary   = policy_analysis.get("summary", "")
    agmt_type = agreement_meta.get("agreement_type", "")

    verdict_color = {
        "COMPLIANT":           "#28a745",
        "NON_COMPLIANT":       "#dc3545",
        "PARTIALLY_COMPLIANT": "#fd7e14",
    }.get(verdict, "#fd7e14")

    rec_label = {
        "APPROVE":             "Approve",
        "REJECT":              "Reject",
        "CONDITIONAL_APPROVE": "Conditional Approve",
    }.get(rec, rec)

    verdict_label = {
        "COMPLIANT":           "Compliant",
        "NON_COMPLIANT":       "Non-Compliant",
        "PARTIALLY_COMPLIANT": "Partially Compliant",
    }.get(verdict, verdict)

    hl_map = _build_highlight_map(reqs)
    lines  = [_MD_DIFF_CSS, _DOC_CSS]

    # ── Header ─────────────────────────────────────────────────────────────────
    label = policy_type or agmt_type
    header_parts = ["<strong>Policy Compliance Report</strong>"]
    if label:
        header_parts.append(f'<span style="color:#6c757d;font-size:13px;">{_esc(label)}</span>')
    header_parts.append(
        f'<span style="color:{verdict_color};font-weight:700;">{verdict_label}</span>'
        f"&nbsp;·&nbsp;"
        f'<span style="font-weight:600;">Recommendation: {rec_label}</span>'
        f"&nbsp;·&nbsp;"
        f'<span style="color:{verdict_color};font-weight:700;">{score}%</span>'
    )
    lines.append(
        '<div class="diff-group" data-type="unchanged">'
        '<div class="diff-line normal">'
        '<div class="line-content" style="padding:10px 8px;border-bottom:1px solid #e9ecef;">'
        + " &nbsp;·&nbsp; ".join(header_parts)
        + "</div></div></div>"
    )

    if summary:
        lines.append(
            '<div class="diff-group" data-type="unchanged">'
            '<div class="diff-line normal">'
            '<div class="line-content" style="font-size:13px;color:#495057;padding:8px;">'
            f"{_esc(summary)}"
            "</div></div></div>"
        )

    # ── Legend ─────────────────────────────────────────────────────────────────
    lines.append(
        '<div class="diff-group" data-type="unchanged">'
        '<div class="diff-line normal">'
        '<div class="line-content" style="padding:5px 8px;font-size:12px;color:#6c757d;">'
        '<span class="leg-sat">Satisfies</span>'
        '<span class="leg-vio">Violates</span>'
        '<span class="leg-rsk">Risky</span>'
        '<span class="leg-na">Not Addressed (see below)</span>'
        "&nbsp; Hover highlighted sections for details."
        "</div></div></div>"
    )

    # ── Document body ──────────────────────────────────────────────────────────
    if doc_bytes and file_type == "docx":
        doc_html = _docx_to_html(doc_bytes, hl_map)
    elif document_text and file_type in ("markdown", "txt", None):
        # Check if it looks like Markdown (has | table rows or ** bold)
        if "|" in document_text or "**" in document_text:
            doc_html = _markdown_to_html(document_text, hl_map)
        else:
            doc_html = _plain_text_to_html(document_text, hl_map)
    elif document_text:
        doc_html = _plain_text_to_html(document_text, hl_map)
    else:
        doc_html = "<p><em>No document content available.</em></p>"

    lines.append(
        '<div class="diff-group" data-type="unchanged">'
        '<div class="diff-line normal">'
        '<div class="line-content" style="padding:10px 8px;">'
        + doc_html
        + "</div></div></div>"
    )

    # ── NOT_ADDRESSED panel ────────────────────────────────────────────────────
    na_reqs = [r for r in reqs if r.get("status") == "NOT_ADDRESSED"]
    if na_reqs:
        items = "".join(
            f'<div class="na-item"><strong>{_esc(r.get("requirement",""))}</strong>'
            + (f' — {_esc(r.get("reason",""))}' if r.get("reason") else "")
            + (f'<br><em style="font-size:12px;color:#4060a0;">{_esc(r.get("recommendation",""))}</em>' if r.get("recommendation") else "")
            + "</div>"
            for r in na_reqs
        )
        lines.append(
            '<div class="diff-group" data-type="unchanged">'
            '<div class="diff-line normal">'
            '<div class="line-content" style="padding:6px 8px;">'
            f'<div class="na-panel"><div class="na-title">Not Addressed in Document ({len(na_reqs)})</div>{items}</div>'
            "</div></div></div>"
        )

    # ── Risk points ────────────────────────────────────────────────────────────
    risk_points = policy_analysis.get("risk_points", [])
    if risk_points:
        items = "".join(f'<div class="risk-item">{_esc(rp)}</div>' for rp in risk_points)
        lines.append(
            '<div class="diff-group" data-type="unchanged">'
            '<div class="diff-line normal">'
            '<div class="line-content" style="padding:6px 8px;">'
            f'<div class="risk-panel"><div class="risk-title">Risk Points</div>{items}</div>'
            "</div></div></div>"
        )

    # ── Conditions for approval ────────────────────────────────────────────────
    conditions = policy_analysis.get("conditions", [])
    if conditions:
        items = "".join(f'<div class="cond-item">{_esc(c)}</div>' for c in conditions)
        lines.append(
            '<div class="diff-group" data-type="unchanged">'
            '<div class="diff-line normal">'
            '<div class="line-content" style="padding:6px 8px;">'
            f'<div class="cond-panel"><div class="cond-title">Conditions for Approval</div>{items}</div>'
            "</div></div></div>"
        )

    # ── Global tooltip div + JS ────────────────────────────────────────────────
    lines.append(_TOOLTIP_JS)

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════════
#  LOAN OVERVIEW — CSS + helpers for enhanced summary sections
# ══════════════════════════════════════════════════════════════════════════════════

_LOAN_OVERVIEW_CSS = """\
<style>
/* ── Project header ── */
.lo-project-header { background:#fff; border-radius:12px; padding:22px 28px; margin-bottom:16px;
  box-shadow:0 1px 4px rgba(0,0,0,0.08); display:flex; justify-content:space-between; align-items:flex-start; }
.lo-project-name { font-size:22px; font-weight:800; color:#1a1a2e; margin-bottom:6px; }
.lo-project-meta { font-size:13px; color:#6c757d; display:flex; gap:16px; flex-wrap:wrap; }
.lo-loan-amount-box { background:#f0f4ff; border-radius:10px; padding:16px 24px; text-align:right; flex-shrink:0; }
.lo-loan-amount-label { font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:#6c757d; }
.lo-loan-amount-value { font-size:28px; font-weight:800; color:#1a3a8f; }
.lo-loan-amount-sub { font-size:12px; color:#6c757d; margin-top:2px; }
/* ── Metric tiles ── */
.lo-metric-row { display:flex; gap:12px; margin-bottom:16px; }
.lo-metric { flex:1; background:#fff; border-radius:10px; padding:16px 14px; text-align:center;
  box-shadow:0 1px 4px rgba(0,0,0,0.08); border-top:3px solid transparent; }
.lo-metric.sat  { border-top-color:#28a745; }
.lo-metric.viol { border-top-color:#dc3545; }
.lo-metric.risky{ border-top-color:#fd7e14; }
.lo-metric.neutral{ border-top-color:#adb5bd; }
.lo-metric-value { font-size:22px; font-weight:800; line-height:1.1; }
.lo-metric.sat  .lo-metric-value { color:#28a745; }
.lo-metric.viol .lo-metric-value { color:#dc3545; }
.lo-metric.risky .lo-metric-value { color:#fd7e14; }
.lo-metric.neutral .lo-metric-value { color:#495057; }
.lo-metric-label { font-size:9px; font-weight:700; letter-spacing:.07em; text-transform:uppercase; color:#6c757d; margin-top:5px; }
.lo-metric-limit { font-size:11px; color:#adb5bd; margin-top:3px; }
/* ── Four-table grid ── */
.lo-tables-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px; }
.lo-table-card { background:#fff; border-radius:10px; padding:18px 20px; box-shadow:0 1px 4px rgba(0,0,0,0.08); }
.lo-table-title { font-size:12px; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  margin-bottom:14px; padding-left:10px; border-left:3px solid; }
.lo-table-title.fin   { color:#1a3a8f; border-left-color:#1a3a8f; }
.lo-table-title.terms { color:#0d6efd; border-left-color:#0d6efd; }
.lo-table-title.afford{ color:#28a745; border-left-color:#28a745; }
.lo-table-title.comp  { color:#fd7e14; border-left-color:#fd7e14; }
.lo-kv-row { display:flex; justify-content:space-between; align-items:center; padding:7px 0;
  border-bottom:1px solid #f0f0f0; font-size:13px; }
.lo-kv-row:last-child { border-bottom:none; }
.lo-kv-key { color:#6c757d; }
.lo-kv-val { font-weight:700; color:#1a1a2e; }
.lo-status-badge { font-size:11px; padding:2px 8px; border-radius:10px; font-weight:700; }
.lo-status-badge.ok  { background:#d4edda; color:#155724; }
.lo-status-badge.bad { background:#fde8e8; color:#7b0d14; }
.lo-status-badge.warn{ background:#fff3cd; color:#7d4e00; }
/* ── Radar chart ── */
.lo-radar-card { background:#fff; border-radius:10px; padding:20px 24px; box-shadow:0 1px 4px rgba(0,0,0,0.08); margin-bottom:20px; }
.lo-radar-wrap { display:flex; align-items:center; gap:32px; flex-wrap:wrap; }
.lo-radar-legend { flex:1; min-width:200px; }
.lo-radar-legend-item { display:flex; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid #f0f0f0; font-size:13px; }
.lo-radar-legend-item:last-child { border-bottom:none; }
.lo-radar-legend-dot { width:12px; height:12px; border-radius:50%; flex-shrink:0; }
.lo-radar-legend-label { color:#495057; flex:1; }
.lo-radar-legend-score { font-weight:800; }
/* ── Grouped Scorecard ── */
.lo-grouped-card { background:#fff; border-radius:10px; padding:20px 24px; box-shadow:0 1px 4px rgba(0,0,0,0.08); margin-bottom:20px; }
.lo-group-row { padding:12px 0; border-bottom:1px solid #f0f0f0; }
.lo-group-row:last-child { border-bottom:none; }
.lo-group-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
.lo-group-name { font-weight:700; font-size:13px; color:#1a1a2e; }
.lo-group-pct { font-weight:800; font-size:14px; }
.lo-group-bar-wrap { background:#e9ecef; border-radius:4px; height:6px; overflow:hidden; }
.lo-group-bar { height:6px; border-radius:4px; }
.lo-group-meta { font-size:11px; color:#adb5bd; margin-top:4px; }
/* ── Path to Approval ── */
.lo-path-card { background:#fff; border-radius:10px; padding:20px 24px; box-shadow:0 1px 4px rgba(0,0,0,0.08); margin-bottom:20px; }
.lo-path-top { display:flex; gap:20px; align-items:center; margin-bottom:18px; }
.lo-path-score-box { background:#f8f9fa; border-radius:8px; padding:14px 20px; text-align:center; flex-shrink:0; min-width:90px; }
.lo-path-score-label { font-size:10px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; color:#6c757d; margin-bottom:4px; }
.lo-path-score-val { font-size:26px; font-weight:800; line-height:1; }
.lo-path-arrow { font-size:22px; color:#ced4da; }
.lo-path-steps { flex:1; }
.lo-path-step { display:flex; gap:12px; align-items:flex-start; padding:8px 0; border-bottom:1px solid #f0f0f0; font-size:13px; }
.lo-path-step:last-child { border-bottom:none; }
.lo-path-step-num { width:22px; height:22px; border-radius:50%; background:#1a3a8f; color:#fff;
  font-size:11px; font-weight:700; display:flex; align-items:center; justify-content:center; flex-shrink:0; margin-top:1px; }
.lo-path-step-text { flex:1; line-height:1.5; color:#1a1a2e; }
.lo-path-step-badge { font-size:10px; padding:2px 7px; border-radius:10px; font-weight:700;
  background:#e8f0fe; color:#1a3a8f; flex-shrink:0; margin-top:3px; }
</style>
"""

# ── Loan category keyword map ──────────────────────────────────────────────────
_LOAN_CATEGORIES = [
    ("Financial",      ["dscr", "ltv", "ltc", "loan-to", "debt yield", "debt service", "leverage", "noi", "income", "apprais", "interest rate"]),
    ("Affordability",  ["ami", "affordable", "unit", "housing", "lihtc", "tax credit", "low-income", "income restrict", "occupancy", "tenant"]),
    ("Documentation",  ["environmental", "phase i", "document", "commitment", "recorded", "subordinate", "agreement", "letter", "evidence"]),
    ("Compliance",     ["comply", "compliance", "policy", "regulation", "legal", "lien", "title", "code", "statute", "permit"]),
    ("Risk & Reserves",["reserve", "sponsor", "experience", "guarantor", "construction", "risk", "contingency", "liquidity", "insurance", "escrow"]),
]


def _group_requirements(reqs: list) -> dict:
    """Group requirements by category using keyword matching."""
    groups: dict = {cat: [] for cat, _ in _LOAN_CATEGORIES}
    groups["Other"] = []
    for req in reqs:
        text = (req.get("requirement", "") + " " + req.get("reason", "")).lower()
        matched = False
        for cat, keywords in _LOAN_CATEGORIES:
            if any(kw in text for kw in keywords):
                groups[cat].append(req)
                matched = True
                break
        if not matched:
            groups["Other"].append(req)
    return {k: v for k, v in groups.items() if v}


def _category_score(reqs: list) -> int:
    if not reqs:
        return 0
    sat   = sum(1 for r in reqs if r.get("status") == "SATISFIES")
    risky = sum(1 for r in reqs if r.get("status") == "RISKY")
    return round((sat + risky * 0.5) / len(reqs) * 100)


def _score_color(score: int) -> str:
    if score >= 80: return "#28a745"
    if score >= 50: return "#fd7e14"
    return "#dc3545"


def _build_radar_svg(category_scores: dict) -> str:
    """Generate a pure SVG radar/spider chart for given category scores (0-100)."""
    cats = list(category_scores.items())
    n = len(cats)
    if n < 3:
        return ""
    cx, cy, r = 200, 200, 130

    def pt(i: int, pct: float):
        a = math.radians(i * 360 / n - 90)
        return cx + r * pct * math.cos(a), cy + r * pct * math.sin(a)

    grid  = "".join(
        f'<polygon points="{" ".join(f"{pt(i,p)[0]:.1f},{pt(i,p)[1]:.1f}" for i in range(n))}" fill="none" stroke="#e9ecef" stroke-width="1"/>'
        for p in [0.25, 0.5, 0.75, 1.0]
    )
    axes  = "".join(
        f'<line x1="{cx}" y1="{cy}" x2="{pt(i,1.0)[0]:.1f}" y2="{pt(i,1.0)[1]:.1f}" stroke="#e9ecef" stroke-width="1"/>'
        for i in range(n)
    )
    dpts  = " ".join(f"{pt(i, cats[i][1]/100)[0]:.1f},{pt(i, cats[i][1]/100)[1]:.1f}" for i in range(n))
    data  = f'<polygon points="{dpts}" fill="rgba(13,110,253,0.15)" stroke="#0d6efd" stroke-width="2.5" stroke-linejoin="round"/>'
    dots  = ""
    lbls  = ""
    for i, (cat, score) in enumerate(cats):
        px, py = pt(i, score / 100)
        color  = _score_color(score)
        dots  += f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{color}" stroke="#fff" stroke-width="2"/>'
        lx, ly = pt(i, 1.28)
        anchor = "middle"
        if lx < cx - 15: anchor = "end"
        elif lx > cx + 15: anchor = "start"
        lbls += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-size="11" font-weight="700" fill="#1a1a2e" font-family="Segoe UI,sans-serif">{_esc(cat)}</text>'
            f'<text x="{lx:.1f}" y="{ly+14:.1f}" text-anchor="{anchor}" font-size="10" font-weight="700" fill="{color}" font-family="Segoe UI,sans-serif">{score}%</text>'
        )
    return f'<svg width="400" height="400" viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg">{grid}{axes}{data}{dots}{lbls}</svg>'


def _build_loan_overview_html(loan_metrics: dict) -> str:
    """Project header + key metric tiles + 2x2 detail table grid."""
    if not loan_metrics:
        return ""
    h = []

    # Project header
    pname  = loan_metrics.get("project_name", "")
    lamount = loan_metrics.get("loan_amount", "")
    lrate  = loan_metrics.get("loan_rate", "")
    lterm  = loan_metrics.get("loan_term", "")
    ltype  = loan_metrics.get("loan_type", "")
    meta   = " ".join(filter(None, [
        f"📍 {_esc(str(loan_metrics['location']))}" if loan_metrics.get("location") else "",
        f"🏢 {_esc(str(loan_metrics['sponsor']))}" if loan_metrics.get("sponsor") else "",
        (f"🏠 {_esc(str(loan_metrics.get('total_units',''))) } units · {_esc(str(loan_metrics.get('affordable_units','')))} affordable") if loan_metrics.get("total_units") else "",
    ]))
    if pname or lamount:
        sub = " · ".join(filter(None, [lrate, lterm, ltype]))
        h.append('<div class="lo-project-header">')
        h.append('<div>')
        if pname: h.append(f'<div class="lo-project-name">{_esc(str(pname))}</div>')
        if meta:  h.append(f'<div class="lo-project-meta"><span>{meta}</span></div>')
        h.append('</div>')
        if lamount:
            h.append('<div class="lo-loan-amount-box">')
            h.append('<div class="lo-loan-amount-label">Loan Amount</div>')
            h.append(f'<div class="lo-loan-amount-value">{_esc(str(lamount))}</div>')
            if sub: h.append(f'<div class="lo-loan-amount-sub">{_esc(sub)}</div>')
            h.append('</div>')
        h.append('</div>')

    # Metric tiles
    tiles_def = [
        ("dscr",       "DSCR",       "min"),
        ("ltv",        "LTV",        "max"),
        ("ltc",        "LTC",        "max"),
        ("debt_yield", "Debt Yield", "min"),
        ("ami_level",  "AMI Level",  "required"),
        ("exceptions", "Exceptions", None),
    ]
    tiles = []
    for key, label, limit_field in tiles_def:
        data = loan_metrics.get(key)
        if data is None:
            continue
        if key == "exceptions" and isinstance(data, dict):
            count = data.get("count", "")
            total_chk = data.get("total_checks", "")
            val_str   = str(count)
            limit_str = f"of {total_chk} checks" if total_chk else ""
            css_cls   = "viol" if int(count or 0) > 0 else "sat"
        elif isinstance(data, dict):
            val_str   = str(data.get("value", ""))
            status    = data.get("status", "")
            css_cls   = {"SATISFIES": "sat", "VIOLATES": "viol", "RISKY": "risky"}.get(status, "neutral")
            lval      = data.get(limit_field, "") if limit_field else ""
            limit_str = f"{limit_field.title()} {_esc(str(lval))}" if lval else ""
        else:
            val_str, css_cls, limit_str = str(data), "neutral", ""
        tiles.append(
            f'<div class="lo-metric {css_cls}">'
            f'<div class="lo-metric-value">{_esc(val_str)}</div>'
            f'<div class="lo-metric-label">{_esc(label)}</div>'
            + (f'<div class="lo-metric-limit">{limit_str}</div>' if limit_str else "")
            + '</div>'
        )
    if tiles:
        h.append('<div class="lo-metric-row">' + "".join(tiles) + '</div>')

    # Helper for key-value rows
    def kv(lbl: str, val, badge: str = None) -> str:
        if val is None or val == "": return ""
        v = _esc(str(val))
        v_html = f'<span class="lo-status-badge {badge}">{v}</span>' if badge else f'<span class="lo-kv-val">{v}</span>'
        return f'<div class="lo-kv-row"><span class="lo-kv-key">{_esc(lbl)}</span>{v_html}</div>'

    def badge_cls(val: str) -> str:
        v = str(val).lower()
        if any(x in v for x in ["pending", "not yet", "missing", "undocumented", "no ", "n/a"]): return "bad"
        if any(x in v for x in ["partial", "conditional", "review", "min"]): return "warn"
        return "ok"

    lm = loan_metrics
    h.append('<div class="lo-tables-grid">')

    fin = "".join(filter(None, [
        kv("Loan Amount",        lm.get("loan_amount")),
        kv("Appraised Value",    lm.get("appraised_value")),
        kv("Total Project Cost", lm.get("total_project_cost")),
        kv("Net Operating Income",lm.get("noi")),
        kv("Annual Debt Service", lm.get("annual_debt_service")),
    ]))
    if fin: h.append(f'<div class="lo-table-card"><div class="lo-table-title fin">Project Financials</div>{fin}</div>')

    trm = "".join(filter(None, [
        kv("Loan Type",    lm.get("loan_type")),
        kv("Purpose",     lm.get("loan_purpose")),
        kv("Interest Rate",lm.get("loan_rate")),
        kv("Term",        lm.get("loan_term")),
        kv("Amortization",lm.get("amortization")),
    ]))
    if trm: h.append(f'<div class="lo-table-card"><div class="lo-table-title terms">Loan Terms</div>{trm}</div>')

    aff = "".join(filter(None, [
        kv("Total Units",     lm.get("total_units")),
        kv("Affordable Units",lm.get("affordable_units")),
        kv("Affordability",  lm.get("affordability_pct")),
        kv("AMI Restriction",lm.get("ami_restriction")),
    ]))
    if aff: h.append(f'<div class="lo-table-card"><div class="lo-table-title afford">Affordable Housing</div>{aff}</div>')

    comp_items = [
        kv("Env. Clearance",   lm.get("env_clearance"),    badge_cls(str(lm.get("env_clearance","")))   if lm.get("env_clearance")   else None),
        kv("Tax Credit",       lm.get("tax_credit"),       badge_cls(str(lm.get("tax_credit","")))       if lm.get("tax_credit")       else None),
        kv("Subordinate Debt", lm.get("subordinate_debt"), badge_cls(str(lm.get("subordinate_debt",""))) if lm.get("subordinate_debt") else None),
        kv("Reserves",         lm.get("reserves"),         badge_cls(str(lm.get("reserves","")))         if lm.get("reserves")         else None),
    ]
    comp = "".join(filter(None, comp_items))
    if comp: h.append(f'<div class="lo-table-card"><div class="lo-table-title comp">Compliance &amp; Docs</div>{comp}</div>')

    h.append('</div>')  # lo-tables-grid
    return "\n".join(h)


def _build_grouped_scorecard_html(reqs: list) -> str:
    """Compliance score bars grouped by policy category."""
    if not reqs:
        return ""
    groups = _group_requirements(reqs)
    if not groups:
        return ""
    h = ['<div class="lo-grouped-card"><div class="sr-card-title">Compliance by Policy Category</div>']
    for cat, cat_reqs in groups.items():
        score = _category_score(cat_reqs)
        color = _score_color(score)
        sat   = sum(1 for r in cat_reqs if r.get("status") == "SATISFIES")
        viol  = sum(1 for r in cat_reqs if r.get("status") == "VIOLATES")
        risky = sum(1 for r in cat_reqs if r.get("status") == "RISKY")
        na    = sum(1 for r in cat_reqs if r.get("status") == "NOT_ADDRESSED")
        parts = ([f"{sat} ✓"] if sat else []) + ([f"{risky} ⚠"] if risky else []) + ([f"{viol} ✗"] if viol else []) + ([f"{na} –"] if na else [])
        h.append(
            f'<div class="lo-group-row">'
            f'<div class="lo-group-header"><span class="lo-group-name">{_esc(cat)}</span>'
            f'<span class="lo-group-pct" style="color:{color}">{score}%</span></div>'
            f'<div class="lo-group-bar-wrap"><div class="lo-group-bar" style="width:{score}%;background:{color};"></div></div>'
            f'<div class="lo-group-meta">{len(cat_reqs)} requirements · {" · ".join(parts)}</div>'
            f'</div>'
        )
    h.append('</div>')
    return "\n".join(h)


def _build_risk_radar_html(reqs: list) -> str:
    """Risk profile radar chart card."""
    if not reqs:
        return ""
    groups = _group_requirements(reqs)
    if len(groups) < 3:
        return ""
    cat_scores = {cat: _category_score(cat_reqs) for cat, cat_reqs in groups.items()}
    svg = _build_radar_svg(cat_scores)
    if not svg:
        return ""
    legend = "".join(
        f'<div class="lo-radar-legend-item">'
        f'<div class="lo-radar-legend-dot" style="background:{_score_color(score)}"></div>'
        f'<span class="lo-radar-legend-label">{_esc(cat)}</span>'
        f'<span class="lo-radar-legend-score" style="color:{_score_color(score)}">{score}%</span>'
        f'</div>'
        for cat, score in cat_scores.items()
    )
    return (
        '<div class="lo-radar-card">'
        '<div class="sr-card-title">Risk Profile Radar</div>'
        '<div class="lo-radar-wrap">'
        + svg
        + f'<div class="lo-radar-legend">{legend}</div>'
        + '</div></div>'
    )


def _build_path_to_approval_html(reqs: list, current_score: int, conditions: list) -> str:
    """Path to approval: score projection + numbered conditions."""
    if not reqs:
        return ""
    total    = len(reqs)
    sat      = sum(1 for r in reqs if r.get("status") == "SATISFIES")
    issues   = [r for r in reqs if r.get("status") in ("VIOLATES", "NOT_ADDRESSED")]
    proj_sat = sat + len(issues)
    proj     = round(proj_sat / total * 100)
    c_color  = _score_color(current_score)
    p_color  = _score_color(proj)
    steps    = conditions if conditions else [
        req.get("recommendation") or req.get("reason", "") or req.get("requirement", "")
        for req in issues[:6]
    ]
    steps_html = "".join(
        f'<div class="lo-path-step">'
        f'<div class="lo-path-step-num">{i}</div>'
        f'<div class="lo-path-step-text">{_esc(str(s)[:250])}</div>'
        f'<div class="lo-path-step-badge">Required</div>'
        f'</div>'
        for i, s in enumerate(steps, 1) if s
    )
    proj_label = "APPROVE" if proj >= 80 else "CONDITIONAL" if proj >= 50 else "STILL AT RISK"
    return (
        '<div class="lo-path-card">'
        '<div class="sr-card-title">Path to Approval</div>'
        '<div class="lo-path-top">'
        f'<div class="lo-path-score-box"><div class="lo-path-score-label">Current Score</div><div class="lo-path-score-val" style="color:{c_color}">{current_score}%</div></div>'
        '<div class="lo-path-arrow">&#8594;</div>'
        f'<div class="lo-path-score-box"><div class="lo-path-score-label">After Resolution</div><div class="lo-path-score-val" style="color:{p_color}">{proj}%</div><div style="font-size:10px;color:#6c757d;margin-top:2px">{proj_label}</div></div>'
        f'<div style="flex:1;font-size:13px;color:#6c757d;padding:8px 0">Resolving <strong>{len(issues)}</strong> exception{"s" if len(issues)!=1 else ""} would improve your compliance score by <strong style="color:{p_color}">{proj - current_score}pp</strong>.</div>'
        '</div>'
        + (f'<div class="lo-path-steps">{steps_html}</div>' if steps_html else "")
        + '</div>'
    )


# ══════════════════════════════════════════════════════════════════════════════════
#  POLICY REPORT JSON — structured document segments for frontend rendering
# ══════════════════════════════════════════════════════════════════════════════════

def _make_highlight_json(match) -> dict | None:
    """Convert a _match_highlight() result to a serialisable dict, or None."""
    if not match:
        return None
    status, requirement, reason = match
    bg_map = {
        "SATISFIES": {"bg_color": "#d4edda", "text_color": "#155724"},
        "VIOLATES":  {"bg_color": "#fde8e8", "text_color": "#7b0d14"},
        "RISKY":     {"bg_color": "#fff3cd", "text_color": "#7d4e00"},
    }
    colors = bg_map.get(status, {"bg_color": "#e8f0fe", "text_color": "#0a3577"})
    return {
        "status":        status,
        "status_label":  status.replace("_", " ").title(),
        "bg_color":      colors["bg_color"],
        "text_color":    colors["text_color"],
        "requirement":   requirement,
        "reason":        reason,
    }


def _parse_md_to_segments(md_text: str, highlight_map: list) -> list:
    """Parse Markdown document into structured segments with highlight annotations."""
    segments = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped:
            continue

        if stripped.startswith("|"):
            table_lines = [stripped]
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            data_rows = [l for l in table_lines if not _is_md_separator(l)]
            if not data_rows:
                continue
            rows = []
            for row_line in data_rows:
                cells_raw = [c.strip() for c in row_line.split("|") if c.strip()]
                if not cells_raw:
                    continue
                cells_plain = [_md_strip(c) for c in cells_raw]
                row_plain = " | ".join(cells_plain)
                match = _match_highlight(row_plain, highlight_map)
                if not match:
                    for cp in cells_plain:
                        match = _match_highlight(cp, highlight_map)
                        if match:
                            break
                rows.append({"cells": cells_plain, "highlight": _make_highlight_json(match)})
            segments.append({"type": "table", "rows": rows})
            continue

        plain = _md_strip(stripped)
        is_title   = bool(re.match(r"^(YOUR |HOUSING |FOR OFFICE)", plain, re.I))
        is_heading = (
            bool(re.match(r"^\*\*\d+\.", stripped))
            or bool(re.match(r"^\*\*[A-Z][^*]{2,}\*\*$", stripped))
            or stripped.startswith("#")
        )
        seg_type = "title" if is_title else "heading" if is_heading else "paragraph"
        match = _match_highlight(plain, highlight_map)
        segments.append({"type": seg_type, "text": plain, "highlight": _make_highlight_json(match)})
    return segments


def _parse_text_to_segments(document_text: str, highlight_map: list) -> list:
    """Parse plain text into structured segments with highlight annotations."""
    segments = []
    for raw in document_text.split("\n"):
        text = raw.strip()
        if not text:
            continue
        if "\t" in text or " | " in text:
            sep = "\t" if "\t" in text else " | "
            segs = text.split(sep, 1)
            cells = [segs[0].strip()] + ([segs[1].strip()] if len(segs) > 1 else [])
            match = _match_highlight(text, highlight_map)
            segments.append({"type": "table", "rows": [{"cells": cells, "highlight": _make_highlight_json(match)}]})
        elif re.match(r"^(\d+\.\s|\bExhibit\b|[IVXLC]+\.\s)", text, re.I) or (len(text) < 80 and text == text.upper()):
            match = _match_highlight(text, highlight_map)
            segments.append({"type": "heading", "text": text, "highlight": _make_highlight_json(match)})
        else:
            match = _match_highlight(text, highlight_map)
            segments.append({"type": "paragraph", "text": text, "highlight": _make_highlight_json(match)})
    return segments


def _parse_docx_to_segments(doc_bytes: bytes, highlight_map: list) -> list:
    """Parse DOCX into structured segments with highlight annotations."""
    try:
        from docx import Document as DocxDocument
        from docx.text.paragraph import Paragraph as DocxPara
        from docx.table import Table as DocxTable
    except ImportError:
        return []
    doc = DocxDocument(io.BytesIO(doc_bytes))
    segments = []
    for child in doc.element.body:
        raw_tag = child.tag
        tag = raw_tag.split("}")[-1] if "}" in raw_tag else raw_tag
        if tag == "p":
            para = DocxPara(child, doc)
            text = para.text.strip()
            if not text:
                continue
            is_heading = bool(
                re.match(r"^(\d+\.\s|\bExhibit\b|[IVXLC]+\.\s)", text, re.I)
                or (len(text) < 80 and text == text.upper() and len(text.split()) > 1)
            )
            match = _match_highlight(text, highlight_map)
            segments.append({"type": "heading" if is_heading else "paragraph", "text": text, "highlight": _make_highlight_json(match)})
        elif tag == "tbl":
            table = DocxTable(child, doc)
            rows = []
            for row in table.rows:
                seen_vals, seen_set = [], set()
                for cell in row.cells:
                    cv = cell.text.strip()
                    if cv not in seen_set:
                        seen_vals.append(cv)
                        seen_set.add(cv)
                if not any(seen_vals):
                    continue
                row_text = " | ".join(seen_vals)
                match = _match_highlight(row_text, highlight_map)
                if not match:
                    for cv in seen_vals:
                        match = _match_highlight(cv, highlight_map)
                        if match:
                            break
                rows.append({"cells": seen_vals, "highlight": _make_highlight_json(match)})
            segments.append({"type": "table", "rows": rows})
    return segments


def build_policy_report_json(
    policy_analysis: dict,
    *,
    policy_type: str = "",
    agreement_meta: dict = None,
    document_text: str = None,
    doc_bytes: bytes = None,
    file_type: str = None,
) -> dict:
    """
    Return all report data as a structured dict for frontend rendering.

    The frontend can use `document_segments` to build the annotated document view —
    each segment carries its text, type (title/heading/paragraph/table), and an
    optional `highlight` dict with status, colours, requirement text and reason.

    Returned as `policy_report_data` in the API response.
    """
    agreement_meta = agreement_meta or {}
    reqs    = policy_analysis.get("policy_requirements", [])
    verdict = policy_analysis.get("overall_verdict", "PARTIALLY_COMPLIANT")
    rec     = policy_analysis.get("approval_recommendation", "CONDITIONAL_APPROVE")
    score   = policy_analysis.get("compliance_score", 0)
    summary = policy_analysis.get("summary", "")

    verdict_color = {
        "COMPLIANT":           "#28a745",
        "NON_COMPLIANT":       "#dc3545",
        "PARTIALLY_COMPLIANT": "#fd7e14",
    }.get(verdict, "#fd7e14")

    verdict_label = {
        "COMPLIANT":           "Compliant",
        "NON_COMPLIANT":       "Non-Compliant",
        "PARTIALLY_COMPLIANT": "Partially Compliant",
    }.get(verdict, verdict)

    rec_label = {
        "APPROVE":             "Approve",
        "REJECT":              "Reject",
        "CONDITIONAL_APPROVE": "Conditional Approve",
    }.get(rec, rec)

    hl_map = _build_highlight_map(reqs)

    if doc_bytes and file_type == "docx":
        segments = _parse_docx_to_segments(doc_bytes, hl_map)
    elif document_text and ("|" in document_text or "**" in document_text):
        segments = _parse_md_to_segments(document_text, hl_map)
    elif document_text:
        segments = _parse_text_to_segments(document_text, hl_map)
    else:
        segments = []

    return {
        "header": {
            "policy_type":          policy_type,
            "agreement_type":       agreement_meta.get("agreement_type", ""),
            "verdict":              verdict,
            "verdict_label":        verdict_label,
            "verdict_color":        verdict_color,
            "score":                score,
            "score_color":          _score_color(score),
            "recommendation":       rec,
            "recommendation_label": rec_label,
            "summary":              summary,
        },
        "document_segments": segments,
        "not_addressed": [
            {
                "requirement":    r.get("requirement", ""),
                "reason":         r.get("reason", ""),
                "recommendation": r.get("recommendation", ""),
            }
            for r in reqs if r.get("status") == "NOT_ADDRESSED"
        ],
        "risk_points": policy_analysis.get("risk_points", []),
        "conditions":  policy_analysis.get("conditions", []),
        "legend": {
            "SATISFIES":     {"label": "Satisfies",     "bg_color": "#d4edda", "text_color": "#155724"},
            "VIOLATES":      {"label": "Violates",      "bg_color": "#fde8e8", "text_color": "#7b0d14"},
            "RISKY":         {"label": "Risky",         "bg_color": "#fff3cd", "text_color": "#7d4e00"},
            "NOT_ADDRESSED": {"label": "Not Addressed", "bg_color": "#e8f0fe", "text_color": "#0a3577"},
        },
    }


# ══════════════════════════════════════════════════════════════════════════════════
#  POLICY SUMMARY JSON — structured data for frontend consumption
# ══════════════════════════════════════════════════════════════════════════════════

def build_policy_summary_json(
    policy_analysis: dict,
    *,
    policy_type: str = "",
    jurisdiction_info: dict = None,
    agreement_meta: dict = None,
) -> dict:
    """
    Return all summary data as a structured dict suitable for JSON serialization.

    This exposes every field used by generate_policy_summary so the frontend
    can build a rich UI without parsing HTML.  Returned as `policy_summary_data`
    in the API response.
    """
    jurisdiction_info = jurisdiction_info or {}
    agreement_meta    = agreement_meta    or {}

    reqs     = policy_analysis.get("policy_requirements", [])
    total    = len(reqs)
    sat      = sum(1 for r in reqs if r.get("status") == "SATISFIES")
    viol     = sum(1 for r in reqs if r.get("status") == "VIOLATES")
    risky    = sum(1 for r in reqs if r.get("status") == "RISKY")
    not_addr = sum(1 for r in reqs if r.get("status") == "NOT_ADDRESSED")
    score    = policy_analysis.get("compliance_score", round(sat / total * 100) if total else 0)
    verdict  = policy_analysis.get("overall_verdict", "PARTIALLY_COMPLIANT")
    rec      = policy_analysis.get("approval_recommendation", "CONDITIONAL_APPROVE")
    conditions = policy_analysis.get("conditions", [])

    score_color = _score_color(score)
    verdict_label = {
        "COMPLIANT": "Compliant", "NON_COMPLIANT": "Non-Compliant",
        "PARTIALLY_COMPLIANT": "Partially Compliant",
    }.get(verdict, verdict)
    rec_label = {
        "APPROVE": "Approve", "REJECT": "Reject",
        "CONDITIONAL_APPROVE": "Conditional Approve",
    }.get(rec, rec)

    # ── Grouped categories ────────────────────────────────────────────────────
    groups = _group_requirements(reqs)
    grouped_categories = []
    for cat, cat_reqs in groups.items():
        cat_sat  = sum(1 for r in cat_reqs if r.get("status") == "SATISFIES")
        cat_viol = sum(1 for r in cat_reqs if r.get("status") == "VIOLATES")
        cat_risk = sum(1 for r in cat_reqs if r.get("status") == "RISKY")
        cat_na   = sum(1 for r in cat_reqs if r.get("status") == "NOT_ADDRESSED")
        cat_score = _category_score(cat_reqs)
        grouped_categories.append({
            "name":          cat,
            "score":         cat_score,
            "score_color":   _score_color(cat_score),
            "total":         len(cat_reqs),
            "satisfies":     cat_sat,
            "violates":      cat_viol,
            "risky":         cat_risk,
            "not_addressed": cat_na,
            "requirements":  cat_reqs,
        })

    # ── Path to approval ──────────────────────────────────────────────────────
    issues      = [r for r in reqs if r.get("status") in ("VIOLATES", "NOT_ADDRESSED")]
    proj_sat    = sat + len(issues)
    proj_score  = round(proj_sat / total * 100) if total else score
    proj_label  = "APPROVE" if proj_score >= 80 else "CONDITIONAL_APPROVE" if proj_score >= 50 else "REJECT"
    steps       = conditions if conditions else [
        r.get("recommendation") or r.get("reason", "") or r.get("requirement", "")
        for r in issues[:8] if r.get("recommendation") or r.get("reason")
    ]

    path_to_approval = {
        "current_score":         score,
        "current_score_color":   score_color,
        "projected_score":       proj_score,
        "projected_score_color": _score_color(proj_score),
        "improvement_pp":        proj_score - score,
        "exceptions_to_resolve": len(issues),
        "projected_verdict":     proj_label,
        "steps":                 [s for s in steps if s],
    }

    # ── Issues list (violations + risky + not addressed) ─────────────────────
    badge_labels = {
        "VIOLATES":      "Violation",
        "NOT_ADDRESSED": "Not Addressed",
        "RISKY":         "Risky",
    }
    issues_list = [
        {
            "requirement":  r.get("requirement", ""),
            "status":       r.get("status", ""),
            "status_label": badge_labels.get(r.get("status", ""), r.get("status", "")),
            "reason":       r.get("reason", ""),
            "recommendation": r.get("recommendation", ""),
            "relevant_text":  r.get("relevant_text"),
        }
        for r in reqs if r.get("status") in ("VIOLATES", "NOT_ADDRESSED", "RISKY")
    ]

    # ── Agreement meta (for display header) ───────────────────────────────────
    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}
    parties   = agreement_meta.get("parties") or {}

    return {
        # ── Top-level verdict & score ──────────────────────────────────────
        "score":               score,
        "score_color":         score_color,
        "overall_verdict":     verdict,
        "verdict_label":       verdict_label,
        "approval_recommendation": rec,
        "rec_label":           rec_label,
        "summary":             policy_analysis.get("summary", ""),
        "policy_type":         policy_type,

        # ── Counts ────────────────────────────────────────────────────────
        "stats": {
            "total":         total,
            "satisfies":     sat,
            "violates":      viol,
            "risky":         risky,
            "not_addressed": not_addr,
        },

        # ── All requirements (full detail) ────────────────────────────────
        "policy_requirements": reqs,

        # ── Issues only ───────────────────────────────────────────────────
        "issues": issues_list,

        # ── Risk & conditions ─────────────────────────────────────────────
        "risk_points": policy_analysis.get("risk_points", []),
        "conditions":  conditions,

        # ── Grouped category breakdown + radar data ───────────────────────
        "grouped_categories": grouped_categories,

        # ── Path to approval ──────────────────────────────────────────────
        "path_to_approval": path_to_approval,

        # ── Jurisdiction ──────────────────────────────────────────────────
        "jurisdiction": {
            "jurisdiction":    jurisdiction_info.get("jurisdiction", ""),
            "agreement_type":  jurisdiction_info.get("agreement_type", ""),
            "applicable_laws": jurisdiction_info.get("applicable_laws", []),
            "checklist":       jurisdiction_info.get("checklist", []),
        },

        # ── Agreement / client info ───────────────────────────────────────
        "agreement": {
            "type":         agmt_type,
            "date":         agmt_det.get("agreement_date", ""),
            "city":         agmt_det.get("city", ""),
            "state":        agmt_det.get("state", ""),
            "party_a":      (parties.get("landlord") or {}).get("name", ""),
            "party_b":      (parties.get("tenant") or {}).get("company_name")
                            or (parties.get("tenant") or {}).get("name", ""),
        },

        # ── Loan metrics (pass-through for frontend tiles) ────────────────
        "loan_metrics": agreement_meta.get("loan_metrics") or {},
    }


# ══════════════════════════════════════════════════════════════════════════════════
#  POLICY SUMMARY — analytics scorecard
# ══════════════════════════════════════════════════════════════════════════════════

def generate_policy_summary(
    policy_analysis: dict,
    *,
    policy_type: str = "",
    jurisdiction_info: dict = None,
    agreement_meta: dict = None,
) -> str:
    jurisdiction_info = jurisdiction_info or {}
    agreement_meta    = agreement_meta or {}

    reqs     = policy_analysis.get("policy_requirements", [])
    total    = len(reqs)
    sat      = sum(1 for r in reqs if r.get("status") == "SATISFIES")
    viol     = sum(1 for r in reqs if r.get("status") == "VIOLATES")
    risky    = sum(1 for r in reqs if r.get("status") == "RISKY")
    not_addr = sum(1 for r in reqs if r.get("status") == "NOT_ADDRESSED")
    score    = policy_analysis.get("compliance_score", round(sat / total * 100) if total else 0)
    verdict  = policy_analysis.get("overall_verdict", "PARTIALLY_COMPLIANT")
    rec      = policy_analysis.get("approval_recommendation", "CONDITIONAL_APPROVE")

    score_color   = "#28a745" if score >= 80 else "#fd7e14" if score >= 50 else "#dc3545"
    verdict_label = {
        "COMPLIANT": "Compliant", "NON_COMPLIANT": "Non-Compliant",
        "PARTIALLY_COMPLIANT": "Partially Compliant",
    }.get(verdict, verdict)
    rec_label = {
        "APPROVE": "Approve", "REJECT": "Reject", "CONDITIONAL_APPROVE": "Conditional Approve",
    }.get(rec, rec)
    conditions = policy_analysis.get("conditions", [])

    h = [_SUMMARY_CSS, _LOAN_OVERVIEW_CSS, '<div class="sr">']

    # ── Loan Overview (rendered only when loan_metrics are provided) ──────────
    loan_overview = _build_loan_overview_html(agreement_meta.get("loan_metrics") or {})
    if loan_overview:
        h.append(loan_overview)

    # ── Meta card ──────────────────────────────────────────────────────────────
    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}
    parties   = agreement_meta.get("parties") or {}
    if agmt_type or agmt_det or parties or policy_type:
        h.append('<div class="sr-card">')
        display_title = policy_type or agmt_type
        if display_title:
            h.append(f'<div style="font-size:18px;font-weight:800;color:#1a1a2e;margin-bottom:10px;">{_esc(display_title)}</div>')
        pills = []
        if agmt_det.get("agreement_date"):
            pills.append(agmt_det["agreement_date"])
        place = ", ".join(v for v in [agmt_det.get("city"), agmt_det.get("state")] if v)
        if place:
            pills.append(place)
        landlord = (parties.get("landlord") or {}).get("name", "")
        tenant   = (parties.get("tenant") or {}).get("company_name") or (parties.get("tenant") or {}).get("name", "")
        if landlord:
            pills.append(f"Party A: {landlord}")
        if tenant:
            pills.append(f"Party B: {tenant}")
        if pills:
            h.append('<div class="sr-meta">')
            for p in pills:
                h.append(f'<span class="sr-meta-pill">{_esc(p)}</span>')
            h.append('</div>')
        h.append('</div>')

    # ── Score circle + verdict ─────────────────────────────────────────────────
    circ = 251.33
    dash = round(circ * score / 100, 2)
    h.append(f'''
<div class="sr-header">
  <svg width="110" height="110" viewBox="0 0 100 100" style="flex-shrink:0;">
    <circle cx="50" cy="50" r="40" fill="none" stroke="#e9ecef" stroke-width="8"/>
    <circle cx="50" cy="50" r="40" fill="none" stroke="{score_color}" stroke-width="8"
            stroke-dasharray="{dash} {circ}" stroke-linecap="round"
            transform="rotate(-90 50 50)"/>
    <text x="50" y="46" text-anchor="middle" font-size="19" font-weight="800"
          fill="{score_color}" font-family="Segoe UI,Roboto,Arial,sans-serif">{score}%</text>
    <text x="50" y="62" text-anchor="middle" font-size="9" font-weight="600"
          fill="#6c757d" font-family="Segoe UI,Roboto,Arial,sans-serif" letter-spacing="0.04em">SCORE</text>
  </svg>
  <div class="sr-header-text">
    <div class="sr-header-title">Policy Compliance Analysis</div>
    <div class="sr-header-sub">
      Verdict:&nbsp;<span style="color:{score_color};font-weight:700;">{verdict_label}</span>
      <span class="dot">·</span> Recommendation:&nbsp;<span style="font-weight:700;">{rec_label}</span>
      <span class="dot">·</span> {total} requirement{"s" if total != 1 else ""} checked
    </div>
  </div>
</div>''')

    # ── Stats row ──────────────────────────────────────────────────────────────
    h.append('<div class="sr-stats-row">')
    for css, num, lbl in [
        ("total", total, "Total"), ("match", sat, "Satisfies"),
        ("viol", viol, "Violates"), ("part", risky, "Risky"), ("nf", not_addr, "Not Addressed"),
    ]:
        h.append(f'<div class="sr-stat-card {css}"><div class="sr-stat-num">{num}</div><div class="sr-stat-lbl">{lbl}</div></div>')
    h.append('</div>')

    # ── NEW: Compliance by Category (Grouped Scorecard) ────────────────────────
    grouped_html = _build_grouped_scorecard_html(reqs)
    if grouped_html:
        h.append(grouped_html)

    # ── NEW: Risk Profile Radar Chart ────────────────────────────────────
    radar_html = _build_risk_radar_html(reqs)
    if radar_html:
        h.append(radar_html)

    # ── Issues requiring attention ─────────────────────────────────────────────
    flags = [r for r in reqs if r.get("status") in ("VIOLATES", "NOT_ADDRESSED", "RISKY")]
    if flags:
        badge_map = {
            "VIOLATES":      ("badge-viol", "Violation"),
            "NOT_ADDRESSED": ("badge-nf",   "Not Addressed"),
            "RISKY":         ("badge-part", "Risky"),
        }
        h.append('<div class="sr-card"><div class="sr-card-title">Issues Requiring Attention</div>')
        for flag in flags:
            st = flag.get("status", "")
            badge_cls, badge_lbl = badge_map.get(st, ("badge-opt", st))
            css_extra = " nf" if st == "NOT_ADDRESSED" else ""
            h.append(f'''
<div class="sr-flag{css_extra}">
  <div class="sr-flag-title">{_esc(flag.get("requirement",""))} &nbsp; <span class="badge {badge_cls}">{badge_lbl}</span></div>
  <div class="sr-flag-reason">{_esc(flag.get("reason",""))}</div>
</div>''')
        h.append('</div>')

    # ── NEW: Path to Approval ────────────────────────────────────────────
    path_html = _build_path_to_approval_html(reqs, score, conditions)
    if path_html:
        h.append(path_html)

    # ── All requirements table ─────────────────────────────────────────────────
    if reqs:
        badge_html = {
            "SATISFIES":     '<span class="badge badge-match">Satisfies</span>',
            "VIOLATES":      '<span class="badge badge-viol">Violates</span>',
            "RISKY":         '<span class="badge badge-part">Risky</span>',
            "NOT_ADDRESSED": '<span class="badge badge-nf">Not Addressed</span>',
        }
        h.append('<div class="sr-card"><div class="sr-card-title">All Policy Requirements</div>')
        h.append('<table class="sr-table"><thead><tr><th>#</th><th>Requirement</th><th>Status</th><th>Finding</th></tr></thead><tbody>')
        for i, req in enumerate(reqs, 1):
            st = req.get("status", "NOT_ADDRESSED")
            h.append(f'''<tr>
  <td style="color:#adb5bd;font-size:12px;">{i}</td>
  <td style="font-weight:600;font-size:13px;">{_esc(req.get("requirement",""))}</td>
  <td>{badge_html.get(st, f'<span class="badge">{st}</span>')}</td>
  <td style="font-size:12px;color:#555;">{_esc(req.get("reason",""))}</td>
</tr>''')
        h.append('</tbody></table></div>')

    # ── Risk points ────────────────────────────────────────────────────────────
    risk_points = policy_analysis.get("risk_points", [])
    if risk_points:
        h.append('<div class="sr-card"><div class="sr-card-title">Risk Points</div>')
        h.append('<table class="sr-table"><thead><tr><th>#</th><th>Risk</th></tr></thead><tbody>')
        for i, rp in enumerate(risk_points, 1):
            h.append(f'<tr><td style="color:#adb5bd;font-size:12px;">{i}</td><td style="font-size:13px;color:#fd7e14;">{_esc(rp)}</td></tr>')
        h.append('</tbody></table></div>')

    # ── Conditions ────────────────────────────────────────────────────────────
    conditions = policy_analysis.get("conditions", [])
    if conditions:
        h.append('<div class="sr-card"><div class="sr-card-title">Conditions for Approval</div>')
        for i, cond in enumerate(conditions, 1):
            h.append(f'<div class="sr-check-item"><span>{i}. {_esc(cond)}</span><span class="badge badge-req">Required</span></div>')
        h.append('</div>')

    # ── Jurisdiction ───────────────────────────────────────────────────────────
    if jurisdiction_info:
        juris  = jurisdiction_info.get("jurisdiction", "")
        laws   = jurisdiction_info.get("applicable_laws", [])
        checks = jurisdiction_info.get("checklist", [])
        if juris or laws or checks:
            h.append('<div class="sr-card"><div class="sr-card-title">Jurisdiction &amp; Compliance Checklist</div>')
            if juris:
                h.append(f'<span class="sr-meta-pill">{_esc(juris)}</span>')
            if laws:
                h.append(f'<div style="font-size:12px;color:#6c757d;margin:10px 0;"><strong>Applicable Laws:</strong> '
                         + " &nbsp;·&nbsp; ".join(_esc(l) for l in laws) + '</div>')
            for item in checks:
                lbl_cls = "badge-req" if item.get("required") else "badge-opt"
                lbl_txt = "Required"  if item.get("required") else "Optional"
                h.append(f'<div class="sr-check-item"><span>{_esc(item.get("item",""))}</span><span class="badge {lbl_cls}">{lbl_txt}</span></div>')
            h.append('</div>')

    h.append('</div>')  # .sr
    return "\n".join(h)

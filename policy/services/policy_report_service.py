import io
import logging
import math
import re

from analyzer.services.report_service import _SUMMARY_CSS

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

# ── Global-tooltip JS (mouse-following, animated card, always stays in viewport) ─
_TOOLTIP_JS = """
<div id="g-tip" style="
  display:none; position:fixed;
  background:#ffffff; border-radius:10px; width:340px; max-width:92vw;
  font-size:13px; line-height:1.5; z-index:99999; pointer-events:none;
  box-shadow:0 12px 40px rgba(0,0,0,0.18), 0 2px 8px rgba(0,0,0,0.10);
  border:1px solid rgba(0,0,0,0.07); overflow:hidden;
  opacity:0; transition:opacity 0.14s ease;
"></div>
<script>
(function(){
  var tip = document.getElementById('g-tip');
  var fadeOut;
  function pos(e){
    var w=tip.offsetWidth||340, h=tip.offsetHeight||120;
    var vw=window.innerWidth, vh=window.innerHeight;
    var x=e.clientX+18, y=e.clientY+18;
    if(x+w>vw-10) x=e.clientX-w-14;
    if(y+h>vh-10) y=e.clientY-h-14;
    tip.style.left=x+'px'; tip.style.top=y+'px';
  }
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mouseenter',function(e){
      clearTimeout(fadeOut);
      tip.innerHTML=this.getAttribute('data-tip');
      tip.style.display='block';
      pos(e);
      requestAnimationFrame(function(){ tip.style.opacity='1'; });
    });
    el.addEventListener('mousemove',pos);
    el.addEventListener('mouseleave',function(){
      tip.style.opacity='0';
      fadeOut=setTimeout(function(){ tip.style.display='none'; },150);
    });
  });
})();
</script>
"""

_DOC_CSS = """
<style>
/* ── Document body ── */
.pdv-wrap {
  font-family: Arial, sans-serif;
  font-size: 10pt; line-height: 1.5; color: #000;
  max-width: 860px; margin: 0 auto; padding: 4px 0;
}
/* Paragraph: minimal margin, inline styles carry all formatting */
.pdv-para { margin: 2px 0; padding: 0; }

/* Heading classes — used by Markdown/plain-text renderers */
.pdv-title   { font-size:20pt; font-weight:700; text-align:center; color:#1F4E79; margin:14px 0 4px; font-family:Arial,sans-serif; }
.pdv-subtitle{ font-size:13pt; font-weight:700; text-align:center; color:#2E75B6; margin-bottom:8px; font-family:Arial,sans-serif; }
.pdv-h1      { font-size:13pt; font-weight:700; color:#1F4E79; margin:14px 0 4px; padding-bottom:4px; border-bottom:2px solid #1F4E79; font-family:Arial,sans-serif; }
.pdv-h2      { font-size:12pt; font-weight:700; color:#1F4E79; margin:12px 0 3px; padding-bottom:2px; border-bottom:1px solid #dee2e6; font-family:Arial,sans-serif; }
.pdv-h3      { font-size:11pt; font-weight:700; color:#2E75B6; margin:10px 0 3px; font-family:Arial,sans-serif; }
.pdv-h4      { font-size:10pt; font-weight:600; color:#34495e;  margin:8px 0 2px; font-style:italic; font-family:Arial,sans-serif; }
.pdv-heading { font-size:13pt; font-weight:700; color:#1F4E79; margin:14px 0 4px; padding:3px 0; border-bottom:1px solid #e5e7eb; font-family:Arial,sans-serif; }

/* Tables — DOCX cells carry all inline styling; this just sets structure */
.pdv-table {
  border-collapse: collapse; width: 100%; margin: 6px 0 12px;
}
.pdv-table td, .pdv-table th {
  border: 1px solid #d1d5db; padding: 5px 10px; vertical-align: top;
}

/* Highlighted text: smooth pill feel */
[data-tip] { cursor: help; border-radius: 3px; }
[data-tip]:hover { filter: brightness(0.96); }

/* ── Legend bar ── */
.pdv-legend {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  padding: 8px 12px; margin-bottom: 14px;
  background: #fafafa; border: 1px solid #e5e7eb; border-radius: 8px;
  font-size: 12px; color: #4b5563;
}
.leg-sat {
  display:inline-flex; align-items:center; gap:4px;
  background:#d1fae5; color:#065f46; padding:3px 10px;
  border-radius:20px; font-weight:600; font-size:11px; letter-spacing:.02em;
}
.leg-vio {
  display:inline-flex; align-items:center; gap:4px;
  background:#fee2e2; color:#991b1b; padding:3px 10px;
  border-radius:20px; font-weight:600; font-size:11px; letter-spacing:.02em;
}
.leg-rsk {
  display:inline-flex; align-items:center; gap:4px;
  background:#fef3c7; color:#92400e; padding:3px 10px;
  border-radius:20px; font-weight:600; font-size:11px; letter-spacing:.02em;
}
.leg-na {
  display:inline-flex; align-items:center; gap:4px;
  background:#e0e7ff; color:#3730a3; padding:3px 10px;
  border-radius:20px; font-weight:600; font-size:11px; letter-spacing:.02em;
}

/* ── Panels (all 4 statuses) ── */

/* Shared panel scroll-offset so headings aren't hidden under sticky bars */
.vio-panel, .risk-panel, .na-panel, .sat-panel {
  scroll-margin-top: 12px;
}

/* VIOLATES — red */
.vio-panel {
  border-left: 4px solid #dc2626; border-radius: 8px;
  background: #fef2f2; padding: 14px 18px; margin-top: 18px;
  box-shadow: 0 1px 4px rgba(220,38,38,0.08);
}
.vio-title {
  font-weight: 700; font-size: 13px; color: #991b1b;
  margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
}
.vio-item {
  font-size: 13px; color: #7f1d1d; padding: 5px 0 5px 22px; position: relative;
  border-bottom: 1px solid rgba(220,38,38,0.12);
}
.vio-item:last-child { border-bottom: none; }
.vio-item::before { content:"✗"; position:absolute; left:1px; font-size:11px; top:6px; color:#dc2626; font-weight:700; }

/* RISKY — amber */
.risk-panel {
  border-left: 4px solid #f59e0b; border-radius: 8px;
  background: #fffbeb; padding: 14px 18px; margin-top: 14px;
  box-shadow: 0 1px 4px rgba(245,158,11,0.08);
}
.risk-title {
  font-weight: 700; font-size: 13px; color: #92400e;
  margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
}
.risk-item {
  font-size: 13px; color: #78350f; padding: 5px 0 5px 20px; position: relative;
  border-bottom: 1px solid rgba(245,158,11,0.12);
}
.risk-item:last-child { border-bottom: none; }
.risk-item::before { content:"⚠"; position:absolute; left:0; font-size:12px; top:5px; }

/* NOT ADDRESSED — blue */
.na-panel {
  border-left: 4px solid #3b82f6; border-radius: 8px;
  background: #eff6ff; padding: 14px 18px; margin-top: 14px;
  box-shadow: 0 1px 4px rgba(59,130,246,0.08);
}
.na-title {
  font-weight: 700; font-size: 13px; color: #1e40af;
  margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
}
.na-item {
  font-size: 13px; color: #1e3a8a; padding: 5px 0 5px 16px; position: relative;
  border-bottom: 1px solid rgba(59,130,246,0.12);
}
.na-item:last-child { border-bottom: none; }
.na-item::before { content:"·"; position:absolute; left:3px; color:#3b82f6; font-size:18px; line-height:1; top:4px; }

/* SATISFIES — green */
.sat-panel {
  border-left: 4px solid #16a34a; border-radius: 8px;
  background: #f0fdf4; padding: 14px 18px; margin-top: 14px;
  box-shadow: 0 1px 4px rgba(22,163,74,0.08);
}
.sat-title {
  font-weight: 700; font-size: 13px; color: #15803d;
  margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
}
.sat-item {
  font-size: 13px; color: #14532d; padding: 5px 0 5px 20px; position: relative;
  border-bottom: 1px solid rgba(22,163,74,0.12);
}
.sat-item:last-child { border-bottom: none; }
.sat-item::before { content:"✓"; position:absolute; left:1px; font-size:11px; top:6px; color:#16a34a; font-weight:700; }

/* CONDITIONS — teal */
.cond-panel {
  border-left: 4px solid #10b981; border-radius: 8px;
  background: #ecfdf5; padding: 14px 18px; margin-top: 14px;
  box-shadow: 0 1px 4px rgba(16,185,129,0.08);
}
.cond-title {
  font-weight: 700; font-size: 13px; color: #065f46;
  margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
}
.cond-item {
  font-size: 13px; color: #064e3b; padding: 5px 0 5px 16px; position: relative;
  border-bottom: 1px solid rgba(16,185,129,0.12);
}
.cond-item:last-child { border-bottom: none; }
.cond-item::before { content:"›"; position:absolute; left:2px; font-weight:900; color:#10b981; }

/* Empty-state text inside any panel */
.panel-empty { font-size:13px; color:#9ca3af; font-style:italic; padding:4px 0; }
</style>
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _para_runs_to_html(para) -> str:
    """Convert DOCX paragraph runs to HTML, preserving bold/italic/underline per run."""
    parts = []
    for run in para.runs:
        text = run.text
        if not text:
            continue
        chunk = _esc(text)
        if run.bold and run.italic:
            chunk = f"<strong><em>{chunk}</em></strong>"
        elif run.bold:
            chunk = f"<strong>{chunk}</strong>"
        elif run.italic:
            chunk = f"<em>{chunk}</em>"
        if run.underline:
            chunk = f"<u>{chunk}</u>"
        parts.append(chunk)
    # Fallback: if no runs processed, use paragraph plain text
    return "".join(parts) if parts else _esc(para.text)


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
    """Build the data-tip HTML string — a rich card rendered in tooltip innerHTML."""
    badge_map = {
        "SATISFIES": ("&#x2714;", "#059669", "#d1fae5", "#065f46", "Satisfies"),
        "VIOLATES":  ("&#x2716;", "#dc2626", "#fee2e2", "#991b1b", "Violates"),
        "RISKY":     ("&#x26A0;", "#d97706", "#fef3c7", "#92400e", "Risky"),
    }
    icon, accent, bg, text_dark, label = badge_map.get(
        status, ("&#x2022;", "#6b7280", "#f3f4f6", "#374151", status.replace("_", " ").title())
    )
    req_s    = _esc(requirement)
    reason_s = _esc(reason)
    # Card: colored header bar + body
    tip_html = (
        # Header strip
        f'<div style="background:{bg};border-bottom:3px solid {accent};'
        f'padding:9px 13px;display:flex;align-items:center;gap:8px;">'
        f'<span style="background:{accent};color:#fff;width:22px;height:22px;border-radius:50%;'
        f'display:inline-flex;align-items:center;justify-content:center;'
        f'font-size:12px;font-weight:700;flex-shrink:0;">{icon}</span>'
        f'<span style="color:{text_dark};font-weight:700;font-size:12px;'
        f'letter-spacing:.04em;text-transform:uppercase;">{label}</span>'
        f'</div>'
        # Body
        f'<div style="padding:10px 13px;">'
    )
    if req_s:
        tip_html += (
            f'<div style="font-weight:700;font-size:12px;color:#111827;'
            f'margin-bottom:{6 if reason_s else 0}px;line-height:1.45;">{req_s}</div>'
        )
    if reason_s:
        tip_html += (
            f'<div style="font-size:11px;color:#4b5563;line-height:1.55;">{reason_s}</div>'
        )
    tip_html += '</div>'
    # Escape single-quotes for HTML attribute
    return tip_html.replace("'", "&#39;")


# ── DOCX → highlighted HTML ────────────────────────────────────────────────────

# Word 2010+ namespace for w14:checkbox content controls
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"


def _docx_to_html(doc_bytes: bytes, highlight_map: list) -> str:
    """
    Convert DOCX to HTML applying exact run-level font/color/size/alignment from
    the source file — a true mirror image of the original document.
    Overlays highlight background + tooltip only on policy-matched text.

    Fully preserves:
    - w14:checkbox content control SDTs  (modern Word 2010+ checkboxes)
    - Legacy FORMCHECKBOX / FORMRADIO field codes
    - Inline SDTs (text fields, date pickers) inside paragraphs
    - Body-level SDTs wrapping paragraphs or tables
    - Table cells containing any of the above
    """
    try:
        from docx import Document as DocxDocument
        from docx.text.paragraph import Paragraph as DocxPara
        from docx.text.run import Run as DocxRun
        from docx.table import Table as DocxTable
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        return "<p><em>python-docx not available for document rendering.</em></p>"

    _ALIGN = {
        WD_ALIGN_PARAGRAPH.CENTER:  "center",
        WD_ALIGN_PARAGRAPH.RIGHT:   "right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    }

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _run_rgb(run):
        """Return '#RRGGBB' from run font color, or None."""
        try:
            c = run.font.color
            if c and c.type and c.rgb:
                return f"#{str(c.rgb)}"
        except Exception:
            pass
        return None

    def _para_inline_style(para):
        """
        Build an inline CSS style string from the paragraph's alignment and
        the first non-empty run's font (size, color, bold, italic, family).
        """
        css = ["font-family:Arial,sans-serif"]
        align = _ALIGN.get(para.alignment)
        if align:
            css.append(f"text-align:{align}")
        for run in para.runs:
            if not run.text.strip():
                continue
            sz = run.font.size
            if sz:
                css.append(f"font-size:{sz.pt:.1f}pt")
            rgb = _run_rgb(run)
            if rgb:
                css.append(f"color:{rgb}")
            if run.bold:
                css.append("font-weight:700")
            if run.italic:
                css.append("font-style:italic")
            fn = run.font.name
            if fn:
                css[0] = f"font-family:'{fn}',Arial,sans-serif"
            break
        return ";".join(css)

    def _cell_bg(cell):
        """Return '#RRGGBB' cell shading from w:shd, or None."""
        try:
            tc   = cell._tc
            tcPr = tc.find(qn("w:tcPr"))
            if tcPr is not None:
                shd  = tcPr.find(qn("w:shd"))
                if shd is not None:
                    fill = shd.get(qn("w:fill"))
                    if fill and len(fill) == 6 and fill.upper() not in ("AUTO",):
                        return f"#{fill}"
        except Exception:
            pass
        return None

    def _cell_text_style(cell):
        """Return inline CSS for a cell's dominant text style (font/color/bold)."""
        css = ["font-family:Arial,sans-serif;font-size:10pt"]
        for para in cell.paragraphs:
            for run in para.runs:
                if not run.text.strip():
                    continue
                rgb = _run_rgb(run)
                if rgb:
                    css.append(f"color:{rgb}")
                if run.bold:
                    css.append("font-weight:700")
                fn = run.font.name
                if fn:
                    css[0] = f"font-family:'{fn}',Arial,sans-serif;font-size:10pt"
                break
            break
        return ";".join(css)

    # ── Checkbox / form-field detection ──────────────────────────────────────

    def _sdt_checkbox_state(sdt_elem):
        """
        Detect a w14:checkbox content control (Word 2010+).
        Returns True/False for checked state, or None if not a checkbox SDT.
        """
        sdtPr = sdt_elem.find(qn("w:sdtPr"))
        if sdtPr is None:
            return None
        cb = sdtPr.find(f"{{{_W14_NS}}}checkbox")
        if cb is None:
            return None
        checked_el = cb.find(f"{{{_W14_NS}}}checked")
        if checked_el is None:
            return False
        val = checked_el.get(f"{{{_W14_NS}}}val", "0")
        return val not in ("0", "false")

    def _legacy_ff_checkbox_state(r_elem):
        """
        Detect a legacy FORMCHECKBOX / FORMRADIO field code embedded in a w:r.
        The run must contain w:fldChar fldCharType='begin' with w:ffData/w:checkBox.
        Returns True/False for checked state, or None if not a checkbox run.
        """
        fld = r_elem.find(qn("w:fldChar"))
        if fld is None:
            return None
        if fld.get(qn("w:fldCharType")) != "begin":
            return None
        ffData = fld.find(qn("w:ffData"))
        if ffData is None:
            return None
        checkBox = ffData.find(qn("w:checkBox"))
        if checkBox is None:
            return None
        checked_el = checkBox.find(qn("w:checked"))
        if checked_el is None:
            return False
        val = checked_el.get(qn("w:val"), "1")
        return val not in ("0", "false")

    def _checkbox_html(checked: bool, size_pt=None) -> str:
        """Render a disabled HTML checkbox preserving its checked/unchecked state."""
        sz  = f"{size_pt:.0f}px" if size_pt else "13px"
        chk = "checked" if checked else ""
        return (
            f'<input type="checkbox" {chk} disabled '
            f'style="width:{sz};height:{sz};vertical-align:middle;'
            f'margin:0 4px 0 1px;accent-color:#1F4E79;cursor:default;">'
        )

    # ── Run-level HTML (paragraph content) ───────────────────────────────────

    def _runs_html(para) -> str:
        """
        Convert paragraph children to HTML, handling:
        - Normal runs (bold/italic/underline/color/size deviations)
        - Inline w:sdt checkbox content controls
        - Legacy FORMCHECKBOX / FORMRADIO field codes
        - Inline hyperlinks
        - Non-checkbox inline SDTs (text fields, date pickers) → plain text
        """
        # Dominant style from first real text run (paragraph-level baseline)
        dom_rgb  = None
        dom_bold = False
        dom_sz   = None
        for r in para.runs:
            if r.text.strip():
                dom_rgb  = _run_rgb(r)
                dom_bold = bool(r.bold)
                sz       = r.font.size
                dom_sz   = sz.pt if sz else None
                break

        out      = []
        in_fld   = False   # True while consuming a FORMCHECKBOX/FORMRADIO field
        fld_chk  = None    # checked state of the current legacy field

        for child in para._p:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            # ── Inline SDT (w14:checkbox or text/date content control) ────────
            if ctag == "sdt":
                checked = _sdt_checkbox_state(child)
                if checked is not None:
                    out.append(_checkbox_html(checked, dom_sz))
                else:
                    # Non-checkbox SDT: extract raw text from sdtContent
                    sdtContent = child.find(qn("w:sdtContent"))
                    if sdtContent is not None:
                        for t_el in sdtContent.findall(f".//{qn('w:t')}"):
                            if t_el.text:
                                out.append(_esc(t_el.text))
                continue

            # ── Hyperlink: recurse into its child runs ────────────────────────
            if ctag == "hyperlink":
                for r_elem in child.findall(qn("w:r")):
                    run = DocxRun(r_elem, para)
                    if run.text:
                        chunk = _esc(run.text)
                        if run.underline:
                            chunk = f'<u style="color:#0563C1;">{chunk}</u>'
                        out.append(chunk)
                continue

            # ── Only process w:r from here ────────────────────────────────────
            if ctag != "r":
                continue

            # ── Legacy FORMCHECKBOX/FORMRADIO begin ───────────────────────────
            ff_state = _legacy_ff_checkbox_state(child)
            if ff_state is not None:
                in_fld  = True
                fld_chk = ff_state
                continue

            # ── Legacy field end → emit checkbox ─────────────────────────────
            fld_el = child.find(qn("w:fldChar"))
            if fld_el is not None:
                if fld_el.get(qn("w:fldCharType")) == "end" and in_fld:
                    in_fld = False
                    out.append(_checkbox_html(fld_chk, dom_sz))
                    fld_chk = None
                    continue

            if in_fld:
                continue   # skip instrText / w:fldChar separate runs inside field

            # ── Normal run ────────────────────────────────────────────────────
            run = DocxRun(child, para)
            text = run.text
            if not text:
                continue
            chunk = _esc(text)

            span_css = []
            run_rgb  = _run_rgb(run)
            if run_rgb and run_rgb != dom_rgb:
                span_css.append(f"color:{run_rgb}")
            sz     = run.font.size
            run_sz = sz.pt if sz else None
            if run_sz and run_sz != dom_sz:
                span_css.append(f"font-size:{run_sz:.1f}pt")
            run_bold = bool(run.bold)
            if run_bold and not dom_bold:
                span_css.append("font-weight:700")
            elif not run_bold and dom_bold and run.bold is False:
                span_css.append("font-weight:400")

            if span_css:
                chunk = f'<span style="{";".join(span_css)}">{chunk}</span>'
            if run.italic:
                chunk = f"<em>{chunk}</em>"
            if run.underline:
                chunk = f"<u>{chunk}</u>"
            out.append(chunk)

        return "".join(out) if out else _esc(para.text)

    # ── Element renderers ─────────────────────────────────────────────────────

    def _has_inline_checkboxes(p_elem) -> bool:
        """True if the paragraph element contains any inline checkbox SDTs."""
        for child in p_elem:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "sdt" and _sdt_checkbox_state(child) is not None:
                return True
            # Legacy FORMCHECKBOX begin
            if ctag == "r":
                if _legacy_ff_checkbox_state(child) is not None:
                    return True
        return False

    def _render_paragraph(p_elem) -> str | None:
        """Render a w:p element to an HTML <p> string, or None if empty."""
        para = DocxPara(p_elem, doc)
        text = para.text.strip()

        # Skip truly empty paragraphs unless they carry inline form controls
        if not text and not _has_inline_checkboxes(p_elem):
            return None

        base_style = _para_inline_style(para)
        inner_html = _runs_html(para)
        if not inner_html.strip():
            return None

        match = _match_highlight(text, highlight_map)
        if match:
            hl_bg = _STATUS_BG.get(match[0], "")
            tip   = _tip_attr(*match)
            style = f"{base_style};background-color:{hl_bg};" if hl_bg else base_style
            return f'<p class="pdv-para" style="{style}" data-tip=\'{tip}\'>{inner_html}</p>'
        return f'<p class="pdv-para" style="{base_style}">{inner_html}</p>'

    def _cell_content_html(cell) -> str:
        """
        Render a table cell's full content using _runs_html so that checkboxes
        and other inline controls inside cells are preserved.
        Falls back to escaped plain text if all paragraphs are truly empty.
        """
        rendered = []
        for para in cell.paragraphs:
            inner = _runs_html(para)
            if inner.strip():
                rendered.append(inner)
        return "<br>".join(rendered) if rendered else _esc(cell.text)

    def _render_table(tbl_elem) -> list:
        """Render a w:tbl element, returning a list of HTML strings."""
        table  = DocxTable(tbl_elem, doc)
        rows_h = ['<table class="pdv-table">']

        for row in table.rows:
            # Deduplicate merged cells by plain text key
            seen_cells: list = []
            seen_set:   set  = set()
            for cell in row.cells:
                cv = cell.text.strip()
                if cv not in seen_set:
                    seen_cells.append(cell)
                    seen_set.add(cv)

            if not any(c.text.strip() for c in seen_cells):
                continue

            # Determine highlight for the row / individual cells
            row_text = " | ".join(c.text.strip() for c in seen_cells)
            match    = _match_highlight(row_text, highlight_map)
            if not match:
                for cell in seen_cells:
                    match = _match_highlight(cell.text.strip(), highlight_map)
                    if match:
                        break

            hl_bg   = _STATUS_BG.get(match[0]) if match else None
            tip_str = f" data-tip='{_tip_attr(*match)}'" if match else ""

            rows_h.append("<tr>")
            for cell in seen_cells:
                text_style   = _cell_text_style(cell)
                bg           = hl_bg if hl_bg else (_cell_bg(cell) or None)
                bg_css       = f"background-color:{bg};" if bg else ""
                cell_content = _cell_content_html(cell)
                rows_h.append(
                    f'<td style="{text_style};{bg_css}vertical-align:top;'
                    f'border:1px solid #d1d5db;padding:5px 10px;"{tip_str}>'
                    f'{cell_content}</td>'
                )
            rows_h.append("</tr>")

        rows_h.append("</table>")
        return rows_h

    def _render_children(children) -> list:
        """
        Dispatch a sequence of body-level (or sdtContent-level) child elements
        to their respective renderers.  Handles p, tbl, and sdt recursively.
        """
        parts: list = []
        for child in children:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p":
                html = _render_paragraph(child)
                if html:
                    parts.append(html)

            elif tag == "tbl":
                parts.extend(_render_table(child))

            elif tag == "sdt":
                # Body-level SDT: recurse into its content (may contain p / tbl / nested sdt)
                sdtContent = child.find(qn("w:sdtContent"))
                if sdtContent is not None:
                    parts.extend(_render_children(sdtContent))

        return parts

    # ── Build HTML ────────────────────────────────────────────────────────────
    doc   = DocxDocument(io.BytesIO(doc_bytes))
    parts = ['<div class="pdv-wrap">']
    parts.extend(_render_children(doc.element.body))
    parts.append("</div>")
    return "\n".join(parts)


# ── PDF → highlighted HTML ─────────────────────────────────────────────────────

def _pdf_to_html(doc_bytes: bytes, highlight_map: list) -> str:
    """
    PDF → HTML with absolute positioning for pixel-perfect layout fidelity.

    Each PDF page becomes a position:relative container sized exactly to the
    page dimensions (in points).  Every text block, image, and table is placed
    with position:absolute at its exact PDF coordinates so nothing reflows.

    Highlights use two complementary mechanisms:
      1. page.search_for() → precise span-level overlay divs at exact text coords.
      2. _match_highlight() fallback → background-color on the parent block div
         when search_for() cannot locate the text (e.g. ligatures / encoding).
    """
    try:
        import fitz
    except ImportError:
        return "<p><em>PyMuPDF (fitz) not installed; cannot render PDF.</em></p>"

    import base64 as _b64
    from collections import Counter

    doc = fitz.open(stream=doc_bytes, filetype="pdf")

    # ── Pass 1: dominant body font size (used as fallback for spans) ───────────
    all_sizes: list[float] = []
    for _pg in doc:
        for blk in _pg.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    sz = sp.get("size", 0)
                    if sp.get("text", "").strip() and sz > 0:
                        all_sizes.append(round(sz, 1))
    normal_size: float = Counter(all_sizes).most_common(1)[0][0] if all_sizes else 11.0

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _color_hex(color_int: int) -> str:
        return (
            f"#{(color_int >> 16) & 0xFF:02X}"
            f"{(color_int >>  8) & 0xFF:02X}"
            f"{ color_int        & 0xFF:02X}"
        )

    def _render_span(span: dict) -> str:
        """Single PDF span → inline HTML with exact font/bold/italic/color."""
        raw = span.get("text", "")
        if not raw:
            return ""
        chunk     = _esc(raw)
        sz        = span.get("size", normal_size)
        flags     = span.get("flags", 0)
        is_bold   = bool(flags & 16)
        is_italic = bool(flags & 2)
        color     = _color_hex(span.get("color", 0))
        font_name = span.get("font", "")

        css: list[str] = [f"font-size:{sz:.1f}pt"]
        if is_bold:
            css.append("font-weight:700")
        if color not in ("#000000", "#000001"):
            css.append(f"color:{color}")
        if font_name:
            css.append(f"font-family:'{font_name}',Arial,sans-serif")
        else:
            css.append("font-family:Arial,sans-serif")

        chunk = f'<span style="{";".join(css)}">{chunk}</span>'
        if is_italic:
            chunk = f"<em>{chunk}</em>"
        return chunk

    def _block_dominant_css(block: dict) -> str:
        """CSS string for the block container based on first real span."""
        for ln in block.get("lines", []):
            for sp in ln.get("spans", []):
                if sp.get("text", "").strip():
                    sz    = sp.get("size", normal_size)
                    flags = sp.get("flags", 0)
                    color = _color_hex(sp.get("color", 0))
                    fn    = sp.get("font", "")
                    css   = [f"font-size:{sz:.1f}pt"]
                    if flags & 16:
                        css.append("font-weight:700")
                    if color not in ("#000000", "#000001"):
                        css.append(f"color:{color}")
                    css.append(
                        f"font-family:'{fn}',Arial,sans-serif" if fn
                        else "font-family:Arial,sans-serif"
                    )
                    return ";".join(css)
        return f"font-size:{normal_size:.1f}pt;font-family:Arial,sans-serif"

    def _inside(cx: float, cy: float, bbox: tuple) -> bool:
        """True if point (cx, cy) lies inside bbox (x0,y0,x1,y1)."""
        return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]

    def _block_inside_any(bb: tuple, table_bboxes: list) -> bool:
        cx = (bb[0] + bb[2]) / 2
        cy = (bb[1] + bb[3]) / 2
        return any(_inside(cx, cy, tb) for tb in table_bboxes)

    # ── Pass 2: page-by-page rendering ────────────────────────────────────────
    page_htmls: list[str] = []

    for pg_num, page in enumerate(doc):
        pw: float = page.rect.width   # points
        ph: float = page.rect.height  # points
        page_dict = page.get_text("dict")

        # ── 2a. Highlight overlays via search_for() ───────────────────────────
        # One overlay div per rect returned by search_for for each matched text.
        # These are rendered last (on top of everything else).
        overlay_parts: list[str] = []
        found_texts: set[str] = set()   # track which hl texts landed on this page

        for hl_text, status, requirement, reason in highlight_map:
            if not hl_text or len(hl_text.strip()) < 4:
                continue
            hl_bg = _STATUS_BG.get(status)
            if not hl_bg:
                continue
            tip = _tip_attr(status, requirement, reason)

            # Try full text first, then a shorter prefix (handles long excerpts)
            candidates = [hl_text]
            if len(hl_text) > 100:
                # Take up to 100 chars, cut at last space so we don't split a word
                short = hl_text[:100].rsplit(" ", 1)[0]
                if len(short) >= 10:
                    candidates.append(short)

            for candidate in candidates:
                try:
                    rects = page.search_for(candidate)
                except Exception:
                    rects = []
                for r in rects:
                    found_texts.add(hl_text)
                    rw = r.x1 - r.x0
                    rh = r.y1 - r.y0
                    overlay_parts.append(
                        f'<div style="position:absolute;left:{r.x0:.2f}pt;top:{r.y0:.2f}pt;'
                        f'width:{rw:.2f}pt;height:{rh:.2f}pt;'
                        f'background-color:{hl_bg};opacity:0.5;'
                        f'pointer-events:all;cursor:help;border-radius:2px;z-index:10;" '
                        f'data-tip=\'{tip}\'></div>'
                    )
                if rects:
                    break  # found on this page with this candidate — stop trying shorter

        # ── 2b. Detect tables ─────────────────────────────────────────────────
        table_bboxes: list[tuple] = []
        table_elements: list[tuple] = []   # (y0, x0, html)

        def _normalize_rows(raw_rows: list) -> list:
            """
            Convert PyMuPDF tab.extract() output to clean (label, value) pairs.

            PyMuPDF often:
            - Returns 4-6 phantom empty columns around the real 2 columns
            - Splits wrapped cell text across consecutive rows
            - Places the same logical column in different column indices across rows

            Strategy:
            1. Drop entirely-empty rows.
            2. Merge single-cell "continuation" rows into the previous row's
               matching column (handles line-wrapped label text like
               "Estimated Credit Score (Co-\nBorrower)").
            3. From each row take first-non-empty cell as label and
               last-non-empty cell (if different) as value → always 2 columns.
            """
            # Step 1 — drop empty rows
            non_empty = [
                row for row in raw_rows
                if any(v is not None and str(v).strip() for v in row)
            ]
            if not non_empty:
                return []

            # Step 2 — merge continuation rows
            merged: list[list] = []
            for row in non_empty:
                filled = [(i, str(v).strip()) for i, v in enumerate(row)
                          if v is not None and str(v).strip()]
                if len(filled) == 1 and merged:
                    # Single non-empty cell: append to same column of previous row
                    col_idx, cell_text = filled[0]
                    prev = merged[-1]
                    if col_idx < len(prev) and prev[col_idx] is not None:
                        prev_text = str(prev[col_idx]).rstrip()
                        # Remove trailing hyphen from line-wrapped word
                        if prev_text.endswith("-"):
                            prev[col_idx] = prev_text[:-1] + cell_text
                        else:
                            prev[col_idx] = prev_text + " " + cell_text
                        continue   # absorbed — don't add as new row
                merged.append(list(row))

            # Step 3 — extract (label, value) pairs
            pairs: list[tuple[str, str]] = []
            for row in merged:
                filled = [(i, str(v).strip()) for i, v in enumerate(row)
                          if v is not None and str(v).strip()]
                if not filled:
                    continue
                label = filled[0][1]
                value = filled[-1][1] if len(filled) > 1 else ""
                pairs.append((label, value))
            return pairs

        try:
            finder = page.find_tables()
            for tab in finder.tables:
                tb = tab.bbox   # (x0, y0, x1, y1)
                table_bboxes.append(tb)

                pairs = _normalize_rows(tab.extract())
                if not pairs:
                    continue

                tw = tb[2] - tb[0]
                t_parts = [
                    f'<table style="border-collapse:collapse;width:{tw:.2f}pt;'
                    f'font-size:{normal_size:.1f}pt;font-family:Arial,sans-serif;">'
                ]
                for label, value in pairs:
                    row_text   = f"{label} | {value}" if value else label
                    row_match  = _match_highlight(row_text, highlight_map)
                    cell_match = row_match or _match_highlight(label, highlight_map) or (
                        _match_highlight(value, highlight_map) if value else None
                    )
                    hl_bg  = _STATUS_BG.get(cell_match[0]) if cell_match else None
                    tip_s  = f" data-tip='{_tip_attr(*cell_match)}'" if cell_match else ""
                    bg_css = f"background-color:{hl_bg};" if hl_bg else ""
                    lw = tw * 0.38   # label column ≈ 38% width (matches original PDF)
                    t_parts.append(
                        f'<tr>'
                        f'<td style="{bg_css}border:1px solid #d1d5db;padding:4px 8px;'
                        f'vertical-align:top;font-weight:600;width:{lw:.1f}pt;"'
                        f'{tip_s}>{_esc(label)}</td>'
                        f'<td style="{bg_css}border:1px solid #d1d5db;padding:4px 8px;'
                        f'vertical-align:top;"{tip_s}>{_esc(value)}</td>'
                        f'</tr>'
                    )
                t_parts.append("</table>")

                table_elements.append((
                    tb[1], tb[0],
                    f'<div style="position:absolute;left:{tb[0]:.2f}pt;top:{tb[1]:.2f}pt;'
                    f'width:{tw:.2f}pt;">' + "\n".join(t_parts) + '</div>'
                ))
        except Exception as exc:
            logger.debug("PDF table detection skipped (page %d): %s", pg_num + 1, exc)

        # ── 2c. Embedded images ───────────────────────────────────────────────
        image_elements: list[tuple] = []   # (y0, x0, html)
        try:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                rects_list = page.get_image_rects(xref)
                if not rects_list:
                    continue
                ir = rects_list[0]
                try:
                    base_img = doc.extract_image(xref)
                    ext      = base_img.get("ext", "png")
                    img_b64  = _b64.b64encode(base_img["image"]).decode()
                    iw = ir.x1 - ir.x0
                    ih = ir.y1 - ir.y0
                    image_elements.append((
                        ir.y0, ir.x0,
                        f'<img src="data:image/{ext};base64,{img_b64}" '
                        f'style="position:absolute;left:{ir.x0:.2f}pt;top:{ir.y0:.2f}pt;'
                        f'width:{iw:.2f}pt;height:{ih:.2f}pt;display:block;" alt="">'
                    ))
                except Exception:
                    pass
        except Exception:
            pass

        # ── 2d. Text blocks (skip blocks inside a detected table) ─────────────
        text_elements: list[tuple] = []   # (y0, x0, html)

        for block in page_dict["blocks"]:
            if block.get("type") != 0:
                continue
            bb = block.get("bbox", (0, 0, 0, 0))
            if _block_inside_any(bb, table_bboxes):
                continue

            bx0, by0, bx1, by1 = bb
            bw = bx1 - bx0
            bh = by1 - by0

            lines_html:  list[str] = []
            plain_parts: list[str] = []
            for ln in block.get("lines", []):
                lh: list[str] = []
                lp: list[str] = []
                for sp in ln.get("spans", []):
                    raw = sp.get("text", "")
                    if raw:
                        lh.append(_render_span(sp))
                        lp.append(raw)
                if lp:
                    lines_html.append("".join(lh))
                    plain_parts.append("".join(lp).strip())

            if not lines_html:
                continue

            block_plain = " ".join(plain_parts).strip()
            # Join PDF lines with a space so the browser reflows text naturally
            # within the block's exact width.  Using <br> would force line breaks
            # at the PDF's exact split points, but HTML font metrics are slightly
            # different — any line that is even 1px wider than its PDF width would
            # wrap to an extra line, adding unwanted height and causing overlap with
            # the element positioned below this block.
            inner_html  = " ".join(lines_html)
            dom_css     = _block_dominant_css(block)

            # Fallback highlight: only apply background if search_for() did NOT
            # already place an overlay for this text on this page.
            match  = _match_highlight(block_plain, highlight_map)
            bg_css = ""
            tip    = ""
            if match and match[0] in _STATUS_BG and _STATUS_BG[match[0]]:
                # Check whether the matched hl_text was already handled by search_for
                already_overlaid = any(
                    hl_text in found_texts
                    for hl_text, st, _, _ in highlight_map
                    if st == match[0]
                )
                if not already_overlaid:
                    bg_css = f"background-color:{_STATUS_BG[match[0]]};"
                    tip    = f" data-tip='{_tip_attr(*match)}'"

            text_elements.append((
                by0, bx0,
                f'<div style="position:absolute;left:{bx0:.2f}pt;top:{by0:.2f}pt;'
                f'width:{bw:.2f}pt;{dom_css};{bg_css}'
                f'line-height:1.4;overflow:hidden;"'
                f'{tip}>{inner_html}</div>'
            ))

        # ── 2e. Assemble page ─────────────────────────────────────────────────
        # Stacking order: images first, then tables, then text, overlays on top.
        all_elements = image_elements + table_elements + text_elements
        all_elements.sort(key=lambda e: (e[0], e[1]))

        page_parts = [
            f'<div style="position:relative;width:{pw:.2f}pt;min-height:{ph:.2f}pt;'
            f'background:white;margin:0 auto 32px;overflow:visible;'
            f'box-shadow:0 2px 12px rgba(0,0,0,0.12);">'
        ]
        for _, _, html in all_elements:
            page_parts.append(html)
        page_parts.extend(overlay_parts)   # highlight overlays on top
        page_parts.append('</div>')

        page_htmls.append("\n".join(page_parts))

    doc.close()

    return (
        '<div style="background:#f3f4f6;padding:24px 16px;">'
        + "\n".join(page_htmls)
        + '</div>'
    )


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
    Convert Markdown document to HTML preserving full heading hierarchy (h1-h4),
    tables with header rows, and inline bold/italic. Applies background highlights
    and tooltip attributes where text matches a policy requirement.
    """
    parts = ['<div class="pdv-wrap">']
    lines = md_text.split("\n")
    i = 0

    while i < len(lines):
        raw      = lines[i]
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

            data_rows = [l for l in table_lines if not _is_md_separator(l)]
            if not data_rows:
                continue

            parts.append('<table class="pdv-table">')
            for row_idx, row_line in enumerate(data_rows):
                cells_raw = [c.strip() for c in row_line.split("|")]
                cells_raw = [c for c in cells_raw if c]
                if not cells_raw:
                    continue

                row_plain = " | ".join(_md_strip(c) for c in cells_raw)
                match = _match_highlight(row_plain, highlight_map)
                if not match:
                    for c in cells_raw:
                        match = _match_highlight(_md_strip(c), highlight_map)
                        if match:
                            break

                bg_style = f"background-color:{_STATUS_BG[match[0]]};" if match and _STATUS_BG.get(match[0]) else ""
                tip_str  = f" data-tip='{_tip_attr(*match)}'" if match else ""

                is_header = (row_idx == 0)
                parts.append("<tr>")
                for ci, cell_raw in enumerate(cells_raw):
                    cell_html = _md_inline(cell_raw)
                    if is_header:
                        parts.append(f'<th style="{bg_style}"{tip_str}>{cell_html}</th>')
                    else:
                        cls = "lbl" if ci == 0 else "val"
                        parts.append(f'<td class="{cls}" style="{bg_style}"{tip_str}>{cell_html}</td>')
                parts.append("</tr>")

            parts.append("</table>")
            continue

        # ── ATX Headings: #, ##, ###, #### ───────────────────────────────────
        heading_match = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if heading_match:
            level  = len(heading_match.group(1))
            htext  = heading_match.group(2).strip()
            plain  = _md_strip(htext)
            inner  = _md_inline(htext)
            css    = {1: "pdv-h1", 2: "pdv-h2", 3: "pdv-h3", 4: "pdv-h4"}.get(level, "pdv-h3")
            match  = _match_highlight(plain, highlight_map)
            bg     = f' style="background-color:{_STATUS_BG[match[0]]};"' if match and _STATUS_BG.get(match[0]) else ""
            tip    = f" data-tip='{_tip_attr(*match)}'" if match else ""
            parts.append(f'<div class="{css}"{bg}{tip}>{inner}</div>')
            continue

        # ── Setext heading: underline with === or --- ─────────────────────────
        if i < len(lines):
            next_stripped = lines[i].strip()
            if re.match(r"^=+$", next_stripped):
                i += 1
                plain = _md_strip(stripped)
                inner = _md_inline(stripped)
                match = _match_highlight(plain, highlight_map)
                bg    = f' style="background-color:{_STATUS_BG[match[0]]};"' if match and _STATUS_BG.get(match[0]) else ""
                tip   = f" data-tip='{_tip_attr(*match)}'" if match else ""
                parts.append(f'<div class="pdv-h1"{bg}{tip}>{inner}</div>')
                continue
            elif re.match(r"^-+$", next_stripped) and len(next_stripped) > 2:
                i += 1
                plain = _md_strip(stripped)
                inner = _md_inline(stripped)
                match = _match_highlight(plain, highlight_map)
                bg    = f' style="background-color:{_STATUS_BG[match[0]]};"' if match and _STATUS_BG.get(match[0]) else ""
                tip   = f" data-tip='{_tip_attr(*match)}'" if match else ""
                parts.append(f'<div class="pdv-h2"{bg}{tip}>{inner}</div>')
                continue

        # ── Bold-headed lines: **N. Section** or **ALL CAPS** ────────────────
        plain = _md_strip(stripped)
        is_all_caps_title = (len(plain) < 100 and plain == plain.upper() and len(plain.split()) > 1)
        is_bold_section   = bool(re.match(r"^\*\*\d+[\.\)]\s", stripped))
        is_bold_heading   = bool(re.match(r"^\*\*[A-Z][^*]{2,}\*\*\s*$", stripped))

        inline_html = _md_inline(stripped)
        match       = _match_highlight(plain, highlight_map)
        bg_attr     = f' style="background-color:{_STATUS_BG[match[0]]};"' if match and _STATUS_BG.get(match[0]) else ""
        tip_attr_s  = f" data-tip='{_tip_attr(*match)}'" if match else ""

        if is_all_caps_title:
            parts.append(f'<div class="pdv-title"{bg_attr}{tip_attr_s}>{inline_html}</div>')
        elif is_bold_section or is_bold_heading:
            parts.append(f'<div class="pdv-h2"{bg_attr}{tip_attr_s}>{inline_html}</div>')
        else:
            parts.append(f'<p class="pdv-para"{bg_attr}{tip_attr_s}>{inline_html}</p>')

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
    reqs = policy_analysis.get("policy_requirements", [])

    hl_map = _build_highlight_map(reqs)
    lines  = [_DOC_CSS]

    # ── Count each status for the legend badges ───────────────────────────────
    sat_reqs   = [r for r in reqs if r.get("status") == "SATISFIES"]
    vio_reqs   = [r for r in reqs if r.get("status") == "VIOLATES"]
    risky_reqs = [r for r in reqs if r.get("status") == "RISKY"]
    na_reqs    = [r for r in reqs if r.get("status") == "NOT_ADDRESSED"]
    risk_pts   = policy_analysis.get("risk_points", [])

    risky_total = len(risky_reqs) + len(risk_pts)

    # ── Legend — badges (non-clickable) ───────────────────────────────────────
    lines.append(
        '<div class="pdv-legend">'
        f'<span class="leg-sat">&#x2714; Satisfies ({len(sat_reqs)})</span>'
        f'<span class="leg-vio">&#x2716; Violates ({len(vio_reqs)})</span>'
        f'<span class="leg-rsk">&#x26A0; Risky ({risky_total})</span>'
        f'<span class="leg-na">&#x25CB; Not Addressed ({len(na_reqs)})</span>'
        '<span style="color:#9ca3af;font-size:11px;">— Hover highlighted text for details</span>'
        '</div>'
    )

    # ── Document body ──────────────────────────────────────────────────────────
    if doc_bytes and file_type == "docx":
        doc_html = _docx_to_html(doc_bytes, hl_map)
    elif doc_bytes and file_type == "pdf":
        doc_html = _pdf_to_html(doc_bytes, hl_map)
    elif document_text and file_type in ("markdown", "txt", None):
        if "|" in document_text or "**" in document_text or document_text.lstrip().startswith("#"):
            doc_html = _markdown_to_html(document_text, hl_map)
        else:
            doc_html = _plain_text_to_html(document_text, hl_map)
    elif document_text:
        doc_html = _plain_text_to_html(document_text, hl_map)
    else:
        doc_html = "<p><em>No document content available.</em></p>"

    lines.append(doc_html)

    # ── Bottom summary panels — all 4 statuses ────────────────────────────────

    # 1. VIOLATES (most critical first)
    lines.append(f'<div class="vio-panel" id="sec-vio">')
    lines.append(f'<div class="vio-title">&#x2716; Violations Found ({len(vio_reqs)})</div>')
    if vio_reqs:
        for r in vio_reqs:
            req_text = _esc(r.get("requirement", ""))
            reason   = _esc(r.get("reason", ""))
            body     = f"<strong>{req_text}</strong>" + (f" — {reason}" if reason else "")
            lines.append(f'<div class="vio-item">{body}</div>')
    else:
        lines.append('<div class="panel-empty">No violations found.</div>')
    lines.append('</div>')

    # 2. RISKY
    lines.append(f'<div class="risk-panel" id="sec-rsk">')
    lines.append(f'<div class="risk-title">&#x26A0; Risk Points ({risky_total})</div>')
    if risky_reqs or risk_pts:
        for r in risky_reqs:
            req_text = _esc(r.get("requirement", ""))
            reason   = _esc(r.get("reason", ""))
            body     = f"<strong>{req_text}</strong>" + (f" — {reason}" if reason else "")
            lines.append(f'<div class="risk-item">{body}</div>')
        for pt in risk_pts:
            lines.append(f'<div class="risk-item">{_esc(str(pt))}</div>')
    else:
        lines.append('<div class="panel-empty">No risk points identified.</div>')
    lines.append('</div>')

    # 3. NOT ADDRESSED
    lines.append(f'<div class="na-panel" id="sec-na">')
    lines.append(f'<div class="na-title">&#x25CB; Not Addressed in Document ({len(na_reqs)})</div>')
    if na_reqs:
        for r in na_reqs:
            req_text = _esc(r.get("requirement", ""))
            reason   = _esc(r.get("reason", "") or r.get("recommendation", ""))
            body     = f"<strong>{req_text}</strong>" + (f" — {reason}" if reason else "")
            lines.append(f'<div class="na-item">{body}</div>')
    else:
        lines.append('<div class="panel-empty">All requirements are addressed.</div>')
    lines.append('</div>')

    # 4. SATISFIES
    lines.append(f'<div class="sat-panel" id="sec-sat">')
    lines.append(f'<div class="sat-title">&#x2714; Requirements Satisfied ({len(sat_reqs)})</div>')
    if sat_reqs:
        for r in sat_reqs:
            req_text = _esc(r.get("requirement", ""))
            reason   = _esc(r.get("reason", ""))
            body     = f"<strong>{req_text}</strong>" + (f" — {reason}" if reason else "")
            lines.append(f'<div class="sat-item">{body}</div>')
    else:
        lines.append('<div class="panel-empty">No satisfied requirements found.</div>')
    lines.append('</div>')

    # ── Global tooltip card + JS ───────────────────────────────────────────────
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

/* ══ Dashboard layout ══ */

/* Header */
.ds-hdr { background:#fff; border-radius:12px; padding:14px 20px; margin-bottom:14px;
  box-shadow:0 1px 4px rgba(0,0,0,0.08); display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
.ds-hdr-title-block { flex:1; min-width:180px; }
.ds-hdr-policy { font-size:16px; font-weight:800; color:#1a1a2e; margin-bottom:3px; }
.ds-hdr-meta { font-size:12px; color:#6c757d; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.ds-hdr-dot { color:#ced4da; }

/* Stat chips */
.ds-chips { display:flex; gap:6px; flex-shrink:0; flex-wrap:wrap; }
.ds-chip { display:flex; flex-direction:column; align-items:center;
  border-radius:8px; padding:8px 12px; min-width:50px; background:#f8f9fa; border-top:2px solid; }
.ds-chip-n { font-size:20px; font-weight:800; line-height:1; }
.ds-chip-l { font-size:8px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; color:#6c757d; margin-top:2px; }
.ds-chip.total { border-top-color:#495057; } .ds-chip.total .ds-chip-n { color:#212529; }
.ds-chip.pass  { border-top-color:#28a745; } .ds-chip.pass  .ds-chip-n { color:#28a745; }
.ds-chip.fail  { border-top-color:#dc3545; } .ds-chip.fail  .ds-chip-n { color:#dc3545; }
.ds-chip.risky { border-top-color:#fd7e14; } .ds-chip.risky .ds-chip-n { color:#fd7e14; }
.ds-chip.na    { border-top-color:#0d6efd; } .ds-chip.na    .ds-chip-n { color:#0d6efd; }

/* Main two-panel */
.ds-main { display:grid; grid-template-columns:1.15fr 0.85fr; gap:14px; margin-bottom:14px; }

/* Left panel — sectioned table */
.ds-left { background:#fff; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,0.08); overflow:hidden; }
.ds-sec { border-bottom:1px solid #f0f0f0; }
.ds-sec:last-child { border-bottom:none; }
.ds-sec-hdr { display:flex; justify-content:space-between; align-items:center;
  padding:8px 16px; font-size:10.5px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
.ds-sec-hdr.na   { background:#eef2ff; color:#3730a3; border-left:3px solid #0d6efd; }
.ds-sec-hdr.viol { background:#fff1f2; color:#9b1c1c; border-left:3px solid #dc3545; }
.ds-sec-hdr.cond { background:#f0fdf4; color:#14532d; border-left:3px solid #28a745; }
.ds-sec-hdr.risky{ background:#fffbeb; color:#92400e; border-left:3px solid #fd7e14; }
.ds-sec-badge { font-size:11px; font-weight:800; padding:1px 7px; border-radius:10px; }
.ds-sec-hdr.na   .ds-sec-badge { background:#e0e7ff; color:#3730a3; }
.ds-sec-hdr.viol .ds-sec-badge { background:#fee2e2; color:#991b1b; }
.ds-sec-hdr.cond .ds-sec-badge { background:#dcfce7; color:#14532d; }
.ds-sec-hdr.risky .ds-sec-badge { background:#fef3c7; color:#92400e; }
.ds-sec-empty { padding:8px 16px; font-size:12px; color:#adb5bd; font-style:italic; }
.ds-item { display:flex; align-items:flex-start; gap:6px;
  padding:7px 14px 7px 16px; border-bottom:1px solid #f8f8f8; }
.ds-item:last-child { border-bottom:none; }
.ds-item-dot { width:5px; height:5px; border-radius:50%; flex-shrink:0; margin-top:5px; }
.ds-item-body { flex:1; min-width:0; }
.ds-item-title { font-size:12px; font-weight:600; color:#1a1a2e; line-height:1.35; margin-bottom:1px; }
.ds-item-sub { font-size:11px; color:#6c757d; line-height:1.35; }
.ds-item-num { font-size:11px; color:#adb5bd; flex-shrink:0; margin-top:2px; }

/* Right panel — score + category circles */
.ds-right { background:#fff; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,0.08);
  padding:18px 14px; display:flex; flex-direction:column; align-items:center; gap:14px; }
.ds-score-center { text-align:center; }
.ds-score-verdict { font-size:12px; color:#6c757d; margin-top:6px; }
.ds-score-rec { font-size:11px; color:#6c757d; margin-top:2px; }

/* Category donut grid */
.ds-cat-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:8px; width:100%; }
.ds-cat-tile { background:#f8f9fa; border-radius:8px; padding:10px 8px;
  display:flex; flex-direction:column; align-items:center; gap:4px; }
.ds-cat-tile-name { font-size:10px; font-weight:700; color:#495057;
  text-align:center; letter-spacing:.03em; line-height:1.3; }
.ds-cat-tile-counts { font-size:10px; color:#adb5bd; }

/* All requirements table */
.ds-reqs { background:#fff; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,0.08);
  overflow:hidden; margin-bottom:14px; }
.ds-reqs-hdr { font-size:10.5px; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  color:#6c757d; padding:10px 14px; border-bottom:1px solid #f0f0f0; background:#fafbfc; }
.ds-rt { width:100%; border-collapse:collapse; }
.ds-rt th { background:#f8f9fa; font-size:10px; font-weight:700; letter-spacing:.05em;
  text-transform:uppercase; color:#6c757d; padding:6px 12px; text-align:left; border-bottom:2px solid #e9ecef; }
.ds-rt td { padding:5px 12px; border-bottom:1px solid #f4f4f4; vertical-align:top; font-size:12px; }
.ds-rt tr:last-child td { border-bottom:none; }
.ds-rt tr:hover td { background:#fafbff; }
.ds-rt-num { color:#adb5bd; font-size:11px; }
.ds-rt-req { font-weight:600; color:#1a1a2e; }
.ds-rt-find { color:#6c757d; }
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

    verdict_label = {
        "COMPLIANT": "Compliant", "NON_COMPLIANT": "Non-Compliant",
        "PARTIALLY_COMPLIANT": "Partially Compliant",
    }.get(verdict, verdict)
    rec_label = {
        "APPROVE": "Approve", "REJECT": "Reject",
        "CONDITIONAL_APPROVE": "Conditional Approve",
    }.get(rec, rec)

    # ── Grouped categories ────────────────────────────────────────────────────
    # Stamp each requirement with its category so the frontend can filter
    # policy_analysis.policy_requirements by category without needing a
    # separate requirements list inside each group.
    groups = _group_requirements(reqs)
    for cat, cat_reqs in groups.items():
        for r in cat_reqs:
            r["category"] = cat
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
            "total":         len(cat_reqs),
            "satisfies":     cat_sat,
            "violates":      cat_viol,
            "risky":         cat_risk,
            "not_addressed": cat_na,
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
        "projected_score":       proj_score,
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
        # ── Display labels (derived, not in raw AI output) ─────────────────
        "verdict_label":  verdict_label,
        "rec_label":      rec_label,
        "policy_type":    policy_type,

        # ── Counts ────────────────────────────────────────────────────────
        "stats": {
            "total":         total,
            "satisfies":     sat,
            "violates":      viol,
            "risky":         risky,
            "not_addressed": not_addr,
        },

        # ── Issues only (violations + risky + not addressed) ─────────────
        "issues": issues_list,

        # ── Grouped category breakdown ────────────────────────────────────
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
            "type":    agmt_type,
            "date":    agmt_det.get("agreement_date", ""),
            "city":    agmt_det.get("city", ""),
            "state":   agmt_det.get("state", ""),
            "party_a": (parties.get("landlord") or {}).get("name", ""),
            "party_b": (parties.get("tenant") or {}).get("company_name")
                       or (parties.get("tenant") or {}).get("name", ""),
        },

        # ── Loan metrics (pass-through for frontend tiles) ────────────────
        "loan_metrics": agreement_meta.get("loan_metrics") or {},
    }


# ── Mini donut SVG helper ─────────────────────────────────────────────────────

def _mini_donut(pct: int, color: str, size: int = 54) -> str:
    """Return a small inline SVG donut showing pct% filled in color."""
    r = 20
    circ = 2 * math.pi * r
    dash = circ * pct / 100
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 52 52">'
        f'<circle cx="26" cy="26" r="{r}" fill="none" stroke="#e9ecef" stroke-width="5"/>'
        f'<circle cx="26" cy="26" r="{r}" fill="none" stroke="{color}" stroke-width="5"'
        f' stroke-dasharray="{dash:.2f} {circ:.2f}" stroke-linecap="round"'
        f' transform="rotate(-90 26 26)"/>'
        f'<text x="26" y="30" text-anchor="middle" font-size="11" font-weight="800"'
        f' fill="{color}" font-family="Segoe UI,sans-serif">{pct}%</text>'
        f'</svg>'
    )


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

    agmt_type = agreement_meta.get("agreement_type", "")
    agmt_det  = agreement_meta.get("agreement_details") or {}

    h = [_SUMMARY_CSS, _LOAN_OVERVIEW_CSS, '<div class="sr">']

    # ── Loan Overview (rendered only when loan_metrics are provided) ──────────
    loan_overview = _build_loan_overview_html(agreement_meta.get("loan_metrics") or {})
    if loan_overview:
        h.append(loan_overview)

    # ── Header: title · date/location · verdict/rec · stat chips ─────────────
    display_title = _esc(policy_type or agmt_type or "Policy Compliance Analysis")
    meta_bits = []
    if agmt_det.get("agreement_date"):
        meta_bits.append(f'<span>{_esc(agmt_det["agreement_date"])}</span>')
    place = ", ".join(v for v in [agmt_det.get("city"), agmt_det.get("state")] if v)
    if place:
        meta_bits.append(f'<span>{_esc(place)}</span>')
    meta_html = ('<span class="ds-hdr-dot">·</span>'.join(meta_bits)) if meta_bits else ""

    circ = 251.33
    dash = round(circ * score / 100, 2)

    h.append(f'''<div class="ds-hdr">
  <div class="ds-hdr-title-block">
    <div class="ds-hdr-policy">{display_title}</div>
    <div class="ds-hdr-meta">
      <span style="color:{score_color};font-weight:700;">{verdict_label}</span>
      <span class="ds-hdr-dot">·</span>
      <span>{rec_label}</span>
      <span class="ds-hdr-dot">·</span>
      <span>{total} requirement{"s" if total != 1 else ""}</span>
      {"<span class='ds-hdr-dot'>·</span>" + meta_html if meta_html else ""}
    </div>
  </div>
  <div class="ds-chips">
    <div class="ds-chip total"><div class="ds-chip-n">{total}</div><div class="ds-chip-l">Total</div></div>
    <div class="ds-chip pass"><div class="ds-chip-n">{sat}</div><div class="ds-chip-l">Pass</div></div>
    <div class="ds-chip fail"><div class="ds-chip-n">{viol}</div><div class="ds-chip-l">Fail</div></div>
    <div class="ds-chip risky"><div class="ds-chip-n">{risky}</div><div class="ds-chip-l">Risky</div></div>
    <div class="ds-chip na"><div class="ds-chip-n">{not_addr}</div><div class="ds-chip-l">N/A</div></div>
  </div>
</div>''')

    # ── Main two-panel ────────────────────────────────────────────────────────
    h.append('<div class="ds-main">')

    # ── LEFT: 4 sectioned lists ───────────────────────────────────────────────
    na_reqs    = [r for r in reqs if r.get("status") == "NOT_ADDRESSED"]
    viol_reqs  = [r for r in reqs if r.get("status") == "VIOLATES"]
    risky_reqs = [r for r in reqs if r.get("status") == "RISKY"]
    risk_pts   = policy_analysis.get("risk_points", [])

    h.append('<div class="ds-left">')

    # Section 1 — Not Addressed
    h.append(
        f'<div class="ds-sec">'
        f'<div class="ds-sec-hdr na">'
        f'<span>Not Addressed</span>'
        f'<span class="ds-sec-badge">{len(na_reqs)}</span>'
        f'</div>'
    )
    if na_reqs:
        for r in na_reqs:
            h.append(
                f'<div class="ds-item">'
                f'<div class="ds-item-dot" style="background:#0d6efd;"></div>'
                f'<div class="ds-item-body">'
                f'<div class="ds-item-title">{_esc(r.get("requirement",""))}</div>'
                f'<div class="ds-item-sub">{_esc(r.get("reason",""))}</div>'
                f'</div></div>'
            )
    else:
        h.append('<div class="ds-sec-empty">None</div>')
    h.append('</div>')

    # Section 2 — Actions Required (violations)
    h.append(
        f'<div class="ds-sec">'
        f'<div class="ds-sec-hdr viol">'
        f'<span>Actions Required</span>'
        f'<span class="ds-sec-badge">{len(viol_reqs)}</span>'
        f'</div>'
    )
    if viol_reqs:
        for r in viol_reqs:
            h.append(
                f'<div class="ds-item">'
                f'<div class="ds-item-dot" style="background:#dc3545;"></div>'
                f'<div class="ds-item-body">'
                f'<div class="ds-item-title">{_esc(r.get("requirement",""))}</div>'
                f'<div class="ds-item-sub">{_esc(r.get("reason",""))}</div>'
                f'</div></div>'
            )
    else:
        h.append('<div class="ds-sec-empty">None</div>')
    h.append('</div>')

    # Section 3 — Conditions for Approval
    h.append(
        f'<div class="ds-sec">'
        f'<div class="ds-sec-hdr cond">'
        f'<span>Conditions for Approval</span>'
        f'<span class="ds-sec-badge">{len(conditions)}</span>'
        f'</div>'
    )
    if conditions:
        for i, cond in enumerate(conditions, 1):
            h.append(
                f'<div class="ds-item">'
                f'<div class="ds-item-num">{i}.</div>'
                f'<div class="ds-item-body">'
                f'<div class="ds-item-title">{_esc(cond)}</div>'
                f'</div></div>'
            )
    else:
        h.append('<div class="ds-sec-empty">No conditions.</div>')
    h.append('</div>')

    # Section 4 — Risky Points (risky requirements + risk_points text list)
    all_risky_count = len(risky_reqs) + len(risk_pts)
    h.append(
        f'<div class="ds-sec">'
        f'<div class="ds-sec-hdr risky">'
        f'<span>Risky Points</span>'
        f'<span class="ds-sec-badge">{all_risky_count}</span>'
        f'</div>'
    )
    if risky_reqs or risk_pts:
        for r in risky_reqs:
            h.append(
                f'<div class="ds-item">'
                f'<div class="ds-item-dot" style="background:#fd7e14;"></div>'
                f'<div class="ds-item-body">'
                f'<div class="ds-item-title">{_esc(r.get("requirement",""))}</div>'
                f'<div class="ds-item-sub">{_esc(r.get("reason",""))}</div>'
                f'</div></div>'
            )
        for rp in risk_pts:
            h.append(
                f'<div class="ds-item">'
                f'<div class="ds-item-dot" style="background:#fd7e14;"></div>'
                f'<div class="ds-item-body">'
                f'<div class="ds-item-title">{_esc(rp)}</div>'
                f'</div></div>'
            )
    else:
        h.append('<div class="ds-sec-empty">None</div>')
    h.append('</div>')

    h.append('</div>')  # .ds-left

    # ── RIGHT: big score donut + category circles ─────────────────────────────
    h.append('<div class="ds-right">')

    # Big score donut
    h.append(
        f'<div class="ds-score-center">'
        f'<svg width="130" height="130" viewBox="0 0 100 100" style="display:block;margin:0 auto;">'
        f'<circle cx="50" cy="50" r="40" fill="none" stroke="#e9ecef" stroke-width="7"/>'
        f'<circle cx="50" cy="50" r="40" fill="none" stroke="{score_color}" stroke-width="7"'
        f' stroke-dasharray="{dash} {circ}" stroke-linecap="round" transform="rotate(-90 50 50)"/>'
        f'<text x="50" y="44" text-anchor="middle" font-size="20" font-weight="800"'
        f' fill="{score_color}" font-family="Segoe UI,sans-serif">{score}%</text>'
        f'<text x="50" y="57" text-anchor="middle" font-size="8" font-weight="600"'
        f' fill="#6c757d" font-family="Segoe UI,sans-serif" letter-spacing="0.04em">COMPLIANCE</text>'
        f'</svg>'
        f'<div class="ds-score-verdict"><strong style="color:{score_color};">{verdict_label}</strong></div>'
        f'<div class="ds-score-rec">{rec_label}</div>'
        f'</div>'
    )

    # Category circles
    groups = _group_requirements(reqs)
    if groups:
        h.append('<div class="ds-cat-grid">')
        for cat, cat_reqs in groups.items():
            cat_score = _category_score(cat_reqs)
            color     = _score_color(cat_score)
            sat_c  = sum(1 for r in cat_reqs if r.get("status") == "SATISFIES")
            viol_c = sum(1 for r in cat_reqs if r.get("status") == "VIOLATES")
            risk_c = sum(1 for r in cat_reqs if r.get("status") == "RISKY")
            na_c   = sum(1 for r in cat_reqs if r.get("status") == "NOT_ADDRESSED")
            count_str = f"{sat_c}✓ {viol_c}✗ {risk_c}⚠ {na_c}–".replace(" 0✓","").replace(" 0✗","").replace(" 0⚠","").replace(" 0–","").strip()
            h.append(
                f'<div class="ds-cat-tile">'
                f'{_mini_donut(cat_score, color)}'
                f'<div class="ds-cat-tile-name">{_esc(cat)}</div>'
                f'<div class="ds-cat-tile-counts">{count_str}</div>'
                f'</div>'
            )
        h.append('</div>')

    h.append('</div>')  # .ds-right
    h.append('</div>')  # .ds-main

    # ── All requirements (dense table) ────────────────────────────────────────
    if reqs:
        badge_html = {
            "SATISFIES":     '<span class="badge badge-match" style="font-size:10px;padding:1px 7px;">Pass</span>',
            "VIOLATES":      '<span class="badge badge-viol"  style="font-size:10px;padding:1px 7px;">Fail</span>',
            "RISKY":         '<span class="badge badge-part"  style="font-size:10px;padding:1px 7px;">Risky</span>',
            "NOT_ADDRESSED": '<span class="badge badge-nf"    style="font-size:10px;padding:1px 7px;">N/A</span>',
        }
        h.append('<div class="ds-reqs">')
        h.append('<div class="ds-reqs-hdr">All Policy Requirements</div>')
        h.append('<table class="ds-rt"><thead><tr>'
                 '<th style="width:28px;">#</th>'
                 '<th>Requirement</th>'
                 '<th style="width:66px;">Status</th>'
                 '<th>Finding</th>'
                 '</tr></thead><tbody>')
        for i, req in enumerate(reqs, 1):
            st      = req.get("status", "NOT_ADDRESSED")
            st_badge = badge_html.get(st) or f'<span class="badge">{st}</span>'
            h.append(
                f'<tr>'
                f'<td class="ds-rt-num">{i}</td>'
                f'<td class="ds-rt-req">{_esc(req.get("requirement",""))}</td>'
                f'<td>{st_badge}</td>'
                f'<td class="ds-rt-find">{_esc(req.get("reason",""))}</td>'
                f'</tr>'
            )
        h.append('</tbody></table></div>')

    h.append('</div>')  # .sr
    return "\n".join(h)

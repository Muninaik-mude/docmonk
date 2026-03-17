"""
Three-part test for the new extract-rules → analyze split flow.

Tests
─────
  1. Extract atomic rules from a policy PDF and generate the rule-document
     HTML preview (highlighted source_excerpts + left-panel rule list).
  2. Analyze a loan application DOCX against those extracted rules.
  3. Analyze a loan application PDF  against those extracted rules.

Usage
─────
  First run (calls AI for all three steps):
      python test_extract_rules.py

  Subsequent runs (skip AI, reuse saved JSON from first run):
      python test_extract_rules.py --cached

Output (written to OUT_DIR below)
──────────────────────────────────
  rules.json                        — extracted rules (reused by tests 2 & 3)
  rule_document_preview.html        — test 1 HTML preview
  analysis_docx.json                — test 2 AI result cache
  report_docx.html                  — test 2 analysis report
  analysis_pdf.json                 — test 3 AI result cache
  report_pdf.html                   — test 3 analysis report
"""
import json
import pathlib
import subprocess
import sys
import os
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Configure paths here ───────────────────────────────────────────────────────
POLICY_PDF  = r"E:/sample-housing-lending-policies.pdf"
LOAN_DOCX   = r"E:/sample_loan_applications/loan_application_YCLF_2026_0087_sofia_clearwater.docx"
LOAN_PDF    = r"E:/sample_loan_applications/loan_application_YCLF_2026_0087_sofia_clearwater.pdf"
POLICY_TYPE = "Housing Lending Policy"
OUT_DIR     = pathlib.Path("E:/policy_analysis_reports/extract_rules_test")
# ──────────────────────────────────────────────────────────────────────────────

USE_CACHE = "--cached" in sys.argv
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Django setup ───────────────────────────────────────────────────────────────
project_root = pathlib.Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

import fitz  # PyMuPDF
import base64

from analyzer.services import pdf_service
from policy.services.policy_rule_service import extract_rules_from_policy
from policy.services.policy_service import analyze_document_against_rules
from policy.services.policy_report_service import (
    generate_rule_extraction_report,
    generate_policy_report,
)


def _open(path: pathlib.Path):
    subprocess.Popen(["cmd", "/c", "start", "", str(path)])


def _elapsed(t0: float) -> str:
    return f"{time.time() - t0:.1f}s"


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 1 — Extract rules from policy document + generate HTML preview
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("TEST 1 — Extract rules from rule document + generate HTML preview")
print("=" * 70)

rules_json_path    = OUT_DIR / "rules.json"
preview_html_path  = OUT_DIR / "rule_document_preview.html"

# ── Read policy PDF ────────────────────────────────────────────────────────────
policy_bytes = pathlib.Path(POLICY_PDF).read_bytes()
policy_filename = pathlib.Path(POLICY_PDF).name
with fitz.open(POLICY_PDF) as pdf:
    policy_text = "\n".join(page.get_text() for page in pdf)
print(f"Policy document : {policy_filename}  ({len(policy_text):,} chars)")

# ── Extract rules (or load from cache) ────────────────────────────────────────
if USE_CACHE and rules_json_path.exists():
    rules = json.loads(rules_json_path.read_text(encoding="utf-8"))
    print(f"Rules           : loaded from cache ({len(rules)} rules)")
else:
    if USE_CACHE:
        print("WARNING: no cached rules.json found — running AI anyway")
    print("Calling AI for rule extraction...")
    t0 = time.time()
    extraction_result = extract_rules_from_policy(
        policy_text=policy_text,
        policy_type=POLICY_TYPE,
        document_filename=policy_filename,
    )
    rules = extraction_result["rules"]
    summary = extraction_result["extraction_summary"]
    print(
        f"Extraction done in {_elapsed(t0)}"
        f"  total_rules={summary['total_rules']}"
        f"  categories={summary['categories']}"
    )
    rules_json_path.write_text(
        json.dumps(rules, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Rules saved     : {rules_json_path.name}")

# ── Generate rule-document HTML preview ───────────────────────────────────────
print("Generating HTML preview...")
t0 = time.time()
preview_html = generate_rule_extraction_report(
    rules=rules,
    policy_type=POLICY_TYPE,
    doc_bytes=policy_bytes,
    file_type="pdf",
    document_filename=policy_filename,
)
preview_html_path.write_text(preview_html, encoding="utf-8")
print(
    f"Preview done in {_elapsed(t0)}"
    f"  ({len(preview_html):,} chars)"
    f"  → {preview_html_path.name}"
)
_open(preview_html_path)
print()

# ── Print rule summary ─────────────────────────────────────────────────────────
print(f"  Extracted {len(rules)} atomic rules:")
for i, r in enumerate(rules, 1):
    badge = {"mandatory": "🔴", "conditional": "🟡", "informational": "🟢"}.get(
        r.get("requirement_type", "mandatory"), "⚪"
    )
    print(f"  {i:>2}. {badge} [{r.get('category', '?'):15s}]  {r.get('title', r.get('rule_id', ''))}")
print()


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 2 — Analyze loan application DOCX against extracted rules
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("TEST 2 — Analyze DOCX loan application against extracted rules")
print("=" * 70)

docx_json_path = OUT_DIR / "analysis_docx.json"
docx_html_path = OUT_DIR / "report_docx.html"
docx_bytes     = pathlib.Path(LOAN_DOCX).read_bytes()
docx_filename  = pathlib.Path(LOAN_DOCX).name

# ── Extract text from DOCX ─────────────────────────────────────────────────────
text_blocks  = pdf_service.extract_text_blocks(docx_filename, docx_bytes)
docx_text    = pdf_service.get_full_text(text_blocks)
print(f"Loan document   : {docx_filename}  ({len(docx_text):,} chars)")

# ── Run analysis (or load from cache) ─────────────────────────────────────────
if USE_CACHE and docx_json_path.exists():
    analysis_docx = json.loads(docx_json_path.read_text(encoding="utf-8"))
    print(f"Analysis        : loaded from cache")
else:
    if USE_CACHE:
        print("WARNING: no cached analysis_docx.json — running AI anyway")
    print(f"Calling AI for DOCX analysis ({len(rules)} rules)...")
    t0 = time.time()
    analysis_docx = analyze_document_against_rules(
        document_text=docx_text,
        policy_type=POLICY_TYPE,
        rules=rules,
    )
    print(
        f"Analysis done in {_elapsed(t0)}"
        f"  verdict={analysis_docx.get('overall_verdict')}"
        f"  score={analysis_docx.get('compliance_score')}"
        f"  reqs={len(analysis_docx.get('policy_requirements', []))}"
    )
    docx_json_path.write_text(
        json.dumps(analysis_docx, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Analysis saved  : {docx_json_path.name}")

# ── Generate analysis report ───────────────────────────────────────────────────
print("Generating DOCX analysis report...")
t0 = time.time()
report_docx = generate_policy_report(
    policy_analysis=analysis_docx,
    policy_type=POLICY_TYPE,
    doc_bytes=docx_bytes,
    file_type="docx",
)
docx_html_path.write_text(report_docx, encoding="utf-8")
print(
    f"Report done in {_elapsed(t0)}"
    f"  ({len(report_docx):,} chars)"
    f"  → {docx_html_path.name}"
)
_open(docx_html_path)

# ── Print per-rule results ─────────────────────────────────────────────────────
reqs = analysis_docx.get("policy_requirements", [])
status_icon = {"SATISFIES": "✅", "VIOLATES": "❌", "RISKY": "⚠️", "NOT_ADDRESSED": "○"}
print(f"\n  Results ({len(reqs)} rules checked):")
for r in reqs:
    icon = status_icon.get(r.get("status", ""), "?")
    src  = f"  [{r.get('source_document', '')}]" if r.get("source_document") else ""
    print(f"  {icon} {r.get('rule_reference', r.get('rule_id', ''))}{src}")
print()


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 3 — Analyze loan application PDF against extracted rules
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("TEST 3 — Analyze PDF loan application against extracted rules")
print("=" * 70)

pdf_json_path = OUT_DIR / "analysis_pdf.json"
pdf_html_path = OUT_DIR / "report_pdf.html"
loan_pdf_bytes = pathlib.Path(LOAN_PDF).read_bytes()
loan_pdf_name  = pathlib.Path(LOAN_PDF).name

# ── Extract text from PDF ──────────────────────────────────────────────────────
with fitz.open(LOAN_PDF) as pdoc:
    loan_pdf_text = "\n".join(page.get_text() for page in pdoc)
print(f"Loan document   : {loan_pdf_name}  ({len(loan_pdf_text):,} chars)")

# ── Run analysis (or load from cache) ─────────────────────────────────────────
if USE_CACHE and pdf_json_path.exists():
    analysis_pdf = json.loads(pdf_json_path.read_text(encoding="utf-8"))
    print(f"Analysis        : loaded from cache")
else:
    if USE_CACHE:
        print("WARNING: no cached analysis_pdf.json — running AI anyway")
    print(f"Calling AI for PDF analysis ({len(rules)} rules)...")
    t0 = time.time()
    analysis_pdf = analyze_document_against_rules(
        document_text=loan_pdf_text,
        policy_type=POLICY_TYPE,
        rules=rules,
    )
    print(
        f"Analysis done in {_elapsed(t0)}"
        f"  verdict={analysis_pdf.get('overall_verdict')}"
        f"  score={analysis_pdf.get('compliance_score')}"
        f"  reqs={len(analysis_pdf.get('policy_requirements', []))}"
    )
    pdf_json_path.write_text(
        json.dumps(analysis_pdf, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Analysis saved  : {pdf_json_path.name}")

# ── Generate analysis report ───────────────────────────────────────────────────
print("Generating PDF analysis report...")
t0 = time.time()
report_pdf = generate_policy_report(
    policy_analysis=analysis_pdf,
    policy_type=POLICY_TYPE,
    doc_bytes=loan_pdf_bytes,
    file_type="pdf",
)
pdf_html_path.write_text(report_pdf, encoding="utf-8")
print(
    f"Report done in {_elapsed(t0)}"
    f"  ({len(report_pdf):,} chars)"
    f"  → {pdf_html_path.name}"
)
_open(pdf_html_path)

# ── Print per-rule results ─────────────────────────────────────────────────────
reqs = analysis_pdf.get("policy_requirements", [])
print(f"\n  Results ({len(reqs)} rules checked):")
for r in reqs:
    icon = status_icon.get(r.get("status", ""), "?")
    src  = f"  [{r.get('source_document', '')}]" if r.get("source_document") else ""
    print(f"  {icon} {r.get('rule_reference', r.get('rule_id', ''))}{src}")
print()


# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("ALL TESTS COMPLETE")
print("=" * 70)
print(f"Rules extracted      : {len(rules)}")
print(f"DOCX analysis verdict: {analysis_docx.get('overall_verdict')}  "
      f"score={analysis_docx.get('compliance_score')}")
print(f"PDF  analysis verdict: {analysis_pdf.get('overall_verdict')}  "
      f"score={analysis_pdf.get('compliance_score')}")
print(f"\nOutputs saved to: {OUT_DIR}")
print("  rule_document_preview.html  — rule doc with source_excerpts highlighted")
print("  report_docx.html            — DOCX loan analysis report")
print("  report_pdf.html             — PDF  loan analysis report")
print()
print("Next run (skip AI, reuse saved JSON):")
print("  python test_extract_rules.py --cached")

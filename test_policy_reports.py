"""
Full policy report test — runs AI analysis and saves JSON so re-runs are free.

Usage (first run — calls AI):
    python test_policy_reports.py

Usage (subsequent runs — skips AI, uses saved JSON):
    python test_policy_reports.py --cached

Output files in E:/pdf_render_test/:
    report_<stem>.html   — rendered HTML report
    analysis_<stem>.json — saved AI response (reused on --cached)
"""
import json
import pathlib
import subprocess
import sys
import os
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Configure ──────────────────────────────────────────────────────────────────
PDFS = [
    r"E:/loan_application_YCLF_2026_0087_sofia_clearwater.pdf",
    r"E:/loan_application_YCLF_2026_0094_raymond_swiftwind.pdf",
    r"E:/loan_application_YCLF_2026_0101_delores_runningbear.pdf",
]
POLICY_PDF  = r"E:/sample-housing-lending-policies.pdf"
POLICY_TYPE = "Housing Lending Policy"
OUT_DIR     = pathlib.Path("E:/pdf_render_test")
# ──────────────────────────────────────────────────────────────────────────────

USE_CACHE = "--cached" in sys.argv

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Django / project setup ────────────────────────────────────────────────────
project_root = pathlib.Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    import django
    django.setup()
except Exception as e:
    print(f"WARNING: Django setup failed ({e})")

import fitz  # PyMuPDF
from policy.services.policy_service import analyze_document_against_policy
from policy.services.policy_report_service import generate_policy_report

# ── Extract policy text once ──────────────────────────────────────────────────
with fitz.open(POLICY_PDF) as pdoc:
    policy_text = "\n".join(p.get_text() for p in pdoc)
print(f"Policy: {len(policy_text):,} chars  |  mode: {'CACHED' if USE_CACHE else 'LIVE AI'}\n")

# ── Process each PDF ──────────────────────────────────────────────────────────
for pdf_path in PDFS:
    pdf   = pathlib.Path(pdf_path)
    stem  = pdf.stem
    json_out = OUT_DIR / f"analysis_{stem}.json"
    html_out = OUT_DIR / f"report_{stem}.html"

    print(f"── {pdf.name} ──")

    # ── Load or generate policy_analysis ─────────────────────────────────────
    if USE_CACHE and json_out.exists():
        policy_analysis = json.loads(json_out.read_text(encoding="utf-8"))
        print(f"  Loaded cached JSON: {json_out.name}")
    else:
        if USE_CACHE:
            print(f"  WARNING: no cached JSON found at {json_out.name}, running AI anyway")
        with fitz.open(str(pdf)) as ddoc:
            doc_text = "\n".join(p.get_text() for p in ddoc)
        print(f"  Doc text: {len(doc_text):,} chars — calling AI...")
        t0 = time.time()
        policy_analysis = analyze_document_against_policy(
            document_text=doc_text,
            policy_type=POLICY_TYPE,
            policy_text=policy_text,
        )
        elapsed = time.time() - t0
        print(
            f"  AI done in {elapsed:.1f}s"
            f"  verdict={policy_analysis.get('overall_verdict')}"
            f"  score={policy_analysis.get('compliance_score')}"
            f"  reqs={len(policy_analysis.get('policy_requirements', []))}"
        )
        # Save JSON for future --cached runs
        json_out.write_text(
            json.dumps(policy_analysis, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  JSON saved: {json_out.name}")

    # ── Generate report HTML ──────────────────────────────────────────────────
    report_html = generate_policy_report(
        policy_analysis=policy_analysis,
        policy_type=POLICY_TYPE,
        doc_bytes=pdf.read_bytes(),
        file_type="pdf",
    )
    html_out.write_text(report_html, encoding="utf-8")
    print(f"  Report: {html_out.name}  ({len(report_html):,} chars)")
    subprocess.Popen(["cmd", "/c", "start", "", str(html_out)])
    print()

print("All done.")

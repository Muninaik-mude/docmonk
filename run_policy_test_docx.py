"""
Quick test script for running policy analysis against a DOCX file.
Usage: edit the DOCX_PATH and POLICY_PDF_PATH below, then run:
    python run_policy_test_docx.py
"""
import base64
import json
import pathlib
import subprocess
import urllib.request

import fitz  # pip install pymupdf

# ── Configure these paths ──────────────────────────────────────────────────────
DOCX_PATH       = r"E:/sample_loan_applications/loan_application_YCLF_2026_0051.docx"
POLICY_PDF_PATH = r"E:/sample-housing-lending-policies.pdf"
API_URL         = "http://localhost:8000/v1/policy/analyze"
OUT_DIR         = pathlib.Path("E:/policy_analysis_reports")
# ──────────────────────────────────────────────────────────────────────────────

# 1. Read DOCX → base64 (binary read, same as any binary file)
with open(DOCX_PATH, "rb") as f:
    doc_b64 = base64.b64encode(f.read()).decode()
print(f"DOCX encoded: {len(doc_b64):,} base64 chars")

# 2. Read policy text from PDF
with fitz.open(POLICY_PDF_PATH) as pdf:
    policy_text = "\n".join(page.get_text() for page in pdf)
print(f"Policy text: {len(policy_text):,} chars")

# 3. Build payload
#    Pass loan_metrics inside agreement_meta to get the enhanced Loan Overview section.
#    Remove or simplify loan_metrics if not applicable to your document.
payload = json.dumps({
    "document_base64":  doc_b64,
    "document_filename": pathlib.Path(DOCX_PATH).name,
    "policy_type":      "Housing Lending Policy",
    "agreement_type":   "Housing Loan Application",
    "policy_text":      policy_text,

    # ── Optional: structure loan metrics for the enhanced Loan Overview section ──
    # "agreement_details": {},
    # "parties": {},

    # Uncomment and fill in to activate metric tiles + 4-table loan overview:
    # "loan_metrics": {
    #     "project_name":    "My Project Name",
    #     "location":        "City, State",
    #     "loan_amount":     "$8,500,000",
    #     "loan_rate":       "6.25%",
    #     "loan_term":       "30 months",
    #     "loan_type":       "Bridge Loan",
    #     "dscr":            {"value": "1.08x", "min": "1.15x", "status": "VIOLATES"},
    #     "ltv":             {"value": "83.3%", "max": "90%",   "status": "SATISFIES"},
    #     "ltc":             {"value": "72%",   "max": "92%",   "status": "SATISFIES"},
    #     "debt_yield":      {"value": "4.85%", "min": "6%",    "status": "VIOLATES"},
    #     "ami_level":       {"value": "60%",   "required": "<=60%", "status": "SATISFIES"},
    #     "exceptions":      {"count": 7, "total_checks": 11},
    #     "env_clearance":   "Pending",
    #     "reserves":        "4.5 mo (min 6)",
    # },
}).encode("utf-8")

# 4. Call API
req = urllib.request.Request(
    API_URL,
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
print("Calling API…")
with urllib.request.urlopen(req, timeout=300) as resp:
    result = json.loads(resp.read().decode())

job_id = result.get("job_id", "unknown")
print(f"Job: {job_id} | Status: {result.get('status')}")

# 5. Save HTML reports
OUT_DIR.mkdir(exist_ok=True)

report_path  = OUT_DIR / f"policy_report_{job_id}.html"
summary_path = OUT_DIR / f"policy_summary_{job_id}.html"

if result.get("report_md_base64"):
    report_path.write_bytes(base64.b64decode(result["report_md_base64"]))
    print("Report :", report_path)

if result.get("summary_md_base64"):
    summary_path.write_bytes(base64.b64decode(result["summary_md_base64"]))
    print("Summary:", summary_path)

# 6. Dump full structured JSON (strip large base64 HTML blobs — designer needs JSON only)
json_dump = {k: v for k, v in result.items() if k not in ("report_md_base64", "summary_md_base64")}
json_path = OUT_DIR / f"api_response_{job_id}.json"
json_path.write_text(json.dumps(json_dump, indent=2, ensure_ascii=False), encoding="utf-8")
print("JSON   :", json_path)

# 7. Open in browser
if report_path.exists():
    subprocess.Popen(["cmd", "/c", "start", "", str(report_path)])
if summary_path.exists():
    subprocess.Popen(["cmd", "/c", "start", "", str(summary_path)])
subprocess.Popen(["cmd", "/c", "start", "", str(json_path)])

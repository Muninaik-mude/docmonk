import base64, fitz, json, urllib.request, pathlib, subprocess

# Policy text from PDF
with fitz.open(r"E:/sample-housing-lending-policies.pdf") as pdf:
    policy_text = "\n".join(page.get_text() for page in pdf)

# Use the actual loan application markdown file
with open(r"E:/loan_application_YCLF_2026_0051.md", "rb") as f:
    doc_b64 = base64.b64encode(f.read()).decode()

payload = json.dumps({
    "document_base64": doc_b64,
    "document_filename": "loan_application_YCLF_2026_0051.md",
    "agreement_type": "Housing Loan Application",
    "policy_type": "Housing Lending Policy",
    "policy_text": policy_text,
}).encode("utf-8")

req = urllib.request.Request(
    "http://localhost:8000/v1/analyze",
    data=payload, headers={"Content-Type": "application/json"}, method="POST"
)

print("Calling API...")
with urllib.request.urlopen(req, timeout=180) as resp:
    result = json.loads(resp.read().decode("utf-8"))

job_id  = result.get("job_id", "unknown")
out_dir = pathlib.Path("E:/policy_analysis_reports")
out_dir.mkdir(exist_ok=True)

# ── HTML reports (base64 → file) ─────────────────────────────────────────────
report_path  = out_dir / f"policy_report_{job_id}.html"
summary_path = out_dir / f"policy_summary_{job_id}.html"

if result.get("report_md_base64"):
    report_path.write_bytes(base64.b64decode(result["report_md_base64"]))
    print("Report HTML:", report_path)
if result.get("summary_md_base64"):
    summary_path.write_bytes(base64.b64decode(result["summary_md_base64"]))
    print("Summary HTML:", summary_path)

# ── Full JSON dump (for UI designer) ─────────────────────────────────────────
# Strip the large base64 HTML blobs — designer only needs structured JSON fields
json_dump = {k: v for k, v in result.items() if k not in ("report_md_base64", "summary_md_base64")}
json_path = out_dir / f"api_response_{job_id}.json"
json_path.write_text(json.dumps(json_dump, indent=2, ensure_ascii=False), encoding="utf-8")
print("JSON dump:", json_path)

subprocess.Popen(["cmd", "/c", "start", "", str(report_path)])
subprocess.Popen(["cmd", "/c", "start", "", str(summary_path)])
subprocess.Popen(["cmd", "/c", "start", "", str(json_path)])
print("Done. Job:", job_id, "| Status:", result.get("status"))

"""
Dumps the full raw JSON response from the policy analysis API to a file.
Run: python dump_api_response.py
"""
import base64, fitz, json, pathlib, urllib.request

DOCX_PATH       = r"E:/loan_application_YCLF_2026_0051.docx"
POLICY_PDF_PATH = r"E:/sample-housing-lending-policies.pdf"
API_URL         = "http://localhost:8000/v1/analyze"
OUT_FILE        = pathlib.Path("E:/policy_api_response.json")

with open(DOCX_PATH, "rb") as f:
    doc_b64 = base64.b64encode(f.read()).decode()

with fitz.open(POLICY_PDF_PATH) as pdf:
    policy_text = "\n".join(page.get_text() for page in pdf)

payload = json.dumps({
    "document_base64":   doc_b64,
    "document_filename": pathlib.Path(DOCX_PATH).name,
    "policy_type":       "Housing Lending Policy",
    "agreement_type":    "Housing Loan Application",
    "policy_text":       policy_text,
}).encode("utf-8")

req = urllib.request.Request(
    API_URL, data=payload,
    headers={"Content-Type": "application/json"}, method="POST"
)
print("Calling API…")
with urllib.request.urlopen(req, timeout=300) as resp:
    raw = resp.read().decode()

# Pretty-print JSON (skip the large base64 blobs for readability)
result = json.loads(raw)

# Remove huge base64 fields so the JSON is human-readable
result.pop("report_md_base64",  None)
result.pop("summary_md_base64", None)
result.pop("policy_analysis",   None)   # duplicated inside policy_summary_data

OUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(f"Saved to: {OUT_FILE}")
print(json.dumps(result, indent=2)[:3000], "\n... (see file for full output)")

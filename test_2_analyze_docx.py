"""
TEST 2 — Analyze a DOCX loan application against the rules extracted in Test 1.

Reads rules.json written by test_1_extract_rules.py.

Usage:
    python test_2_analyze_docx.py
    python test_2_analyze_docx.py --cached   # reuse saved analysis_docx.json, regenerate HTML only
"""
import json, pathlib, subprocess, sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Configure ──────────────────────────────────────────────────────────────────
LOAN_DOCX   = r"E:/sample_loan_applications/loan_application_YCLF_2026_0087_sofia_clearwater.docx"
POLICY_TYPE = "Housing Lending Policy"
OUT_DIR     = pathlib.Path("E:/policy_analysis_reports/extract_rules_test")
# ──────────────────────────────────────────────────────────────────────────────

USE_CACHE = "--cached" in sys.argv

project_root = pathlib.Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

from analyzer.services import pdf_service
from policy.services.policy_service import analyze_document_against_rules
from policy.services.policy_report_service import generate_policy_report

rules_json_path = OUT_DIR / "rules.json"
analysis_path   = OUT_DIR / "analysis_docx.json"
report_path     = OUT_DIR / "report_docx.html"

# ── Load rules ─────────────────────────────────────────────────────────────────
if not rules_json_path.exists():
    print(f"ERROR: {rules_json_path} not found. Run test_1_extract_rules.py first.")
    sys.exit(1)
rules = json.loads(rules_json_path.read_text(encoding="utf-8"))
print(f"Rules  : {len(rules)} rules loaded from {rules_json_path.name}")

# ── Extract DOCX text ──────────────────────────────────────────────────────────
docx_bytes    = pathlib.Path(LOAN_DOCX).read_bytes()
docx_filename = pathlib.Path(LOAN_DOCX).name
text_blocks   = pdf_service.extract_text_blocks(docx_filename, docx_bytes)
doc_text      = pdf_service.get_full_text(text_blocks)
print(f"Doc    : {docx_filename}  ({len(doc_text):,} chars)  mode={'CACHED' if USE_CACHE else 'LIVE AI'}\n")

# ── Analyze (or load cache) ────────────────────────────────────────────────────
if USE_CACHE and analysis_path.exists():
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    print("Analysis loaded from cache.")
else:
    if USE_CACHE:
        print("WARNING: no analysis_docx.json found — running AI anyway")
    print(f"Calling AI ({len(rules)} rules)...")
    t0 = time.time()
    analysis = analyze_document_against_rules(
        document_text=doc_text,
        policy_type=POLICY_TYPE,
        rules=rules,
    )
    print(
        f"Done in {time.time()-t0:.1f}s"
        f"  |  verdict={analysis.get('overall_verdict')}"
        f"  score={analysis.get('compliance_score')}"
        f"  reqs={len(analysis.get('policy_requirements', []))}"
    )
    analysis_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved  : {analysis_path.name}")

# ── Generate report HTML ───────────────────────────────────────────────────────
print("\nGenerating report HTML...")
t0 = time.time()
report_html = generate_policy_report(
    policy_analysis=analysis,
    policy_type=POLICY_TYPE,
    doc_bytes=docx_bytes,
    file_type="docx",
)
report_path.write_text(report_html, encoding="utf-8")
print(f"Done in {time.time()-t0:.1f}s  |  {len(report_html):,} chars  →  {report_path.name}")
subprocess.Popen(["cmd", "/c", "start", "", str(report_path)])

# ── Per-rule results ───────────────────────────────────────────────────────────
reqs = analysis.get("policy_requirements", [])
icon = {"SATISFIES": "✅", "VIOLATES": "❌", "RISKY": "⚠️", "NOT_ADDRESSED": "○"}
print(f"\n{len(reqs)} rule results:")
for r in reqs:
    src = f"  [{r.get('source_document','')}]" if r.get("source_document") else ""
    print(f"  {icon.get(r.get('status',''), '?')}  {r.get('rule_reference', r.get('rule_id',''))}{src}")

print(f"\nDone. Run test_3_analyze_pdf.py next.")

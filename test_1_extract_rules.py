"""
TEST 1 — Extract atomic rules from the policy document + generate HTML preview.

Saves rules.json to OUT_DIR. Tests 2 and 3 read from this file.

Usage:
    python test_1_extract_rules.py
    python test_1_extract_rules.py --cached   # reuse saved rules.json, regenerate HTML only
"""
import json, pathlib, subprocess, sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Configure ──────────────────────────────────────────────────────────────────
POLICY_PDF  = r"E:/sample-housing-lending-policies.pdf"
POLICY_TYPE = "Housing Lending Policy"
OUT_DIR     = pathlib.Path("E:/policy_analysis_reports/extract_rules_test")
# ──────────────────────────────────────────────────────────────────────────────

USE_CACHE = "--cached" in sys.argv
OUT_DIR.mkdir(parents=True, exist_ok=True)

project_root = pathlib.Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

import fitz
from policy.services.policy_rule_service import extract_rules_from_policy
from policy.services.policy_report_service import generate_rule_extraction_report

rules_json_path   = OUT_DIR / "rules.json"
preview_html_path = OUT_DIR / "rule_document_preview.html"

policy_bytes    = pathlib.Path(POLICY_PDF).read_bytes()
policy_filename = pathlib.Path(POLICY_PDF).name
with fitz.open(POLICY_PDF) as pdf:
    policy_text = "\n".join(page.get_text() for page in pdf)
print(f"Policy : {policy_filename}  ({len(policy_text):,} chars)  mode={'CACHED' if USE_CACHE else 'LIVE AI'}\n")

# ── Extract rules ──────────────────────────────────────────────────────────────
if USE_CACHE and rules_json_path.exists():
    rules = json.loads(rules_json_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(rules)} rules from cache.")
else:
    if USE_CACHE:
        print("WARNING: no rules.json found — running AI anyway")
    print("Calling AI for rule extraction...")
    t0 = time.time()
    result = extract_rules_from_policy(
        policy_text=policy_text,
        policy_type=POLICY_TYPE,
        document_filename=policy_filename,
    )
    rules   = result["rules"]
    summary = result["extraction_summary"]
    print(f"Done in {time.time()-t0:.1f}s  |  {summary['total_rules']} rules  |  categories: {summary['categories']}")
    rules_json_path.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved  : {rules_json_path}")

# ── Generate HTML preview ──────────────────────────────────────────────────────
print("\nGenerating HTML preview...")
t0 = time.time()
html = generate_rule_extraction_report(
    rules=rules,
    policy_type=POLICY_TYPE,
    doc_bytes=policy_bytes,
    file_type="pdf",
    document_filename=policy_filename,
)
preview_html_path.write_text(html, encoding="utf-8")
print(f"Done in {time.time()-t0:.1f}s  |  {len(html):,} chars  →  {preview_html_path.name}")
subprocess.Popen(["cmd", "/c", "start", "", str(preview_html_path)])

# ── Print rule list ────────────────────────────────────────────────────────────
print(f"\n{len(rules)} extracted rules:")
badge = {"mandatory": "🔴", "conditional": "🟡", "informational": "🟢"}
for i, r in enumerate(rules, 1):
    b = badge.get(r.get("requirement_type", "mandatory"), "⚪")
    print(f"  {i:>2}. {b} [{r.get('category','?'):15s}]  {r.get('title', r.get('rule_id',''))}")

print(f"\nDone. Run test_2_analyze_docx.py next.")

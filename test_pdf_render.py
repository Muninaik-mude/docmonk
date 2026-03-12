"""
Offline PDF rendering test — NO AI call, NO Django server needed.

Usage:
  1. Set PDF_PATH  to your subject PDF file.
  2. Set JSON_PATH to a .json file containing the policy_analysis dict
     (the result["policy_analysis"] value from a previous API response).
     Saving JSON to a file avoids ALL Python string-escaping issues.
  3. Run:  python test_pdf_render.py
  4. Opens the generated HTML in your default browser.
"""
import json
import pathlib
import subprocess
import sys
import os

# ── Configure these ────────────────────────────────────────────────────────────
PDF_PATH  = r"E:/loan_application_YCLF_2026_0051.pdf"   # subject PDF
JSON_PATH = r"E:/policy_analysis.json"                  # policy_analysis JSON file

OUT_DIR   = pathlib.Path("E:/policy_render_test")
# ──────────────────────────────────────────────────────────────────────────────


# ── Kept for backwards-compat if user still wants to paste JSON inline ─────────
# Leave this empty string to use JSON_PATH instead.
POLICY_ANALYSIS_JSON = """
{
    "overall_verdict": "PARTIALLY_COMPLIANT",
    "compliance_score": 87,
    "summary": "The application meets most eligibility criteria, but the requested loan amount exceeds the policy‑defined maximum for a Home Purchase Loan, creating a direct violation. Credit history also presents a risk that should be mitigated before approval.",
    "policy_requirements": [
      {
        "requirement": "Property must be located in the Investment Area defined by YCLF.",
        "status": "SATISFIES",
        "reason": "The property is in Oglala Lakota County / Pine Ridge Reservation, which matches the stated investment area.",
        "relevant_text": "County / Reservation | Oglala Lakota County / Pine Ridge Reservation",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Loan product must be an eligible product; Home Purchase Loan is permitted.",
        "status": "SATISFIES",
        "reason": "Applicant selected Home Purchase Loan which is listed in the policy table.",
        "relevant_text": "Loan Product | Home Purchase Loan",
        "recommendation": null,
        "category": "Compliance"
      },
      {
        "requirement": "Requested loan amount must not exceed the lower of $100,000 or 99% of appraised value plus expenses.",
        "status": "VIOLATES",
        "reason": "Requested $162,000 exceeds the $100,000 cap (appraised value $158,000 → 99% = $156,420, lower of $100,000).",
        "relevant_text": "Loan Amount Requested | $162,000",
        "recommendation": "Reduce the loan amount to $100,000 or less to meet the policy maximum.",
        "category": "Financial"
      },
      {
        "requirement": "Minimum loan size for Home Purchase Loans is $10,000.",
        "status": "SATISFIES",
        "reason": "Requested amount $162,000 is well above the $10,000 minimum.",
        "relevant_text": "Loan Amount Requested | $162,000",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Ownership type must be fee simple.",
        "status": "SATISFIES",
        "reason": "Ownership type listed as Fee Simple.",
        "relevant_text": "Ownership Type | Fee Simple",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Down‑payment must be at least 1% of appraised value plus expenses.",
        "status": "SATISFIES",
        "reason": "1% of $158,000 = $1,580; down‑payment provided is $3,200.",
        "relevant_text": "Down Payment Amount | $3,200",
        "recommendation": null,
        "category": "Financial"
      },
      {
        "requirement": "Property must be the borrower's primary residence.",
        "status": "SATISFIES",
        "reason": "Applicant answered Yes to primary residence question.",
        "relevant_text": "Is this your primary residence? | Yes",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Homeowner's insurance must be in place.",
        "status": "SATISFIES",
        "reason": "Insurance policy provided (State Farm, Policy #SF-44029).",
        "relevant_text": "Homeowner's Insurance | State Farm — Policy #SF-44029",
        "recommendation": null,
        "category": "Compliance"
      },
      {
        "requirement": "Application fee of $100 must be paid and non‑refundable.",
        "status": "SATISFIES",
        "reason": "Fee paid via check #4482 and recorded in the application.",
        "relevant_text": "Application Fee Paid | $100 (Check No. 4482)",
        "recommendation": null,
        "category": "Documentation"
      },
      {
        "requirement": "Payroll deduction must be available (if recommended).",
        "status": "SATISFIES",
        "reason": "Both borrower and co‑borrower indicated payroll deduction is available and employer confirmed.",
        "relevant_text": "Payroll Deduction Available? | Yes — Employer Confirmed",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Borrower must belong to the target market (enrolled member, spouse, or pending enrollment).",
        "status": "SATISFIES",
        "reason": "Borrower pending enrollment; spouse is an enrolled member of the Oglala Sioux Tribe.",
        "relevant_text": "Tribal Membership Status | Non-member — Enrollment Pending; Co‑Borrower: Enrolled Member — Oglala Sioux Tribe",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "All declarations must be answered truthfully (U.S. citizen, no lawsuits, no recent bankruptcy/foreclosure, etc.).",
        "status": "SATISFIES",
        "reason": "All declaration answers are Yes or No as required and indicate no disqualifying issues.",
        "relevant_text": "Declaration answers show U.S. citizen, no lawsuits, no bankruptcy, no foreclosure, etc.",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Applicant must agree to annual credit checks.",
        "status": "SATISFIES",
        "reason": "Applicant answered Yes to annual credit checks.",
        "relevant_text": "Do you agree to annual credit checks? | Yes",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Applicant must agree to annual meetings with Homebuyer Consultant.",
        "status": "SATISFIES",
        "reason": "Applicant answered Yes to annual meetings.",
        "relevant_text": "Do you agree to annual meetings with Homebuyer Consultant? | Yes",
        "recommendation": null,
        "category": "Other"
      },
      {
        "requirement": "Applicants with problematic credit history must demonstrate potential to recover within five years.",
        "status": "RISKY",
        "reason": "Borrower has a 60‑day late payment in 2022 (within five years) but no explicit recovery plan is provided; credit scores are low (581/634).",
        "relevant_text": "Past Delinquencies | 1x 60-day late payment in 2022 (medical emergency); Estimated Credit Score (Borrower) | 581; (Co‑Borrower) | 634",
        "recommendation": "Provide a documented credit‑rehabilitation plan or additional collateral to mitigate credit risk.",
        "category": "Other"
      }
    ],
    "risk_points": [
      "Low credit scores and a recent 60‑day delinquency create credit risk; a recovery plan is not documented."
    ],
    "approval_recommendation": "CONDITIONAL_APPROVE",
    "conditions": [
      "Reduce the loan amount to $100,000 or less to comply with the maximum loan size for Home Purchase Loans."
    ],
    "verdict_label": "Partially Compliant",
    "rec_label": "Conditional Approve",
    "policy_type": "Housing Lending Policy",
    "stats": {
      "total": 15,
      "satisfies": 13,
      "violates": 1,
      "risky": 1,
      "not_addressed": 0
    },
    "issues": [
      {
        "requirement": "Requested loan amount must not exceed the lower of $100,000 or 99% of appraised value plus expenses.",
        "status": "VIOLATES",
        "status_label": "Violation",
        "reason": "Requested $162,000 exceeds the $100,000 cap (appraised value $158,000 → 99% = $156,420, lower of $100,000).",
        "recommendation": "Reduce the loan amount to $100,000 or less to meet the policy maximum.",
        "relevant_text": "Loan Amount Requested | $162,000"
      },
      {
        "requirement": "Applicants with problematic credit history must demonstrate potential to recover within five years.",
        "status": "RISKY",
        "status_label": "Risky",
        "reason": "Borrower has a 60‑day late payment in 2022 (within five years) but no explicit recovery plan is provided; credit scores are low (581/634).",
        "recommendation": "Provide a documented credit‑rehabilitation plan or additional collateral to mitigate credit risk.",
        "relevant_text": "Past Delinquencies | 1x 60-day late payment in 2022 (medical emergency); Estimated Credit Score (Borrower) | 581; (Co‑Borrower) | 634"
      }
    ],
    "grouped_categories": [
      {
        "name": "Financial",
        "score": 50,
        "total": 2,
        "satisfies": 1,
        "violates": 1,
        "risky": 0,
        "not_addressed": 0
      },
      {
        "name": "Documentation",
        "score": 100,
        "total": 1,
        "satisfies": 1,
        "violates": 0,
        "risky": 0,
        "not_addressed": 0
      },
      {
        "name": "Compliance",
        "score": 100,
        "total": 2,
        "satisfies": 2,
        "violates": 0,
        "risky": 0,
        "not_addressed": 0
      },
      {
        "name": "Other",
        "score": 95,
        "total": 10,
        "satisfies": 9,
        "violates": 0,
        "risky": 1,
        "not_addressed": 0
      }
    ],
    "path_to_approval": {
      "current_score": 87,
      "projected_score": 93,
      "improvement_pp": 6,
      "exceptions_to_resolve": 1,
      "projected_verdict": "APPROVE",
      "steps": [
        "Reduce the loan amount to $100,000 or less to comply with the maximum loan size for Home Purchase Loans."
      ]
    },
    "jurisdiction": {
      "jurisdiction": "",
      "agreement_type": "",
      "applicable_laws": [],
      "checklist": []
    }
  }
"""
# ──────────────────────────────────────────────────────────────────────────────


def main():
    # ── Validate PDF path ──────────────────────────────────────────────────────
    pdf_path = pathlib.Path(PDF_PATH)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {PDF_PATH}")
        sys.exit(1)

    pdf_bytes = pdf_path.read_bytes()
    print(f"PDF loaded: {len(pdf_bytes):,} bytes  ({pdf_path.name})")

    # ── Load policy analysis JSON ──────────────────────────────────────────────
    # Prefer JSON_PATH (file) over the inline POLICY_ANALYSIS_JSON string.
    inline = POLICY_ANALYSIS_JSON.strip()
    if inline:
        # Inline string provided — parse it directly.
        try:
            policy_analysis = json.loads(inline)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid inline JSON: {e}")
            print("Tip: save your JSON to a file and set JSON_PATH instead.")
            sys.exit(1)
    else:
        # Load from file — no Python string-escaping issues.
        json_path = pathlib.Path(JSON_PATH)
        if not json_path.exists():
            print(f"ERROR: JSON file not found at {JSON_PATH}")
            sys.exit(1)
        try:
            policy_analysis = json.loads(json_path.read_text(encoding="utf-8"))
            print(f"JSON loaded from: {json_path.name}")
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in {json_path.name}: {e}")
            sys.exit(1)

    reqs = policy_analysis.get("policy_requirements", [])
    print(f"Policy requirements: {len(reqs)}")
    for r in reqs:
        rt = (r.get("relevant_text") or "")[:60].replace("\n", " ")
        print(f"  [{r.get('status','?'):14s}]  relevant_text: {rt!r}")

    # ── Add project root to sys.path so Django apps are importable ─────────────
    project_root = pathlib.Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    # Minimal Django setup (only needed for imports; no DB used here)
    try:
        import django
        django.setup()
    except Exception as e:
        print(f"WARNING: Django setup failed ({e}) — trying direct import anyway")

    # ── Import rendering functions directly ────────────────────────────────────
    from policy.services.policy_report_service import (
        _build_highlight_map,
        _pdf_to_html,
        _DOC_CSS,
        _TOOLTIP_JS,
    )

    # ── Build highlight map from requirements ──────────────────────────────────
    hl_map = _build_highlight_map(reqs)
    print(f"\nHighlight map entries (non-empty relevant_text, non-NOT_ADDRESSED): {len(hl_map)}")
    for hl_text, status, req, reason in hl_map:
        print(f"  [{status:14s}]  text: {hl_text[:60]!r}")

    # ── Render PDF → HTML ──────────────────────────────────────────────────────
    print("\nRendering PDF -> HTML (absolute positioning)...")
    doc_html = _pdf_to_html(pdf_bytes, hl_map)
    print(f"HTML generated: {len(doc_html):,} chars")

    # ── Wrap with legend + tooltip JS (same as generate_policy_report) ─────────
    legend = (
        '<div class="pdv-legend">'
        '<span class="leg-sat">&#x2714; Satisfies</span>'
        '<span class="leg-vio">&#x2716; Violates</span>'
        '<span class="leg-rsk">&#x26A0; Risky</span>'
        '<span style="color:#9ca3af;font-size:11px;">— Hover highlighted text for details</span>'
        '</div>'
    )
    full_html = "\n".join([_DOC_CSS, legend, doc_html, _TOOLTIP_JS])

    # ── Save output ────────────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"render_test_{pdf_path.stem}.html"
    out_path.write_text(full_html, encoding="utf-8")
    print(f"\nOutput saved: {out_path}")

    # ── Open in browser ────────────────────────────────────────────────────────
    subprocess.Popen(["cmd", "/c", "start", "", str(out_path)])
    print("Opened in browser.")


if __name__ == "__main__":
    main()

"""
Policy rule extraction service.

Extracts atomic, checkable compliance rules from a policy/rule document.
Each rule is a structured object that can be stored by the caller and reused
across multiple loan-document analyses — avoiding repeated rule extraction
and eliminating variance in rule counts between runs.
"""
import json
import logging

from policy.services.policy_ai_service import call_ai as _call_ai, safe_json_parse as _safe_json_parse, PolicyRateLimitError

logger = logging.getLogger(__name__)

# Hard cap to avoid overflowing the model's context window.
# 80,000 chars ≈ 20,000 tokens at ~4 chars/token — safely within most providers.
_MAX_POLICY_CHARS = 80_000


# ── Prompts ────────────────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You are a precise compliance analyst. Your task is to extract every atomic, \
checkable compliance rule from ANY policy document — loan policies, grant \
programs, insurance underwriting guides, or any other regulatory document.

════════════════════════════════════════════════════════
MENTAL MODEL: HOW A COMPLIANCE ANALYST THINKS
════════════════════════════════════════════════════════

Before reading a single word of the document, internalize these two questions.
Apply them to EVERY sentence you encounter:

  ┌─────────────────────────────────────────────────────┐
  │ Q1 — THE BORROWER TEST                              │
  │ "If a borrower submitted their application RIGHT    │
  │  NOW, could this condition be checked against       │
  │  something they provided?"                          │
  │  YES → extract it.   NO → skip it.                 │
  └─────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────┐
  │ Q2 — THE LENDER-ACTION TEST                         │
  │ "Is the subject of this sentence a staff role,      │
  │  committee, or the lender performing an internal    │
  │  action?"                                           │
  │  YES → skip it.   NO → continue to Q1.             │
  └─────────────────────────────────────────────────────┘

These two questions replace all section-specific knowledge. \
A FICO scoring table in Exhibit B passes Q1. \
"Portfolio Manager updates the aging report" fails Q2. \
"Borrower must submit annual tax returns" passes both. \
Apply the same logic to mortgage policies, commercial lending, \
insurance, grants — any document type.

════════════════════════════════════════════════════════
RULE 1 — WHAT TO EXTRACT (The Borrower Test)
════════════════════════════════════════════════════════

Extract a sentence if and only if the borrower must satisfy, \
prove, pay, provide, or agree to something.

✓ EXTRACT — these all pass the borrower test:
  • Eligibility conditions    → "Must be enrolled tribal member"
  • Financial thresholds      → "Minimum loan size $10,000"
  • Document requirements     → "Must submit a business plan"
  • Fee obligations           → "Borrower pays 1% closing fee"
  • Use-of-proceeds limits    → "Proceeds must fund primary residence only"
  • Scoring criteria          → "FICO 700+ earns 5 rating points"
  • Consent requirements      → "Borrower must agree to annual credit checks"
  • Portfolio quotas          → "70% of loans go to tribal members" \
    (a non-tribal borrower may be denied — this IS borrower-checkable)
  • Interest rate parameters  → "Margin between 0.5% and 3%" \
    (determines what rate the borrower pays)
  • Exhibit/appendix tables   → scoring tables, rating criteria, \
    fee schedules in exhibits are borrower-facing — extract every row

✗ SKIP — these all fail the borrower test:
  • "Loan Officer must review the application"
  • "Board approves loans above $300,000"
  • "Portfolio Manager updates the delinquency report"
  • "Committee meets at least monthly"
  • "Executive Director signs the commitment letter"
  • "Staff shall maintain files in a fire-safe cabinet"

════════════════════════════════════════════════════════
RULE 2 — WHAT TO SKIP (The Lender-Action Test)
════════════════════════════════════════════════════════

Skip any sentence whose subject is:
  • A staff role:   Loan Officer, Portfolio Manager, Executive Director,
                    Finance Manager, Homebuyer Consultant, Loan Assistant
  • A committee:    Loan Committee, Board of Directors, LC
  • The lender:     "Loan Fund will...", "YCLF requires...", "CDC shall..."
                    when describing internal lender procedures

⚠ MIXED SECTIONS — some sections contain BOTH governance and borrower rules.
  Apply the lender-action test sentence by sentence. Do not skip an entire
  section just because it is titled "Loan Staff" or "Insider Loans."

  Example — Section titled "Insider Loans":
    SKIP: "Credit applications will be reviewed by the Loan Committee"
    SKIP: "Board must vote within 3 business days"
    EXTRACT: "Insider loans must be made on substantially the same terms \
as those for any other customer" ← borrower-facing obligation
    EXTRACT: "Loans to insiders must be supported by detailed current \
financial statements" ← borrower must submit this

════════════════════════════════════════════════════════
RULE 3 — ATOMICITY (The "Can I Split This?" Test)
════════════════════════════════════════════════════════

Keep splitting until a rule contains exactly ONE testable condition.
Stop only when splitting would destroy meaning.

THREE PATTERNS THAT ARE ALWAYS COMPOUND:

  Pattern A — OR-lists (acceptable alternatives)
  Each option → one rule with requirement_type: "conditional"
  ┌──────────────────────────────────────────────────────────┐
  │ "Ownership must be (a) fee simple, (b) allotted land,   │
  │  or (c) BIA-approved leasehold"                         │
  │  → Rule 1: fee simple ownership accepted  [conditional] │
  │  → Rule 2: allotted land accepted         [conditional] │
  │  → Rule 3: BIA leasehold accepted         [conditional] │
  └──────────────────────────────────────────────────────────┘

  Pattern B — AND-lists (all must be true simultaneously)
  Each condition → one rule with requirement_type: "mandatory"
  ┌──────────────────────────────────────────────────────────┐
  │ "Front-end ratio ≤ 33% and back-end ratio ≤ 45%"       │
  │  → Rule 1: front-end ratio ≤ 33%  [mandatory]          │
  │  → Rule 2: back-end ratio ≤ 45%   [mandatory]          │
  └──────────────────────────────────────────────────────────┘

  Pattern C — Table rows (one row per product/tier)
  Each row → one rule, even if values are identical across rows.
  The PRODUCT NAME is part of the rule identity — never merge.
  ┌──────────────────────────────────────────────────────────┐
  │ Table: Debt Ratios                                       │
  │   Rehab Loan:        29/45                              │
  │   Construction Loan: 29/45                              │
  │   Land Loan:         29/45                              │
  │  → Rule 1: Rehab front-end ≤ 29%        [mandatory]    │
  │  → Rule 2: Rehab back-end ≤ 45%         [mandatory]    │
  │  → Rule 3: Construction front-end ≤ 29% [mandatory]    │
  │  → Rule 4: Construction back-end ≤ 45%  [mandatory]    │
  │  → Rule 5: Land front-end ≤ 29%         [mandatory]    │
  │  → Rule 6: Land back-end ≤ 45%          [mandatory]    │
  └──────────────────────────────────────────────────────────┘

  Pattern D — Compound prose cells
  A single cell or sentence with multiple embedded restrictions
  → split each restriction into its own rule
  ┌──────────────────────────────────────────────────────────┐
  │ "Construction costs for non-luxury improvements that    │
  │  ensure habitability by a TERO-certified contractor"    │
  │  → Rule 1: improvements must be non-luxury  [mandatory] │
  │  → Rule 2: must ensure habitability         [mandatory] │
  │  → Rule 3: TERO-certified contractor req'd  [mandatory] │
  └──────────────────────────────────────────────────────────┘

  Pattern E — Lettered/numbered lists
  Every list item is a separate atomic rule, even with no own heading
  and even if only one sentence long. Do NOT stop after item (a).
  ┌──────────────────────────────────────────────────────────┐
  │ "Target Market:                                         │
  │  a) enrolled members                                    │
  │  b) spouses of enrolled members                        │
  │  c) non-members with pending enrollment                 │
  │  d) permanent residents (minimum one year)"             │
  │  → 4 separate eligibility rules, all conditional        │
  └──────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════
RULE 4 — WHERE TO LOOK (Full Document Scan)
════════════════════════════════════════════════════════

Read the entire document from page 1 to the final line before \
writing a single rule. Apply Q1 and Q2 to every sentence.

High-value locations that models commonly miss:
  • Opening "Purpose" / "Mission" paragraphs — often contain WHO qualifies
  • Plain prose paragraphs — rules hide in sentences, not just tables
  • Sections labelled "Ineligible" or "Prohibited" — each item is a rule
  • Sections with mixed governance + borrower content (Insider Loans, etc.)
  • Exhibits, appendices, scoring tables — contain the most precise rules
  • Footnotes — often contain threshold exceptions or caps

Examples of rules hiding in prose:
  "Application fee $100, nonrefundable"        → borrower must pay $100
  "Closing fee: 1% of loan amount"             → fee obligation
  "Borrower responsible for third-party costs" → expense obligation
  "Term: up to 30 years"                       → term limit
  "Balloon payment at end of 5 years"          → repayment obligation
  "Borrowers must agree to annual credit check"→ consent requirement
  "Maximum five draws in 12-month period"      → draw limit
  "FICO 700+ earns 5 points in loan rating"   → scoring threshold

════════════════════════════════════════════════════════
RULE 5 — FORMATTING RULES
════════════════════════════════════════════════════════

4.1 rule_reference: Copy the exact section heading as it appears.
    GOOD: "III.B — Eligibility Requirements — Collateral"
    BAD:  "Section 3, subsection B"

4.2 source_excerpt: Verbatim from document. No paraphrasing ever.

4.3 description: Focus only on the borrower's obligation.
    Remove all staff role names and committee references.
    BAD:  "The Loan Committee sets margin between 0.5% and 3%"
    GOOD: "The borrower's interest rate margin will be between 0.5% and 3%"

4.4 category: Use ONLY these values:
    eligibility | financial | documentation | property | terms |
    identity | employment | income | credit | insurance | use_of_proceeds
    Do NOT invent new categories. Map "collateral" → financial,
    "guarantee" → financial, "governance" → skip entirely.

4.5 requirement_type:
    "mandatory"    — borrower must always satisfy this
    "conditional"  — one of several acceptable alternatives (OR-list item)
    "informational"— describes a feature with no pass/fail threshold

4.6 check_type:
    presence | value_range | comparison | document_required |
    boolean | pattern | descriptive

════════════════════════════════════════════════════════
RULE 6 — SELF-CHECK (Run before writing each rule)
════════════════════════════════════════════════════════

  a) Does the borrower need to satisfy this?       (No  → skip)
  b) Can it be verified from borrower documents?   (No  → skip)
  c) Is the subject a staff role or lender action? (Yes → skip)
  d) Can this rule be split further?               (Yes → split first)
  e) Does description mention a staff role name?   (Yes → rewrite)
  f) Is source_excerpt verbatim?                   (No  → fix)

════════════════════════════════════════════════════════
OUTPUT FORMAT
════════════════════════════════════════════════════════

You must respond ONLY with valid JSON. No markdown, no extra text.

{{
  "rules": [
    {{
      "rule_id": "snake_case_2_to_4_words_unique",
      "rule_reference": "Exact section heading from document",
      "category": "one of the allowed categories above",
      "title": "Short human-readable title, max 8 words",
      "description": "Complete self-contained borrower-focused statement. \
Include all thresholds, conditions, and values. No staff role names.",
      "requirement_type": "mandatory | conditional | informational",
      "check_type": "presence | value_range | comparison | \
document_required | boolean | pattern | descriptive",
      "source_document": "{document_filename}",
      "source_excerpt": "Verbatim text copied exactly from the source document."
    }}
  ],
  "extraction_summary": {{
    "total_rules": <integer matching rules array length>,
    "categories": ["list", "of", "unique", "category", "values", "used"]
  }}
}}"""


_EXTRACTION_USER_TEMPLATE = """\
POLICY TYPE: {policy_type}

POLICY DOCUMENT:
{policy_text}

Apply the two mental model questions to every sentence in the document above:
  Q1 — Borrower test: "Can this be checked against something the borrower submitted?"
  Q2 — Lender-action test: "Is the subject a staff role or internal lender action?"

Read the entire document before writing any rules.
Extract every atomic borrower-facing rule. Do not stop until the final line.

Respond in the JSON format specified in the system prompt.
Source document filename: {document_filename}"""


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_rules_from_policy(
    policy_text: str,
    policy_type: str,
    document_filename: str = "",
) -> dict:
    """
    Extract atomic compliance rules from a policy document.

    Returns a dict with:
        rules              — list of rule objects
        extraction_summary — { total_rules, categories }

    On AI failure returns a fallback dict with an empty rules list.
    """
    if len(policy_text) > _MAX_POLICY_CHARS:
        logger.warning(
            "Rule extraction: policy_text truncated from %d to %d chars",
            len(policy_text), _MAX_POLICY_CHARS,
        )
        policy_text = policy_text[:_MAX_POLICY_CHARS]

    user_content = _EXTRACTION_USER_TEMPLATE.format(
        policy_type=policy_type or "General Policy",
        policy_text=policy_text,
        document_filename=document_filename or "policy_document",
    )
    messages = [
        {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT.format(document_filename=document_filename or "policy_document")},
        {"role": "user",   "content": user_content},
    ]

    try:
        response_text = _call_ai(messages, max_tokens=16000, temperature=0, seed=42)
        result = _safe_json_parse(response_text)

        rules = result.get("rules", [])
        if not isinstance(rules, list):
            rules = []

        # Normalise each rule — guarantee all expected fields exist
        normalised = []
        seen_ids: set[str] = set()
        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get("rule_id") or f"rule_{idx}").strip()
            # Deduplicate IDs by appending a counter suffix
            if rule_id in seen_ids:
                rule_id = f"{rule_id}_{idx}"
            seen_ids.add(rule_id)

            normalised.append({
                "rule_id":          rule_id,
                "rule_reference":   str(rule.get("rule_reference") or "").strip(),
                "category":         str(rule.get("category") or "general").strip().lower(),
                "title":            str(rule.get("title") or "").strip(),
                "description":      str(rule.get("description") or "").strip(),
                "requirement_type": str(rule.get("requirement_type") or "mandatory").strip().lower(),
                "check_type":       str(rule.get("check_type") or "descriptive").strip().lower(),
                "source_document":  str(rule.get("source_document") or document_filename).strip(),
                "source_excerpt":   str(rule.get("source_excerpt") or "").strip(),
            })

        # Recompute extraction_summary from actual normalised output
        categories = sorted({r["category"] for r in normalised})
        extraction_summary = {
            "total_rules": len(normalised),
            "categories":  categories,
        }

        return {
            "rules":              normalised,
            "extraction_summary": extraction_summary,
        }

    except json.JSONDecodeError:
        logger.exception("Failed to parse AI JSON response for rule extraction")
        return _extraction_fallback("AI response could not be parsed as JSON")
    except PolicyRateLimitError:
        raise
    except Exception as e:
        logger.exception("AI call failed for rule extraction")
        return _extraction_fallback(f"AI extraction failed: {type(e).__name__}: {e}")


def _extraction_fallback(reason: str) -> dict:
    return {
        "rules": [],
        "extraction_summary": {
            "total_rules": 0,
            "categories":  [],
            "error":       reason,
        },
    }

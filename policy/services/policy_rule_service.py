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

_CATEGORY_ALIASES = {"use_of_proceeds": "proceeds"}


def _clean(value) -> str:
    """Strip NUL bytes and Unicode surrogates that PostgreSQL rejects."""
    s = str(value or "")
    s = s.replace("\x00", "")
    # Remove lone Unicode surrogates (U+D800–U+DFFF) which are invalid in UTF-8
    s = s.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    return s.strip()

def _normalise_category(value: str) -> str:
    v = value.strip().lower()
    return _CATEGORY_ALIASES.get(v, v)

# Hard cap to avoid overflowing the model's context window.
# 80,000 chars ≈ 20,000 tokens at ~4 chars/token — safely within most providers.
_MAX_POLICY_CHARS = 80_000


# ── Prompts ────────────────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You are a precise compliance analyst. Your task is to extract every atomic, \
checkable compliance rule from ANY policy document — loan policies, grant \
programs, insurance underwriting guides, lender checklists, or any other \
regulatory document.

════════════════════════════════════════════════════════
STEP 0 — CLASSIFY THE DOCUMENT FIRST (Do this before extracting anything)
════════════════════════════════════════════════════════

Before applying any extraction logic, identify what TYPE of document you are reading.
The type determines how you interpret every sentence.

  ┌─────────────────────────────────────────────────────────────────┐
  │ TYPE A — BORROWER-DIRECT POLICY                                 │
  │ The subject of rules is the BORROWER.                           │
  │ Rules say what the borrower must do/provide/satisfy.            │
  │ Example sentences:                                              │
  │   "Borrower must submit audited financials"                     │
  │   "Minimum credit score: 650"                                   │
  │   "Property must be primary residence"                          │
  │ → Apply Q1/Q2 mental model as-is (below)                       │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │ TYPE B — LENDER/UNDERWRITER CHECKLIST or EVALUATION GUIDE       │
  │ The surface subject is the LENDER or CDFI or PROGRAM.           │
  │ Rules say what the LENDER must evaluate or verify.              │
  │ But the REAL rule is: what the BORROWER must demonstrate.       │
  │ Example sentences:                                              │
  │   "Underwriting must address governance of the borrower"        │
  │   "The Eligible CDFI must evaluate debt service coverage"       │
  │   "Lender must verify that all licenses are in place"           │
  │ → The borrower IS the subject — the lender text is the wrapper  │
  │ → "Lender must evaluate X" = "Borrower must demonstrate X"      │
  │ → Q2 does NOT apply — lender-action text IS the rule trigger    │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │ TYPE C — PROGRAM REQUIREMENTS (Mixed)                           │
  │ Contains BOTH internal lender procedures AND borrower rules.    │
  │ Example: grant program docs, bond program guides                │
  │ → Apply Q1/Q2 sentence-by-sentence                             │
  │ → Skip pure internal procedures                                 │
  │ → Extract anything the borrower must satisfy                    │
  └─────────────────────────────────────────────────────────────────┘

  HOW TO CLASSIFY: Read the first 2 pages. Ask:
  "Are most of the 'must' sentences about what the LENDER does,
   or about what the BORROWER provides?"
  If LENDER → Type B. If BORROWER → Type A. If both → Type C.

  ⚠ CRITICAL FOR TYPE B DOCUMENTS:
  "The Eligible CDFI must address X in underwriting" means:
   → RULE: "The Secondary Borrower/applicant must demonstrate X"
  Extract X as the rule. The lender wrapper is just the framing.
  The compliance question is always: "Does the applicant satisfy X?"

════════════════════════════════════════════════════════
STEP 1 — MENTAL MODEL (Apply after classifying)
════════════════════════════════════════════════════════

For TYPE A and TYPE C documents:

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
  │  action with no borrower obligation behind it?"     │
  │  YES → skip it.   NO → continue to Q1.             │
  └─────────────────────────────────────────────────────┘

For TYPE B documents — replace Q1/Q2 with:

  ┌─────────────────────────────────────────────────────┐
  │ Q3 — THE EVALUATION-WRAPPER TEST                    │
  │ "Does this sentence describe something the lender   │
  │  must EVALUATE, VERIFY, ADDRESS, or ASSESS          │
  │  about the borrower?"                               │
  │  YES → extract it as a BORROWER obligation.         │
  │  NO (pure internal admin) → skip it.                │
  └─────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────┐
  │ Q4 — SCOPE SPECIFICITY TEST (Type B only)           │
  │ "Does this sentence name a SPECIFIC thing to        │
  │  evaluate (governance, DSCR, licenses, key          │
  │  personnel) OR is it just restating the section     │
  │  header in prose?"                                  │
  │  SPECIFIC → extract it as its own atomic rule.      │
  │  RESTATEMENT → skip (the bullet items below it      │
  │                will be the actual rules).           │
  └─────────────────────────────────────────────────────┘

  TYPE B examples to make Q3/Q4 concrete:
  ┌──────────────────────────────────────────────────────────┐
  │ "Underwriting must address governance"                   │
  │   Q3: YES — evaluating borrower's governance            │
  │   Q4: SPECIFIC — extract as "Borrower must demonstrate  │
  │        adequate governance structure"            ✓      │
  │                                                          │
  │ "Underwriting must address all required licenses"        │
  │   Q3: YES   Q4: SPECIFIC — extract               ✓      │
  │                                                          │
  │ "The Eligible CDFI must maintain loan policies"          │
  │   Q3: NO — this is about the LENDER's own policies,     │
  │        not evaluating the borrower            SKIP ✗    │
  │                                                          │
  │ "The Eligible CDFI's underwriting criteria must address  │
  │  the following:"                                         │
  │   Q4: RESTATEMENT of section heading — the bullets      │
  │        below contain the actual rules        SKIP ✗     │
  └──────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════
STEP 2 — SECTION TRAVERSAL (Never stop early)
════════════════════════════════════════════════════════

This is the most common failure mode — stopping after Part I.

RULE: Every numbered section, lettered sub-section, and bullet item
      in the document is a POTENTIAL RULE SOURCE.

For documents with multiple asset classes / loan types / sections:
  → Treat each section as a SEPARATE extraction pass
  → Do NOT assume a section is "the same" as a previous one
  → Even if §3 and §4 look similar, extract each independently
  → Rules with the same content but different asset classes are
     DIFFERENT rules (the asset class is part of rule identity)

Traversal checklist — before you finish, verify you have read:
  □ All top-level numbered sections (1, 2, 3…)
  □ All sub-sections (A, B, C… or i, ii, iii…)
  □ All bullet lists within each section
  □ All sub-bullets (indented items under bullets)
  □ All exhibits and appendices
  □ All footnotes
  □ Any tables

If the document has N distinct asset classes / loan programs,
your output should contain ROUGHLY N × (rules per class) rules.
If your count is far below that, you stopped early.

════════════════════════════════════════════════════════
STEP 3 — WHAT TO EXTRACT
════════════════════════════════════════════════════════

✓ ALWAYS EXTRACT:
  • Eligibility conditions    → "Must be enrolled tribal member"
  • Financial thresholds      → "Minimum loan size $10,000"
  • Document requirements     → "Must submit a business plan"
  • Fee obligations           → "Borrower pays 1% closing fee"
  • Use-of-proceeds limits    → "Proceeds must fund primary residence only"
  • Scoring criteria          → "FICO 700+ earns 5 rating points"
  • Consent / agreement reqs  → "Borrower must agree to annual credit checks"
  • Interest rate parameters  → "Margin between 0.5% and 3%"
  • Exhibit/appendix tables   → every scoring row, fee row, threshold row
  • Evaluation criteria       → (Type B) anything lender is required to assess
  • Compliance requirements   → licenses, certifications, standings
  • Quantitative thresholds   → DSCR, LTV, DTI, ratios, percentages
  • Specific named regulations → "Must comply with 42 C.F.R. 405.2401"

✗ ALWAYS SKIP:
  • Pure internal lender admin: "Loan Officer files the report"
  • Committee-only actions with no borrower obligation: "Board meets monthly"
  • Informational lists with no checkable condition:
    "The following asset classes are eligible: 1. Charter schools 2. CRE…"
    (These are context/scope, not checkable rules — extract the rules
     WITHIN each asset class section instead)

════════════════════════════════════════════════════════
STEP 4 — ATOMICITY
════════════════════════════════════════════════════════

Keep splitting until a rule contains exactly ONE testable condition.

Pattern A — OR-lists → each option = one rule [requirement_type: conditional]
Pattern B — AND-lists → each condition = one rule [requirement_type: mandatory]
Pattern C — Table rows → each row = one rule (product name is part of identity)
Pattern D — Compound prose → split each restriction into its own rule
Pattern E — Lettered/numbered lists → every item = one rule

  ┌──────────────────────────────────────────────────────────┐
  │ "Front-end ratio ≤ 33% and back-end ratio ≤ 45%"       │
  │  → Rule 1: front-end ratio ≤ 33%  [mandatory]          │
  │  → Rule 2: back-end ratio ≤ 45%   [mandatory]          │
  └──────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────┐
  │ Type B: "Underwriting must address governance,          │
  │  key personnel, and financial performance"              │
  │  → Rule 1: Borrower must demonstrate adequate governance│
  │  → Rule 2: Key personnel qualifications must be shown   │
  │  → Rule 3: Financial performance must be demonstrated   │
  └──────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════
STEP 5 — DESCRIPTION REWRITING (Type B only)
════════════════════════════════════════════════════════

For Type B documents, the source text says "lender must evaluate X"
but the rule description must say "borrower must demonstrate X".

REWRITING RULES:
  "Underwriting must address X"       → "The borrower must demonstrate X"
  "The CDFI must evaluate X"          → "The borrower must provide evidence of X"
  "Lender must verify that X"         → "X must be demonstrated by the borrower"
  "Must assess the capacity to Y"     → "The borrower must demonstrate capacity to Y"
  "Must review current licenses"      → "All required licenses must be current and on file"

  BAD:  "Underwriting must address governance and key personnel"
  GOOD: "The Secondary Borrower must demonstrate adequate governance structure
         and qualified key personnel in underwriting submissions"

  The source_excerpt stays VERBATIM from the document.
  The description is the REWRITTEN borrower-obligation version.

════════════════════════════════════════════════════════
STEP 6 — FORMATTING RULES
════════════════════════════════════════════════════════

6.1 rule_reference: Copy the exact section heading as it appears.
    GOOD: "III.B — Eligibility Requirements — Collateral"
    GOOD: "2. CDFI-TO-FINANCING ENTITY LENDING"
    BAD:  "Section 3, subsection B"

6.2 source_excerpt: Verbatim from document. No paraphrasing ever.
    Include the null byte \\u0000 if present. Copy exactly.

6.3 description: Borrower-obligation framing always.
    Remove all lender role names. Rewrite if Type B (see Step 5).

6.4 category: Use ONLY these values:
    eligibility | financial | documentation | property | terms |
    identity | employment | income | credit | insurance | proceeds |
    governance | compliance | underwriting | management | risk_management |
    collateral | reporting | performance | pricing
    Map consistently: "collateral" = collateral, "license check" = compliance,
    "governance review" = governance, "financial statements" = financial.

6.5 requirement_type:
    "mandatory"     — always required
    "conditional"   — one of several acceptable alternatives (OR-list)
    "informational" — feature description with no pass/fail

6.6 check_type:
    existence       — something must exist or be present
    descriptive     — qualitative narrative/assessment required
    quantitative    — numeric threshold or ratio
    document_required — specific document must be submitted
    boolean         — yes/no binary check
    value_range     — must fall within a range

════════════════════════════════════════════════════════
STEP 7 — SELF-CHECK (Run before writing each rule)
════════════════════════════════════════════════════════

  a) Have I classified the document type?           (No  → do Step 0 first)
  b) Am I in a section I have not yet processed?    (Yes → process it now)
  c) Does the borrower need to satisfy this?        (No  → skip)
  d) Is this pure internal admin with no borrower
     obligation behind it?                          (Yes → skip)
  e) Can this rule be split further?                (Yes → split first)
  f) Does description mention a staff role name?    (Yes → rewrite)
  g) Is source_excerpt verbatim?                    (No  → fix)
  h) Is my total count consistent with the number
     of sections × items per section?               (No  → check for early stop)

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
      "description": "Complete self-contained borrower-obligation statement. \
Include all thresholds, conditions, and values. No staff role names. \
Rewritten from lender-evaluation framing if Type B document.",
      "requirement_type": "mandatory | conditional | informational",
      "check_type": "existence | descriptive | quantitative | \
document_required | boolean | value_range",
      "source_document": "{document_filename}",
      "source_excerpt": "Verbatim text copied exactly from the source document."
    }}
  ],
  "extraction_summary": {{
    "document_type": "A | B | C",
    "document_type_reasoning": "One sentence explaining why this classification",
    "total_rules": <integer matching rules array length>,
    "sections_processed": ["list", "of", "all", "sections", "read"],
    "categories": ["list", "of", "unique", "category", "values", "used"]
  }}
}}"""


_EXTRACTION_USER_TEMPLATE = """\
POLICY TYPE: {policy_type}

POLICY DOCUMENT:
{policy_text}

─── EXTRACTION INSTRUCTIONS ───────────────────────────────────────────────────

STEP 0 FIRST: Classify this document as Type A, B, or C (see system prompt).
Write your classification in extraction_summary.document_type before any rules.

Then apply the appropriate mental model:
  Type A/C → Q1 (borrower test) + Q2 (lender-action test)
  Type B   → Q3 (evaluation-wrapper test) + Q4 (scope specificity test)

SECTION TRAVERSAL:
  Read the ENTIRE document — every section, every bullet, every sub-bullet.
  Do NOT stop after the first section or first part.
  If the document has multiple asset classes or loan programs,
  extract rules from EACH of them separately.
  Count your sections processed and report in extraction_summary.

QUALITY BAR:
  For a document with 10+ sections each containing 10+ bullet items,
  expect 80–150+ rules. If your count is below 30, you have stopped early.

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
            rule_id = _clean(rule.get("rule_id") or f"rule_{idx}")
            # Deduplicate IDs by appending a counter suffix
            if rule_id in seen_ids:
                rule_id = f"{rule_id}_{idx}"
            seen_ids.add(rule_id)

            normalised.append({
                "rule_id":          rule_id,
                "rule_reference":   _clean(rule.get("rule_reference")),
                "category":         _normalise_category(_clean(rule.get("category")) or "general"),
                "title":            _clean(rule.get("title")),
                "description":      _clean(rule.get("description")),
                "requirement_type": _clean(rule.get("requirement_type") or "mandatory").lower(),
                "check_type":       _clean(rule.get("check_type") or "descriptive").lower(),
                "source_document":  _clean(rule.get("source_document") or document_filename),
                "source_excerpt":   _clean(rule.get("source_excerpt")),
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

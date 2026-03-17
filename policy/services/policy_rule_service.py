"""
Policy rule extraction service.

Extracts atomic, checkable compliance rules from a policy/rule document.
Each rule is a structured object that can be stored by the caller and reused
across multiple loan-document analyses — avoiding repeated rule extraction
and eliminating variance in rule counts between runs.
"""
import json
import logging

from policy.services.policy_ai_service import call_ai as _call_ai, safe_json_parse as _safe_json_parse

logger = logging.getLogger(__name__)

# Hard cap to avoid overflowing the model's context window.
# 80,000 chars ≈ 20,000 tokens at ~4 chars/token — safely within most providers.
_MAX_POLICY_CHARS = 80_000


# ── Prompts ────────────────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You are a precise compliance analyst. Your task is to extract every atomic, \
checkable compliance rule from a policy document.

Rules for extraction:
1. Only extract CHECKABLE requirements — things that can be verified from an \
applicant's or borrower's submitted document (loan application, identity docs, \
financial statements, property details, etc.).
2. SKIP internal governance rules, staff procedures, post-execution operational \
rules, board approvals, reporting obligations, or organizational structure \
requirements that would never appear in an applicant's submission.
3. Each rule must be ATOMIC — one specific, testable condition per rule. \
Split compound requirements into separate rules.
4. Preserve exact section headings and numbering for rule_reference.
5. Copy source_excerpt verbatim from the policy text — do not paraphrase.

You must respond ONLY with valid JSON. No markdown, no extra text."""

_EXTRACTION_USER_TEMPLATE = """\
POLICY TYPE: {policy_type}

POLICY DOCUMENT:
{policy_text}

Extract every atomic, checkable compliance rule from the policy document above.

Respond in this EXACT JSON format:
{{
    "rules": [
        {{
            "rule_id": "short_snake_case_slug_unique_within_this_extraction",
            "rule_reference": "Exact section heading or numbering as it appears in the document \
(e.g. 'III.B.1 — Maximum Loan Amount', 'Section 4: Eligibility'). \
If no heading exists, use the closest descriptive phrase from surrounding text.",
            "category": "One of: eligibility, financial, documentation, property, terms, \
identity, employment, income, credit, insurance — or a short descriptive label if none fit.",
            "title": "Short human-readable title, max 8 words",
            "description": "Complete, self-contained statement of this single requirement. \
Include all thresholds, conditions, and values so it can be checked standalone.",
            "requirement_type": "mandatory" or "conditional" or "informational",
            "check_type": "One of: presence, value_range, comparison, document_required, \
boolean, pattern, or descriptive",
            "source_document": "{document_filename}",
            "source_excerpt": "Verbatim sentence(s) from the policy document that state this \
rule — copy exactly from the source, do not paraphrase."
        }}
    ],
    "extraction_summary": {{
        "total_rules": <integer matching the length of the rules array>,
        "categories": ["list", "of", "unique", "category", "values", "used"]
    }}
}}

rule_id conventions:
- snake_case, 2–4 words maximum
- Must be unique within this extraction
- Should reflect the rule topic (e.g. "max_loan_amount", "tribal_membership_required")
"""


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
        {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
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

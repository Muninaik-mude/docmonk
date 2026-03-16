"""
Policy analysis AI service.

Uses a dedicated single-provider OpenAI-compatible client configured via
POLICY_AI_API_KEY / POLICY_AI_MODEL / POLICY_AI_BASE_URL environment variables.
Fully decoupled from the clause-analyzer provider pool.
"""
import json
import logging

from policy.services.policy_ai_service import call_ai as _call_ai, safe_json_parse as _safe_json_parse

logger = logging.getLogger(__name__)


# ── Policy-based document analysis ────────────────────────────────────────────

_POLICY_SYSTEM_PROMPT = """You are a strict compliance analyst. You receive a POLICY DOCUMENT and a SUBJECT DOCUMENT.
Your job is to:
1. First, identify what TYPE of subject document this is (e.g. loan application, rental agreement, employment contract, insurance claim, etc.).
2. Extract all compliance requirements from the policy document.
3. For each requirement, determine if it is CHECKABLE against this type of document:
   - CHECKABLE: the requirement can be verified from the content a submitter/applicant provides.
   - NOT APPLICABLE TO THIS DOCUMENT TYPE: the requirement belongs to internal processes,
     staff procedures, governance rules, post-execution operations, or organizational structure
     that would never appear in this type of document.
4. Only evaluate CHECKABLE requirements. Completely skip NOT APPLICABLE ones — do not include
   them in the output at all.
5. Provide an overall recommendation based only on CHECKABLE requirements.

You must respond ONLY with valid JSON, no extra text or markdown formatting."""

_POLICY_USER_TEMPLATE = """POLICY TYPE: {policy_type}

POLICY DOCUMENT (the standards this document must meet):
{policy_text}

SUBJECT DOCUMENT TO ANALYZE:
{document_text}

Analyze the SUBJECT DOCUMENT against every requirement in the POLICY DOCUMENT.

Respond in this EXACT JSON format:
{{
    "overall_verdict": "COMPLIANT" or "NON_COMPLIANT" or "PARTIALLY_COMPLIANT",
    "compliance_score": <integer 0-100 reflecting percentage of requirements satisfied>,
    "summary": "2-3 sentence executive summary of the analysis outcome. Wrap BOTH key terms AND their actual values together in {{{{double curly braces}}}} with a sentiment prefix so the UI can color-code them. Prefix rules: use {{{{-term}}}} for negatives (violations, breaches, exceedances, failures, risks), use {{{{+term}}}} for positives (satisfied requirements, compliant values, approvals), use {{{{~term}}}} for neutral labels (verdict labels, policy names, document types). Examples: {{{{-loan amount of $162,000 exceeds cap}}}}, {{{{-LTV ratio of 102.5%}}}}, {{{{+down payment of 20% meets requirement}}}}, {{{{~NON_COMPLIANT}}}}, {{{{~Housing Lending Policy}}}}. Rule: every dollar amount, percentage, date, and numeric threshold relevant to compliance MUST be wrapped — never leave a number or percentage bare.",
    "policy_requirements": [
        {{
            "requirement": "The specific policy requirement being checked",
            "status": "SATISFIES" or "VIOLATES" or "RISKY" or "NOT_ADDRESSED",
            "reason": "Specific finding citing exact text from both documents where possible",
            "relevant_text": "Verbatim sentence(s) from the subject document relevant to this requirement, or null if nothing found",
            "rule_reference": "Exact section or rule name from the POLICY DOCUMENT this requirement originates from (e.g. 'Section 3.2 — LTV Requirements', 'Rule 5: Maximum Loan Amount', 'Article IV — Eligibility Criteria'). Use the heading or numbering as it appears in the policy text. Never fabricate — if the policy has no section heading, use the closest descriptive phrase from the policy.",
            "recommendation": "Concrete, actionable fix — e.g. 'Reduce loan amount to $100,000 or below to meet the policy cap' — null if status is SATISFIES"
        }}
    ],
    "risk_points": ["Specific risk with brief explanation — only for RISKY or NOT_ADDRESSED items"],
    "approval_recommendation": "APPROVE" or "CONDITIONAL_APPROVE" or "REJECT",
    "conditions": ["Exact corrective action required — one entry per VIOLATES item — empty list if no violations"]
}}

Status classification rules:
- SATISFIES: The subject document fully meets this policy requirement — all values, thresholds, and conditions are present and compliant
- VIOLATES: The subject document directly contradicts or breaches this policy requirement (e.g. exceeds allowed LTV ratio, missing mandatory clause, prohibited term present)
- RISKY: The subject document partially addresses the requirement but has gaps, vague language, or borderline values that create exposure
- NOT_ADDRESSED: The policy requirement is entirely absent from the subject document

Compliance score: count of SATISFIES ÷ total requirements × 100 (rounded to nearest integer)
Overall verdict:
- COMPLIANT: compliance_score >= 70 and no VIOLATES
- NON_COMPLIANT: compliance_score < 60
- PARTIALLY_COMPLIANT: otherwise (60–69, or score >= 70 with VIOLATES present)

Approval recommendation — THREE possible values:
- APPROVE: compliance_score >= 70 AND no VIOLATES (RISKY items are warnings only, do not block approval)
- CONDITIONAL_APPROVE: compliance_score >= 60 but below 70, OR score >= 70 with VIOLATES present — populate "conditions" with one concrete corrective action per violation
- REJECT: compliance_score < 60 — document has too many unmet or violated requirements to be conditionally approved
"""


def analyze_document_against_policy(
    document_text: str,
    policy_type: str,
    policy_text: str,
) -> dict:
    """
    Analyze a subject document against a policy document.

    Returns dict with: overall_verdict, compliance_score, summary,
    policy_requirements, risk_points, approval_recommendation, conditions.
    """
    user_content = _POLICY_USER_TEMPLATE.format(
        policy_type=policy_type or "General Policy",
        policy_text=policy_text,
        document_text=document_text,
    )
    messages = [
        {"role": "system", "content": _POLICY_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    try:
        response_text = _call_ai(messages, max_tokens=8000, temperature=0, seed=42)
        result = _safe_json_parse(response_text)

        # Normalize / validate fields
        if not isinstance(result.get("policy_requirements"), list):
            result["policy_requirements"] = []
        if not isinstance(result.get("risk_points"), list):
            result["risk_points"] = []
        if not isinstance(result.get("conditions"), list):
            result["conditions"] = []

        reqs = result["policy_requirements"]

        # Recalculate compliance score from requirements (source of truth)
        sat = sum(1 for r in reqs if r.get("status") == "SATISFIES")
        score = round(sat / len(reqs) * 100) if reqs else 0
        result["compliance_score"] = score

        # Derive overall_verdict from score + violations
        has_violations = any(r.get("status") == "VIOLATES" for r in reqs)
        if score >= 70 and not has_violations:
            result["overall_verdict"] = "COMPLIANT"
        elif score < 60:
            result["overall_verdict"] = "NON_COMPLIANT"
        else:
            result["overall_verdict"] = "PARTIALLY_COMPLIANT"

        # Derive approval_recommendation from score + violations
        if score >= 70 and not has_violations:
            result["approval_recommendation"] = "APPROVE"
            result["conditions"] = []
        elif score < 60:
            result["approval_recommendation"] = "REJECT"
            result["conditions"] = []
        else:
            # score 60–69, OR score >= 70 with violations
            result["approval_recommendation"] = "CONDITIONAL_APPROVE"
            # Ensure conditions lists one fix per violation if AI didn't populate them
            if not result["conditions"]:
                result["conditions"] = [
                    r["recommendation"]
                    for r in reqs
                    if r.get("status") == "VIOLATES" and r.get("recommendation")
                ]

        return result

    except json.JSONDecodeError:
        logger.exception("Failed to parse AI JSON response for policy analysis")
        return _policy_fallback_result("AI response could not be parsed")
    except Exception as e:
        logger.exception("AI call failed for policy analysis")
        return _policy_fallback_result(f"AI analysis failed: {type(e).__name__}")


def _policy_fallback_result(reason: str) -> dict:
    return {
        "overall_verdict":        "PARTIALLY_COMPLIANT",
        "compliance_score":       0,
        "summary":                f"Policy analysis could not be completed: {reason}. Manual review required.",
        "policy_requirements":    [],
        "risk_points":            [reason],
        "approval_recommendation": "CONDITIONAL_APPROVE",
        "conditions":             ["Manual review required due to analysis failure"],
    }

"""
Policy analysis AI service.

Uses the shared multi-provider pool from analyzer.services.groq_service.
The pool (groq_1, groq_2, cerebras_1, cerebras_2) is initialized once and
shared across both the clause analyzer and policy analyzer.
"""
import json
import logging

from analyzer.services.groq_service import _call_ai, _safe_json_parse

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
    "summary": "2-3 sentence executive summary of the analysis outcome",
    "policy_requirements": [
        {{
            "requirement": "The specific policy requirement being checked",
            "status": "SATISFIES" or "VIOLATES" or "RISKY" or "NOT_ADDRESSED",
            "reason": "Specific finding citing exact text from both documents where possible",
            "relevant_text": "Verbatim sentence(s) from the subject document relevant to this requirement, or null if nothing found",
            "recommendation": "What must be changed or added to satisfy this requirement — null if status is SATISFIES"
        }}
    ],
    "risk_points": ["Specific risk 1", "Specific risk 2"],
    "approval_recommendation": "APPROVE" or "REJECT" or "CONDITIONAL_APPROVE",
    "conditions": ["Condition that must be met before approval — only if CONDITIONAL_APPROVE, else empty list"]
}}

Status classification rules:
- SATISFIES: The subject document fully meets this policy requirement — all values, thresholds, and conditions are present and compliant
- VIOLATES: The subject document directly contradicts or breaches this policy requirement (e.g. exceeds allowed LTV ratio, missing mandatory clause, prohibited term present)
- RISKY: The subject document partially addresses the requirement but has gaps, vague language, or borderline values that create exposure
- NOT_ADDRESSED: The policy requirement is entirely absent from the subject document

Compliance score: count of SATISFIES ÷ total requirements × 100 (rounded to nearest integer)
Overall verdict:
- COMPLIANT: compliance_score >= 80 and no VIOLATES
- NON_COMPLIANT: any VIOLATES present, or compliance_score < 50
- PARTIALLY_COMPLIANT: otherwise (50–79 score, no direct violations)

Approval recommendation:
- APPROVE: COMPLIANT verdict
- REJECT: NON_COMPLIANT verdict
- CONDITIONAL_APPROVE: PARTIALLY_COMPLIANT verdict — list conditions for approval
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
        response_text = _call_ai(messages, max_tokens=8000, temperature=0.1)
        result = _safe_json_parse(response_text)

        # Normalize / validate fields
        valid_verdicts = ("COMPLIANT", "NON_COMPLIANT", "PARTIALLY_COMPLIANT")
        if result.get("overall_verdict") not in valid_verdicts:
            result["overall_verdict"] = "PARTIALLY_COMPLIANT"

        valid_recs = ("APPROVE", "REJECT", "CONDITIONAL_APPROVE")
        if result.get("approval_recommendation") not in valid_recs:
            result["approval_recommendation"] = "CONDITIONAL_APPROVE"

        if not isinstance(result.get("policy_requirements"), list):
            result["policy_requirements"] = []
        if not isinstance(result.get("risk_points"), list):
            result["risk_points"] = []
        if not isinstance(result.get("conditions"), list):
            result["conditions"] = []

        score = result.get("compliance_score")
        if not isinstance(score, int) or not (0 <= score <= 100):
            reqs = result["policy_requirements"]
            sat  = sum(1 for r in reqs if r.get("status") == "SATISFIES")
            result["compliance_score"] = round(sat / len(reqs) * 100) if reqs else 0

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

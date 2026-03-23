"""
Policy analysis AI service.

Uses a dedicated single-provider OpenAI-compatible client configured via
POLICY_AI_API_KEY / POLICY_AI_MODEL / POLICY_AI_BASE_URL environment variables.
Fully decoupled from the clause-analyzer provider pool.
"""
import json
import logging

from policy.services.policy_ai_service import call_ai as _call_ai, safe_json_parse as _safe_json_parse, PolicyRateLimitError

logger = logging.getLogger(__name__)


def _sanitize(obj):
    """Recursively strip NUL bytes and lone Unicode surrogates from all strings.
    PostgreSQL rejects both in text and jsonb columns."""
    if isinstance(obj, str):
        s = obj.replace("\x00", "")
        return s.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    return obj


# Hard cap to avoid overflowing the model's context window.
# 80,000 chars ≈ 20,000 tokens at ~4 chars/token.
_MAX_DOC_CHARS    = 80_000
_MAX_POLICY_CHARS = 80_000


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
    "summary": "2-3 sentence executive summary of the analysis outcome. Wrap BOTH key terms AND their actual values together in {{{{double curly braces}}}} with a sentiment prefix so the UI can color-code them. Prefix rules: use {{{{-term}}}} for negatives (violations, breaches, exceedances, failures, risks), use {{{{+term}}}} for positives (satisfied requirements, compliant values, approvals), use {{{{~term}}}} for neutral labels (verdict labels, policy names, document types). Examples: {{{{-loan amount of $162,000 exceeds cap}}}}, {{{{-LTV ratio of 102.5%}}}}, {{{{+down payment of 20% meets requirement}}}}, {{{{~Requires Review}}}}, {{{{~Housing Lending Policy}}}}. Rule: every dollar amount, percentage, date, and numeric threshold relevant to compliance MUST be wrapped — never leave a number or percentage bare. CRITICAL LANGUAGE RULE: NEVER write 'non-compliant', 'Non-Compliant', or 'NON_COMPLIANT' anywhere in the summary text. Instead of 'The application is non-compliant', write 'The application requires review'. Use 'Requires Review' as a noun label wrapped in {{{{~Requires Review}}}}, or 'requires review' as a verb phrase — never as a predicate adjective after 'is'.",
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
    if len(document_text) > _MAX_DOC_CHARS:
        logger.warning(
            "Policy analysis: document_text truncated from %d to %d chars",
            len(document_text), _MAX_DOC_CHARS,
        )
        document_text = document_text[:_MAX_DOC_CHARS]
    if len(policy_text) > _MAX_POLICY_CHARS:
        logger.warning(
            "Policy analysis: policy_text truncated from %d to %d chars",
            len(policy_text), _MAX_POLICY_CHARS,
        )
        policy_text = policy_text[:_MAX_POLICY_CHARS]

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
        response_text = _call_ai(messages, temperature=0, seed=42)
        result = _sanitize(_safe_json_parse(response_text))

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

        # Safety-net: executive summary must never say "Non-Compliant" — use "Requires Review"
        if isinstance(result.get("summary"), str):
            result["summary"] = (
                result["summary"]
                # Fix grammatical form first: "is non-compliant" → "requires review"
                .replace("is NON_COMPLIANT", "requires review")
                .replace("is Non-Compliant", "requires review")
                .replace("is non-compliant", "requires review")
                # Then replace any remaining standalone occurrences
                .replace("NON_COMPLIANT", "Requires Review")
                .replace("Non-Compliant", "Requires Review")
                .replace("non-compliant", "Requires Review")
            )

        return result

    except json.JSONDecodeError:
        logger.exception("Failed to parse AI JSON response for policy analysis")
        return _policy_fallback_result("AI response could not be parsed")
    except PolicyRateLimitError:
        raise
    except Exception:
        logger.exception("AI call failed for policy analysis")
        raise


# ── Rules-based document analysis ─────────────────────────────────────────────

_RULES_ANALYSIS_SYSTEM_PROMPT = """You are a strict compliance analyst. You receive a list of pre-extracted POLICY RULES and a SUBJECT DOCUMENT.

Your job is to:
1. First, identify what TYPE of subject document this is (e.g. loan application, rental agreement, employment contract, etc.) and what specific loan product or agreement type it covers (e.g. Home Purchase Loan, Rehab Loan, Construction Loan, Land Purchase Loan, Homeowner Improvement Loan, etc.).
2. For each provided rule, determine if it is CHECKABLE against this specific document type and loan product:
   - CHECKABLE: the rule can be verified from the content the applicant/submitter provides in this document for this loan product type.
   - NOT APPLICABLE: the rule explicitly targets a different loan product type than what this document covers, OR the rule belongs to internal processes, staff procedures, committee approvals, governance, post-execution operations, or organizational structure that would never appear in an applicant's submission.
3. Only evaluate CHECKABLE rules. Completely skip NOT APPLICABLE rules — do not include them in the output at all.
4. Provide an overall recommendation based only on CHECKABLE rules.

You must respond ONLY with valid JSON, no extra text or markdown formatting."""

_RULES_ANALYSIS_USER_TEMPLATE = """POLICY TYPE: {policy_type}

POLICY RULES TO CHECK ({rule_count} rules):
{rules_text}

SUBJECT DOCUMENT TO ANALYZE:
{document_text}

Analyze the SUBJECT DOCUMENT against the provided POLICY RULES.

Respond in this EXACT JSON format:
{{
    "overall_verdict": "COMPLIANT" or "NON_COMPLIANT" or "PARTIALLY_COMPLIANT",
    "compliance_score": <integer 0-100 reflecting percentage of CHECKABLE requirements satisfied>,
    "summary": "2-3 sentence executive summary of the analysis outcome. Wrap BOTH key terms AND their actual values together in {{{{double curly braces}}}} with a sentiment prefix so the UI can color-code them. Prefix rules: use {{{{-term}}}} for negatives (violations, breaches, exceedances, failures, risks), use {{{{+term}}}} for positives (satisfied requirements, compliant values, approvals), use {{{{~term}}}} for neutral labels (verdict labels, policy names, document types). Examples: {{{{-loan amount of $162,000 exceeds cap}}}}, {{{{-LTV ratio of 102.5%}}}}, {{{{+down payment of 20% meets requirement}}}}, {{{{~Requires Review}}}}, {{{{~Housing Lending Policy}}}}. Rule: every dollar amount, percentage, date, and numeric threshold relevant to compliance MUST be wrapped — never leave a number or percentage bare. CRITICAL LANGUAGE RULE: NEVER write 'non-compliant', 'Non-Compliant', or 'NON_COMPLIANT' anywhere in the summary text. Instead of 'The application is non-compliant', write 'The application requires review'. Use 'Requires Review' as a noun label wrapped in {{{{~Requires Review}}}}, or 'requires review' as a verb phrase — never as a predicate adjective after 'is'.",
    "policy_requirements": [
        {{
            "rule_id": "same rule_id from the input rule",
            "requirement": "The rule description — copy verbatim from the input rule description",
            "status": "SATISFIES" or "VIOLATES" or "RISKY" or "NOT_ADDRESSED",
            "reason": "Specific finding citing exact text from both the rule and subject document where possible",
            "relevant_text": "Verbatim sentence(s) from the subject document relevant to this requirement, or null if nothing found",
            "rule_reference": "same rule_reference from the input rule",
            "source_document": "same source_document from the input rule",
            "recommendation": "Concrete, actionable fix — e.g. 'Reduce loan amount to $100,000 or below to meet the policy cap' — null if status is SATISFIES"
        }}
    ],
    "risk_points": ["Specific risk with brief explanation — only for RISKY or NOT_ADDRESSED items"],
    "approval_recommendation": "APPROVE" or "CONDITIONAL_APPROVE" or "REJECT",
    "conditions": ["Exact corrective action required — one entry per VIOLATES item — empty list if no violations"]
}}

Status classification rules (only for CHECKABLE rules):
- SATISFIES: The subject document fully meets this rule — all values, thresholds, and conditions are present and compliant
- VIOLATES: The subject document directly contradicts or breaches this rule (e.g. exceeds allowed LTV ratio, missing mandatory clause, prohibited term present)
- RISKY: The subject document partially addresses the requirement but has gaps, vague language, or borderline values that create exposure
- NOT_ADDRESSED: The rule requirement is entirely absent from the subject document but should be present

Compliance score: count of SATISFIES ÷ total CHECKABLE rules × 100 (rounded to nearest integer)
Overall verdict:
- COMPLIANT: compliance_score >= 70 and no VIOLATES
- NON_COMPLIANT: compliance_score < 60
- PARTIALLY_COMPLIANT: otherwise (60–69, or score >= 70 with VIOLATES present)

Approval recommendation — THREE possible values:
- APPROVE: compliance_score >= 70 AND no VIOLATES (RISKY items are warnings only, do not block approval)
- CONDITIONAL_APPROVE: compliance_score >= 60 but below 70, OR score >= 70 with VIOLATES present — populate "conditions" with one concrete corrective action per violation
- REJECT: compliance_score < 60 — document has too many unmet or violated requirements to be conditionally approved
"""


def _format_rules_for_prompt(rules: list) -> str:
    """Serialise rule objects into a compact, readable block for the analysis prompt."""
    lines = []
    for i, rule in enumerate(rules, 1):
        lines.append(
            f"[{i}] rule_id: {rule.get('rule_id', '')}\n"
            f"    Reference : {rule.get('rule_reference', '')}\n"
            f"    Category  : {rule.get('category', '')}\n"
            f"    Type      : {rule.get('requirement_type', 'mandatory')}\n"
            f"    Source    : {rule.get('source_document', '')}\n"
            f"    Rule      : {rule.get('description', '')}"
        )
    return "\n\n".join(lines)


def analyze_document_against_rules(
    document_text: str,
    policy_type: str,
    rules: list,
) -> dict:
    """
    Analyze a subject document against a list of pre-extracted rule objects.

    Accepts the structured rules returned by policy_rule_service.extract_rules_from_policy().
    Returns the same shape as analyze_document_against_policy() — the caller can use
    either function interchangeably.
    """
    if not rules:
        return _policy_fallback_result("No rules provided for analysis")

    if len(document_text) > _MAX_DOC_CHARS:
        logger.warning(
            "Rules-based analysis: document_text truncated from %d to %d chars",
            len(document_text), _MAX_DOC_CHARS,
        )
        document_text = document_text[:_MAX_DOC_CHARS]

    rules_text = _format_rules_for_prompt(rules)
    user_content = _RULES_ANALYSIS_USER_TEMPLATE.format(
        policy_type=policy_type or "General Policy",
        rule_count=len(rules),
        rules_text=rules_text,
        document_text=document_text,
    )
    messages = [
        {"role": "system", "content": _RULES_ANALYSIS_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    try:
        response_text = _call_ai(messages, temperature=0, seed=42)
        result = _sanitize(_safe_json_parse(response_text))

        # Normalise fields
        if not isinstance(result.get("policy_requirements"), list):
            result["policy_requirements"] = []
        if not isinstance(result.get("risk_points"), list):
            result["risk_points"] = []
        if not isinstance(result.get("conditions"), list):
            result["conditions"] = []

        _VALID_STATUSES = {"SATISFIES", "VIOLATES", "RISKY", "NOT_ADDRESSED"}

        # Drop any result with a non-standard status (e.g. NOT_APPLICABLE, N/A).
        # The prompt instructs the AI to omit these entirely, but some models
        # stubbornly include them with a custom status — filter them out here.
        result["policy_requirements"] = [
            r for r in result["policy_requirements"]
            if r.get("status") in _VALID_STATUSES
        ]

        reqs = result["policy_requirements"]

        # Back-fill source_document from the input rules if AI omitted it
        rule_map = {r.get("rule_id"): r for r in rules}
        for req in reqs:
            rid = req.get("rule_id") or ""
            if rid in rule_map and not req.get("source_document"):
                req["source_document"] = rule_map[rid].get("source_document", "")

        # Recalculate compliance score from applicable results only (source of truth)
        sat = sum(1 for r in reqs if r.get("status") == "SATISFIES")
        score = round(sat / len(reqs) * 100) if reqs else 0
        result["compliance_score"] = score

        # Derive overall_verdict
        has_violations = any(r.get("status") == "VIOLATES" for r in reqs)
        if score >= 70 and not has_violations:
            result["overall_verdict"] = "COMPLIANT"
        elif score < 60:
            result["overall_verdict"] = "NON_COMPLIANT"
        else:
            result["overall_verdict"] = "PARTIALLY_COMPLIANT"

        # Derive approval_recommendation
        if score >= 70 and not has_violations:
            result["approval_recommendation"] = "APPROVE"
            result["conditions"] = []
        elif score < 60:
            result["approval_recommendation"] = "REJECT"
            result["conditions"] = []
        else:
            result["approval_recommendation"] = "CONDITIONAL_APPROVE"
            if not result["conditions"]:
                result["conditions"] = [
                    r["recommendation"]
                    for r in reqs
                    if r.get("status") == "VIOLATES" and r.get("recommendation")
                ]

        # Safety-net: executive summary must never say "Non-Compliant" — use "Requires Review"
        if isinstance(result.get("summary"), str):
            result["summary"] = (
                result["summary"]
                # Fix grammatical form first: "is non-compliant" → "requires review"
                .replace("is NON_COMPLIANT", "requires review")
                .replace("is Non-Compliant", "requires review")
                .replace("is non-compliant", "requires review")
                # Then replace any remaining standalone occurrences
                .replace("NON_COMPLIANT", "Requires Review")
                .replace("Non-Compliant", "Requires Review")
                .replace("non-compliant", "Requires Review")
            )

        return result

    except json.JSONDecodeError:
        logger.exception("Failed to parse AI JSON response for rules-based analysis")
        return _policy_fallback_result("AI response could not be parsed")
    except PolicyRateLimitError:
        raise
    except Exception:
        logger.exception("AI call failed for rules-based analysis")
        raise


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

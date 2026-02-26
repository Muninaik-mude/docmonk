import json
import logging

from groq import Groq
from django.conf import settings

logger = logging.getLogger(__name__)

# Max chars sent to AI per clause — keeps every call within any model's context window
# ~5,000 chars ≈ 1,250 tokens (vs 3,000,000 chars for a 1000-page PDF)
_EXCERPT_CHARS = 5_000


def _get_relevant_excerpt(full_text: str, clause_title: str, clause_content: str) -> str:
    """
    Extract the most relevant portion of the document for the given clause.

    For small documents (<= 5,000 chars) returns the full text.
    For large documents slides a 5,000-char window across the text in steps of 2,500 chars,
    scores each window by keyword overlap with the clause title + content,
    and returns the highest-scoring window.

    This ensures every Groq call stays well within the model's context window
    regardless of the PDF size (even 1000-page / 78 MB documents).
    """
    if len(full_text) <= _EXCERPT_CHARS:
        return full_text

    query = (clause_title + " " + clause_content).lower()
    query_words = {w for w in query.split() if len(w) > 3}

    if not query_words:
        return full_text[:_EXCERPT_CHARS]

    step = _EXCERPT_CHARS // 2
    best_score = -1
    best_pos = 0
    pos = 0

    while pos < len(full_text):
        end = min(pos + _EXCERPT_CHARS, len(full_text))
        chunk_words = set(full_text[pos:end].lower().split())
        score = len(query_words & chunk_words)
        if score > best_score:
            best_score = score
            best_pos = pos
        pos += step

    excerpt = full_text[best_pos: best_pos + _EXCERPT_CHARS]
    logger.debug(
        "Excerpt for clause '%s': pos=%d, score=%d, len=%d",
        clause_title, best_pos, best_score, len(excerpt),
    )
    return excerpt


# ── Per-clause analysis ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a legal contract compliance analyzer. Analyze whether a given clause is present, compliant, violated, or missing in the provided document text.

You must respond ONLY with valid JSON, no extra text or markdown formatting."""

USER_PROMPT_TEMPLATE = """CLAUSE TITLE: {title}
CLAUSE CONTENT: {content}

DOCUMENT TEXT:
{pdf_text}

Analyze the clause against the document and respond in this EXACT JSON format:
{{
    "result": "MATCH" or "NOT_FOUND" or "VIOLATION" or "PARTIALLY_SATISFIED",
    "reason": "brief explanation",
    "relevant_text": "the part of document that relates to this clause, or null",
    "ai_recommendation": "If NOT_FOUND: write the missing clause as it should appear. If VIOLATION: write a corrective clause. If PARTIALLY_SATISFIED: write the improved/completed clause. If MATCH: null",
    "parties_obligated": ["Tenant"] or ["Landlord"] or ["Both"] or [],
    "missing_values": ["commencement date not specified", "deposit amount blank"] or [],
    "binding_strength": "MUST/SHALL" or "SHOULD" or "MAY/CAN" or "VAGUE",
    "key_dates_durations": ["30 days notice required", "lease ends March 2029"] or []
}}

Rules:
- MATCH: clause content is present and fully compliant in the document
- PARTIALLY_SATISFIED: clause topic exists but is incomplete, vague, or only partly meets the requirement
- NOT_FOUND: clause topic is completely absent from the document
- VIOLATION: clause topic exists but contradicts or violates the clause requirement
- parties_obligated: list which party carries obligations under this clause (["Tenant"], ["Landlord"], ["Both"], or [] if not applicable)
- missing_values: list critical referenced values that are undefined or blank in the clause or document (e.g. amounts, dates, percentages left as blanks)
- binding_strength: classify the language strength — "MUST/SHALL" for mandatory/imperative, "SHOULD" for advisory, "MAY/CAN" for permissive/optional, "VAGUE" for non-binding phrases like "agrees to try" or "will endeavour"
- key_dates_durations: list all dates and durations found in this clause (e.g. "30 days", "March 2029", "within 60 days of execution", "5th of each month")
"""

_VALID_BINDING = ("MUST/SHALL", "SHOULD", "MAY/CAN", "VAGUE")


def _safe_json_parse(text: str) -> dict:
    """
    Parse JSON with automatic repair for truncated responses.

    Groq can hit max_tokens mid-response, leaving an unterminated string or
    unclosed object/array. This function:
      1. Strips code fences (``` ... ```)
      2. Tries a direct json.loads
      3. Tries to find the deepest valid close-brace and truncate there
      4. Strips trailing commas and force-closes the outermost object
    Raises json.JSONDecodeError only if all three strategies fail.
    """
    # Strip code fences
    if text.startswith("```"):
        lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    text = text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: find the last position where the root object/array is closed
    depth = 0
    in_string = False
    escape_next = False
    last_valid_end = -1

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch in ("{", "["):
                depth += 1
            elif ch in ("}", "]"):
                depth -= 1
                if depth == 0:
                    last_valid_end = i

    if last_valid_end > 0:
        try:
            return json.loads(text[: last_valid_end + 1])
        except json.JSONDecodeError:
            pass

    # Strategy 3: strip trailing partial field + dangling comma, then force-close
    stripped = text.rstrip().rstrip(",")
    if not stripped.endswith("}"):
        stripped += "}"
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    raise json.JSONDecodeError("Cannot repair truncated JSON", text, len(text))


def _parse_response(response_text: str) -> dict:
    """Parse and validate the AI JSON response."""
    result = _safe_json_parse(response_text)

    if result.get("result") not in ("MATCH", "NOT_FOUND", "VIOLATION", "PARTIALLY_SATISFIED"):
        logger.warning("Invalid result value: %s, defaulting to NOT_FOUND", result.get("result"))
        result["result"] = "NOT_FOUND"

    # Validate / default the 4 new fields
    if not isinstance(result.get("parties_obligated"), list):
        result["parties_obligated"] = []
    if not isinstance(result.get("missing_values"), list):
        result["missing_values"] = []
    if result.get("binding_strength") not in _VALID_BINDING:
        result["binding_strength"] = "VAGUE"
    if not isinstance(result.get("key_dates_durations"), list):
        result["key_dates_durations"] = []

    return result


def _call_groq(user_message: str) -> str:
    """Call Groq AI and return the raw response text."""
    client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=60.0)
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        model=settings.GROQ_MODEL,
        temperature=0.1,
        max_tokens=1500,
    )
    return chat_completion.choices[0].message.content.strip()


def analyze_clause_against_pdf(clause: dict, pdf_text: str) -> dict:
    """
    Analyze clause compliance using Groq AI.
    Returns dict with: result, reason, relevant_text, ai_recommendation,
                       parties_obligated, missing_values, binding_strength, key_dates_durations
    """
    title = clause["title"]
    content = clause.get("content") or clause.get("value", "")

    excerpt = _get_relevant_excerpt(pdf_text, title, content)

    user_message = USER_PROMPT_TEMPLATE.format(
        title=title,
        content=content,
        pdf_text=excerpt,
    )

    try:
        response_text = _call_groq(user_message)
        result = _parse_response(response_text)
        result["_provider"] = "groq"
        return result
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Groq JSON response for clause '%s': %s", title, e)
        return {
            "result": "NOT_FOUND",
            "reason": "AI response could not be parsed",
            "relevant_text": None,
            "ai_recommendation": f"Clause '{title}' could not be analyzed. Manual review recommended.",
            "parties_obligated": [],
            "missing_values": [],
            "binding_strength": "VAGUE",
            "key_dates_durations": [],
            "_provider": "groq",
        }
    except Exception as e:
        logger.error("Groq failed for clause '%s': %s", title, e)
        return {
            "result": "NOT_FOUND",
            "reason": f"AI analysis failed: {type(e).__name__}",
            "relevant_text": None,
            "ai_recommendation": f"Clause '{title}' could not be analyzed due to API error. Manual review recommended.",
            "parties_obligated": [],
            "missing_values": [],
            "binding_strength": "VAGUE",
            "key_dates_durations": [],
            "_provider": "groq",
        }


# ── Document-level analysis ──────────────────────────────────────────────────────

_JURISDICTION_SYSTEM = """You are a legal jurisdiction analyst. Analyze the provided contract text and identify the governing jurisdiction, agreement type, and applicable laws. Respond ONLY with valid JSON, no extra text or markdown."""

_JURISDICTION_USER = """DOCUMENT EXCERPT (first 3000 characters):
{pdf_excerpt}

Identify the jurisdiction and compliance requirements. Respond in this EXACT JSON format:
{{
    "jurisdiction": "State/Country name or 'Unknown'",
    "agreement_type": "e.g. Commercial Rental Agreement",
    "applicable_laws": ["Indian Contract Act 1872", "Telangana Stamp Act"],
    "checklist": [
        {{"item": "Registration required under Registration Act", "required": true}},
        {{"item": "Stamp duty payment required", "required": true}},
        {{"item": "Notarization required", "required": false}}
    ]
}}

Rules:
- If jurisdiction is unclear set "jurisdiction" to "Unknown"
- checklist should contain 3-6 key compliance requirements for this jurisdiction and agreement type
- required: true means mandatory, false means optional/recommended
"""


def detect_jurisdiction(pdf_text: str) -> dict:
    """
    Analyze the first 3000 chars of the document to detect jurisdiction and build a
    compliance checklist. Called ONCE before the per-clause loop.

    Returns: {jurisdiction, agreement_type, applicable_laws, checklist}
    """
    excerpt = pdf_text[:3000]
    user_message = _JURISDICTION_USER.format(pdf_excerpt=excerpt)
    client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=60.0)
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": _JURISDICTION_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            model=settings.GROQ_MODEL,
            temperature=0.1,
            max_tokens=1000,
        )
        response_text = chat_completion.choices[0].message.content.strip()
        result = _safe_json_parse(response_text)
        if not isinstance(result.get("checklist"), list):
            result["checklist"] = []
        if not isinstance(result.get("applicable_laws"), list):
            result["applicable_laws"] = []
        return result
    except Exception as e:
        logger.error("detect_jurisdiction failed: %s", e)
        return {
            "jurisdiction": "Unknown",
            "agreement_type": "Unknown",
            "applicable_laws": [],
            "checklist": [],
        }


_CONFLICT_SYSTEM = """You are a legal contract conflict analyst. Identify contradictions or conflicts between clauses in a contract analysis. Respond ONLY with valid JSON, no extra text or markdown."""

_CONFLICT_USER = """CLAUSE ANALYSIS RESULTS:
{clause_summary_text}

Identify any direct contradictions, conflicts, or inconsistencies between these clauses.
Respond in this EXACT JSON format:
{{
    "conflicts": [
        {{
            "clause_a": "Clause title A",
            "clause_b": "Clause title B",
            "conflict": "Brief description of the contradiction"
        }}
    ]
}}

Rules:
- Return an empty list for "conflicts" if no genuine contradictions exist
- Only report direct logical contradictions (e.g. one clause says 30 days notice, another says 60 days)
- Maximum 5 conflicts
"""


def detect_conflicts(analysis_summary: list) -> list:
    """
    Compare all clause analysis results for contradictions/conflicts.
    Called ONCE after all per-clause analyses are complete.

    Returns: [{clause_a, clause_b, conflict}, ...] or []
    """
    lines = []
    for entry in analysis_summary:
        lines.append(
            f"- {entry['clause_title']} [{entry['result']}]: {entry.get('reason', '')}"
        )
    clause_summary_text = "\n".join(lines)

    user_message = _CONFLICT_USER.format(clause_summary_text=clause_summary_text)
    client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=60.0)
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": _CONFLICT_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            model=settings.GROQ_MODEL,
            temperature=0.1,
            max_tokens=800,
        )
        response_text = chat_completion.choices[0].message.content.strip()
        result = _safe_json_parse(response_text)
        conflicts = result.get("conflicts", [])
        return conflicts if isinstance(conflicts, list) else []
    except Exception as e:
        logger.error("detect_conflicts failed: %s", e)
        return []


# ── PDF text location helper ──────────────────────────────────────────────────────

def find_text_location_in_pdf(text_blocks: list, search_text: str) -> dict | None:
    """
    Find which page and bbox contains text most similar to search_text.
    Uses substring matching with case-insensitive comparison.
    """
    if not search_text:
        return None

    search_lower = search_text.lower()
    best_match = None
    best_overlap = 0

    for block in text_blocks:
        block_text_lower = block["text"].lower()

        if search_lower in block_text_lower:
            return {"page_num": block["page_num"], "bbox": block["bbox"]}

        search_words = set(search_lower.split())
        block_words = set(block_text_lower.split())
        overlap = len(search_words & block_words)

        if overlap > best_overlap and overlap >= len(search_words) * 0.3:
            best_overlap = overlap
            best_match = {"page_num": block["page_num"], "bbox": block["bbox"]}

    return best_match

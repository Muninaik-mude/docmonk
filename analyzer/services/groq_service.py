import json
import logging
import random
import time

from groq import Groq, RateLimitError as GroqRateLimitError
from django.conf import settings

logger = logging.getLogger(__name__)

# Max chars sent to AI per clause — keeps every call within any model's context window
_EXCERPT_CHARS = 5_000

# Retry config
_MAX_RETRIES = 5
_BASE_DELAY  = 1.0  # fallback if server doesn't provide retry-after


def _get_relevant_excerpt(full_text: str, clause_title: str, clause_content: str) -> str:
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
    logger.debug("Excerpt for '%s': pos=%d score=%d len=%d", clause_title, best_pos, best_score, len(excerpt))
    return excerpt


# ── Smart retry helper ────────────────────────────────────────────────────────────

def _parse_retry_wait(e: GroqRateLimitError) -> float | None:
    """
    Extract the server-suggested wait time from Groq's rate-limit response headers.

    Groq sends one of:
      retry-after                  → seconds (float)
      x-ratelimit-reset-tokens     → e.g. "0.952s" or "952ms"
      x-ratelimit-reset-requests   → same format

    Returns seconds to wait, or None if headers are unavailable.
    """
    try:
        headers = e.response.headers

        ra = headers.get("retry-after")
        if ra:
            return float(ra) + 0.05

        for key in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
            val = headers.get(key, "")
            if val.endswith("ms"):
                return float(val[:-2]) / 1000 + 0.05
            if val.endswith("s"):
                return float(val[:-1]) + 0.05
    except Exception:
        pass
    return None


# ── Shared Groq caller with smart retry ──────────────────────────────────────────

def _call_groq(user_message: str, system_prompt: str, max_tokens: int = 1500) -> str:
    """
    Call Groq AI with smart rate-limit retry.

    On 429 RateLimitError:
      1. Reads the server's 'retry-after' / 'x-ratelimit-reset-tokens' header
         and waits exactly that long (+ 50 ms jitter) — no wasted time.
      2. Falls back to exponential backoff only when headers are unavailable.

    All other exceptions propagate immediately.
    Thread-safe — each call creates its own Groq client.
    """
    client = Groq(api_key=settings.GROQ_API_KEY)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                model=settings.GROQ_MODEL,
                temperature=0.1,
                max_tokens=max_tokens,
            )
            return completion.choices[0].message.content.strip()

        except GroqRateLimitError as e:
            if attempt == _MAX_RETRIES:
                logger.error("Groq rate limit — exhausted %d retries", _MAX_RETRIES)
                raise

            # Use server-suggested wait time; fall back to exponential backoff
            wait = _parse_retry_wait(e)
            if wait is None:
                wait = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)

            logger.warning("Groq rate limit — retry %d/%d in %.2f s", attempt + 1, _MAX_RETRIES, wait)
            time.sleep(wait)


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
- parties_obligated: list which party carries obligations under this clause
- missing_values: list critical referenced values that are undefined or blank
- binding_strength: "MUST/SHALL" mandatory, "SHOULD" advisory, "MAY/CAN" permissive, "VAGUE" non-binding
- key_dates_durations: list all dates and durations found in this clause
"""

_VALID_BINDING = ("MUST/SHALL", "SHOULD", "MAY/CAN", "VAGUE")


def _safe_json_parse(text: str) -> dict:
    if text.startswith("```"):
        lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

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

    stripped = text.rstrip().rstrip(",")
    if not stripped.endswith("}"):
        stripped += "}"
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    raise json.JSONDecodeError("Cannot repair truncated JSON", text, len(text))


def _parse_response(response_text: str) -> dict:
    result = _safe_json_parse(response_text)

    if result.get("result") not in ("MATCH", "NOT_FOUND", "VIOLATION", "PARTIALLY_SATISFIED"):
        logger.warning("Invalid result value '%s', defaulting to NOT_FOUND", result.get("result"))
        result["result"] = "NOT_FOUND"

    if not isinstance(result.get("parties_obligated"), list):
        result["parties_obligated"] = []
    if not isinstance(result.get("missing_values"), list):
        result["missing_values"] = []
    if result.get("binding_strength") not in _VALID_BINDING:
        result["binding_strength"] = "VAGUE"
    if not isinstance(result.get("key_dates_durations"), list):
        result["key_dates_durations"] = []

    return result


def analyze_clause_against_pdf(clause: dict, pdf_text: str) -> dict:
    """
    Analyze clause compliance using Groq AI.
    Thread-safe — called concurrently from ThreadPoolExecutor.
    """
    title   = clause["title"]
    content = clause.get("content") or clause.get("value", "")
    excerpt = _get_relevant_excerpt(pdf_text, title, content)

    user_message = USER_PROMPT_TEMPLATE.format(title=title, content=content, pdf_text=excerpt)

    try:
        response_text = _call_groq(user_message, SYSTEM_PROMPT, max_tokens=1500)
        result = _parse_response(response_text)
        result["_provider"] = "groq"
        return result
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Groq JSON for clause '%s': %s", title, e)
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
        raise


# ── Combined document context (metadata + jurisdiction in ONE call) ───────────────

_CONTEXT_SYSTEM = """You are a legal document analyst. Extract structured metadata and jurisdiction info from the contract text. Respond ONLY with valid JSON, no extra text or markdown."""

_CONTEXT_USER = """DOCUMENT TEXT (first 4000 characters):
{excerpt}

Extract all document context and respond in this EXACT JSON format:
{{
    "agreement_type": "e.g. Commercial Rental Agreement",
    "agreement_details": {{
        "agreement_date": "YYYY-MM-DD or empty string",
        "city": "city name or empty string",
        "state": "state/province or empty string"
    }},
    "parties": {{
        "landlord": {{
            "name": "full name or empty string",
            "address": "address or empty string",
            "contact": "email/phone or empty string"
        }},
        "tenant": {{
            "name": "full name or empty string",
            "company_name": "company name or empty string",
            "authorized_signatory": "signatory name or empty string",
            "address": "address or empty string",
            "contact": "email/phone or empty string"
        }}
    }},
    "property": {{
        "type": "e.g. Commercial Office Space or empty string",
        "area_sqft": null,
        "address": "property address or empty string"
    }},
    "jurisdiction": "State/Country name or Unknown",
    "applicable_laws": ["Indian Contract Act 1872", "Karnataka Rent Control Act"],
    "checklist": [
        {{"item": "Registration required under Registration Act", "required": true}},
        {{"item": "Stamp duty payment required", "required": true}},
        {{"item": "Notarization required", "required": false}}
    ]
}}

Rules:
- Use empty string for missing text fields, null for missing numeric fields
- jurisdiction: detect from governing law clauses, party addresses, or property location
- checklist: 3-6 key compliance requirements for this jurisdiction and agreement type
- required: true = mandatory, false = optional/recommended
"""


def extract_document_context(full_text: str) -> dict:
    """
    Single Groq call that extracts BOTH document metadata AND jurisdiction info.
    Replaces the two separate parallel calls (extract_document_metadata + detect_jurisdiction).

    Returns combined dict with keys:
        agreement_type, agreement_details, parties, property,
        jurisdiction, applicable_laws, checklist
    """
    excerpt = full_text[:4000]
    try:
        response_text = _call_groq(_CONTEXT_USER.format(excerpt=excerpt), _CONTEXT_SYSTEM, max_tokens=1200)
        result = _safe_json_parse(response_text)

        # Structural defaults
        if not isinstance(result.get("agreement_details"), dict):
            result["agreement_details"] = {"agreement_date": "", "city": "", "state": ""}
        if not isinstance(result.get("parties"), dict):
            result["parties"] = {"landlord": {}, "tenant": {}}
        if not isinstance(result.get("property"), dict):
            result["property"] = {"type": "", "area_sqft": None, "address": ""}
        if not isinstance(result.get("applicable_laws"), list):
            result["applicable_laws"] = []
        if not isinstance(result.get("checklist"), list):
            result["checklist"] = []

        return result
    except Exception as e:
        logger.error("extract_document_context failed: %s", e)
        return {
            "agreement_type": "",
            "agreement_details": {"agreement_date": "", "city": "", "state": ""},
            "parties": {"landlord": {}, "tenant": {}},
            "property": {"type": "", "area_sqft": None, "address": ""},
            "jurisdiction": "Unknown",
            "applicable_laws": [],
            "checklist": [],
        }


# ── PDF text location helper ──────────────────────────────────────────────────────

def find_text_location_in_pdf(text_blocks: list, search_text: str) -> dict | None:
    if not search_text:
        return None

    search_lower = search_text.lower()
    best_match   = None
    best_overlap = 0

    for block in text_blocks:
        block_text_lower = block["text"].lower()

        if search_lower in block_text_lower:
            return {"page_num": block["page_num"], "bbox": block["bbox"]}

        search_words = set(search_lower.split())
        block_words  = set(block_text_lower.split())
        overlap      = len(search_words & block_words)

        if overlap > best_overlap and overlap >= len(search_words) * 0.3:
            best_overlap = overlap
            best_match   = {"page_num": block["page_num"], "bbox": block["bbox"]}

    return best_match

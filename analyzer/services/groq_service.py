import json
import logging
import re
import threading
import time

from openai import OpenAI, RateLimitError
from django.conf import settings

# Matches a leading numbering prefix like "3.1 ", "7.10 ", "2.3.1 ", "6. ", "1) "
_NUM_PREFIX_RE = re.compile(r'^(\d+(?:\.\d+)*[\s.):]+)')

logger = logging.getLogger(__name__)

# Seconds to wait when ALL providers are simultaneously rate-limited
_RATE_LIMIT_RETRY_WAIT = 8.0
# Max full-pool sweeps before giving up (on a single _call_ai invocation)
_MAX_RETRIES = 3


# ── Multi-provider client pool ────────────────────────────────────────────────────

_pool_lock:   threading.Lock = threading.Lock()
_pool:        list           = []
_pool_built:  bool           = False
_idx_lock:    threading.Lock = threading.Lock()
_idx:         int            = 0


def _build_provider_pool() -> list:
    """
    Build the ordered list of AI provider clients from Django settings.

    Provider order: groq_1, groq_2, cerebras_1, cerebras_2
    Any provider whose API key is not set is silently skipped.
    """
    pool = []
    groq_model     = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
    cerebras_model = "gpt-oss-120b"

    for slot, attr in enumerate(["GROQ_API_KEY_1", "GROQ_API_KEY_2"], start=1):
        key = getattr(settings, attr, None)
        if key:
            pool.append({
                "name":   f"groq_{slot}",
                "client": OpenAI(
                    api_key=key,
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=0,
                    timeout=60.0,
                ),
                "model": groq_model,
            })

    for slot, attr in enumerate(["CEREBRAS_KEY_1", "CEREBRAS_KEY_2"], start=1):
        key = getattr(settings, attr, None)
        if key:
            pool.append({
                "name":   f"cerebras_{slot}",
                "client": OpenAI(
                    api_key=key,
                    base_url="https://api.cerebras.ai/v1",
                    max_retries=0,
                    timeout=60.0,
                ),
                "model": cerebras_model,
            })

    logger.info(
        "AI provider pool built: %s",
        [p["name"] for p in pool] or ["<none — check env vars>"],
    )
    return pool


def _get_pool() -> list:
    """Return the shared provider pool, building it once on first call."""
    global _pool, _pool_built
    if not _pool_built:
        with _pool_lock:
            if not _pool_built:
                _pool = _build_provider_pool()
                _pool_built = True
    return _pool


def _next_start_idx() -> int:
    """Thread-safe increment; returns the index this call should start from."""
    global _idx
    with _idx_lock:
        i = _idx
        _idx += 1
    return i


def _call_ai(
    user_message: str,
    system_prompt: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.1,
) -> str:
    """
    Call an AI provider from the pool using round-robin selection.

    Strategy:
      • Pick a start offset via global round-robin counter so parallel calls
        spread evenly across providers.
      • On RateLimitError from one provider, immediately rotate to the next.
      • If every provider in the pool is rate-limited during one sweep,
        wait _RATE_LIMIT_RETRY_WAIT seconds then sweep again.
      • Raises RuntimeError after _MAX_RETRIES full sweeps all hit rate limits.
    """
    pool = _get_pool()
    if not pool:
        raise RuntimeError(
            "No AI providers configured. "
            "Set at least one of: GROQ_API_KEY_1, GROQ_API_KEY_2, "
            "CEREBRAS_KEY_1, CEREBRAS_KEY_2."
        )

    n     = len(pool)
    start = _next_start_idx()

    for attempt in range(_MAX_RETRIES + 1):
        rate_limited = 0

        for offset in range(n):
            provider = pool[(start + offset) % n]
            try:
                resp = provider["client"].chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_message},
                    ],
                    model=provider["model"],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = resp.choices[0].message.content
                return content.strip() if content is not None else ""

            except RateLimitError:
                logger.warning(
                    "Rate limited on %s (sweep %d/%d), rotating to next provider",
                    provider["name"], attempt + 1, _MAX_RETRIES + 1,
                )
                rate_limited += 1
                continue

            except Exception:
                raise   # Non-rate-limit errors propagate immediately

        # All providers were rate-limited in this sweep
        if attempt < _MAX_RETRIES:
            wait = _RATE_LIMIT_RETRY_WAIT * (attempt + 1)
            logger.warning(
                "All %d providers rate-limited — waiting %.1fs before retry %d/%d",
                n, wait, attempt + 1, _MAX_RETRIES,
            )
            time.sleep(wait)

    raise RuntimeError(
        f"All {n} AI providers exhausted after {_MAX_RETRIES + 1} full sweeps."
    )


# ── AI-based relevant text extraction ───────────────────────────────────────────

_EXTRACT_SYSTEM = """You are a document search assistant. Given a clause topic and a legal document, return ONLY the exact verbatim sentence(s) or paragraph(s) from the document that are directly relevant to that clause. Do not paraphrase, summarize, or add any explanation. Return the copied text only."""

_EXTRACT_USER = """CLAUSE TOPIC: {title}

DOCUMENT TEXT:
{doc_text}

Copy verbatim only the sentence(s) from DOCUMENT TEXT that directly mention or relate to "{title}".
IMPORTANT: If the relevant sentence(s) are part of a numbered section or sub-clause (e.g. "3.1 Fees:", "7.10 Assignment:", "2.3.1 Obligations:"), include that full line from the start — do not strip the numbering prefix. Return only the copied text with no extra words. If nothing relevant exists, return an empty string."""


def _extract_relevant_text_via_ai(title: str, doc_text: str) -> str:
    """
    Use a fast AI call to extract the exact verbatim sentences from the document
    that are relevant to the given clause title.

    Returns the extracted text, or empty string if nothing found / on error.
    """
    user_message = _EXTRACT_USER.format(title=title, doc_text=doc_text)
    try:
        return _call_ai(
            user_message,
            _EXTRACT_SYSTEM,
            max_tokens=500,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning("Excerpt extraction failed for '%s': %s", title, e)
        return ""


# ── Per-clause analysis ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a strict legal contract compliance auditor. Your job is to compare a REQUIRED CLAUSE (the standard/library clause) against the ACTUAL DOCUMENT TEXT and determine whether the document satisfies, violates, or is missing that clause.

You are not checking if a topic merely exists. You are verifying whether the document's specific terms — amounts, dates, jurisdictions, named acts, restrictions, percentages, locations — exactly match or conflict with the required clause.

You must respond ONLY with valid JSON, no extra text or markdown formatting."""

USER_PROMPT_TEMPLATE = """REQUIRED CLAUSE (this is what the contract SHOULD contain):
TITLE: {title}
CONTENT: {content}

ACTUAL DOCUMENT TEXT (this is what the contract ACTUALLY says):
{pdf_text}

Your task: Compare the REQUIRED CLAUSE against the ACTUAL DOCUMENT TEXT with the following strict methodology:

STEP 1 — Does the document contain this clause topic at all?
  - If NO → result is NOT_FOUND

STEP 2 — If YES, compare every specific value in the required clause against the document:
  - Monetary amounts (e.g. Rs.2,25,000 required vs Rs.1,50,000 in document → VIOLATION)
  - Named laws/acts (e.g. "Telangana Stamp Act" required vs "Indian Stamp Act" in document → VIOLATION)
  - Locations/jurisdictions (e.g. "Hyderabad" required vs "Bengaluru" in document → VIOLATION)
  - Dates and durations (e.g. "7-day grace period" required vs no grace period in document → VIOLATION)
  - Scope restrictions (e.g. "IT and Software Development only" required vs "commercial/business" in document — narrowing scope is a VIOLATION)
  - If ANY specific value conflicts → result is VIOLATION

STEP 3 — If no conflicts, check completeness:
  - If the document clause is vague where the required clause is specific (e.g. "appropriate insurance" vs "fire and liability insurance worth Rs.50,00,000") → PARTIALLY_SATISFIED
  - If the document clause is missing required sub-conditions (e.g. required clause adds grace period, commencement date, specific party obligations not in document) → PARTIALLY_SATISFIED

STEP 4 — If all values match and clause is complete → MATCH

Respond in this EXACT JSON format:
{{
    "result": "MATCH" or "NOT_FOUND" or "VIOLATION" or "PARTIALLY_SATISFIED",
    "reason": "Specific explanation citing the exact conflicting/missing values — quote the document text and the required clause value side by side",
    "relevant_text": "the exact sentence(s) from the document that relate to this clause, or null",
    "ai_recommendation": "If NOT_FOUND: write the missing clause as it should appear. If VIOLATION or PARTIALLY_SATISFIED: write a corrective/improved clause — if relevant_text literally starts with a numbering prefix such as '3.1', '7.10', '2.3', '1)', then begin ai_recommendation with that exact same prefix; if relevant_text starts with a bullet (•, *, -) or any non-digit character, do NOT add any number prefix. If MATCH: null",
    "parties_obligated": ["Tenant"] or ["Landlord"] or ["Both"] or [],
    "missing_values": ["document says Rs.1,50,000 but required clause says Rs.2,25,000", "no commencement date specified"] or [],
    "binding_strength": "MUST/SHALL" or "SHOULD" or "MAY/CAN" or "VAGUE",
    "key_dates_durations": ["30 days notice required", "lease ends March 2029"] or []
}}

Classification rules (apply strictly):
- MATCH: ALL specific values in the required clause are present and consistent in the document — no deviations whatsoever
- VIOLATION: The topic exists in the document BUT at least one specific value (amount, act name, location, duration, scope) differs from the required clause — even a single Rs.1 difference or a different city name is a VIOLATION
- PARTIALLY_SATISFIED: The topic exists and no direct value conflict, but the document version is vaguer, less specific, or missing sub-conditions compared to the required clause
- NOT_FOUND: The clause topic is entirely absent from the document

Additional field rules:
- reason: Write a precise, professional legal finding (one sentence, no filler). Use these formats:
    VIOLATION → "The agreement specifies '[exact doc text]' whereas the prescribed standard requires '[exact required text]'."
    NOT_FOUND → "The executed agreement contains no provision for [clause title], a mandatory compliance requirement."
    PARTIALLY_SATISFIED → "The agreement addresses [topic] as '[doc text]' but lacks the required specificity: '[required text]'."
    MATCH → "The agreement satisfies this requirement — [brief confirmation of matching values]."
- missing_values: List every specific value that differs or is absent (amounts, dates, act names, locations, percentages)
- binding_strength: classify the language strength — "MUST/SHALL" for mandatory/imperative, "SHOULD" for advisory, "MAY/CAN" for permissive/optional, "VAGUE" for non-binding phrases like "agrees to try" or "appropriate" without specifics
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


def _fix_recommendation_prefix(relevant_text: str | None, recommendation: str | None) -> str | None:
    """
    Align the leading prefix of ai_recommendation with that of relevant_text.

    - If relevant_text starts with a numbering prefix (e.g. '3.1 ', '7.10 ')
      and recommendation does NOT already start with that prefix → prepend it.
    - If relevant_text does NOT start with a numbering prefix (starts with a bullet,
      letter, etc.) but recommendation has a spurious one → strip it.
    """
    if not recommendation or not relevant_text:
        return recommendation

    rel = relevant_text.lstrip()
    rec = recommendation.lstrip()

    m_rel = _NUM_PREFIX_RE.match(rel)
    if m_rel:
        # relevant_text has a numbering prefix — ensure recommendation starts with it
        prefix = m_rel.group(1)
        if not rec.startswith(prefix.rstrip()):
            return prefix + rec

    return recommendation


def _parse_response(response_text: str) -> dict:
    """Parse and validate the AI JSON response."""
    result = _safe_json_parse(response_text)

    if result.get("result") not in ("MATCH", "NOT_FOUND", "VIOLATION", "PARTIALLY_SATISFIED"):
        logger.warning("Invalid result value: %s, defaulting to NOT_FOUND", result.get("result"))
        result["result"] = "NOT_FOUND"

    if not isinstance(result.get("parties_obligated"), list):
        result["parties_obligated"] = []
    if not isinstance(result.get("missing_values"), list):
        result["missing_values"] = []
    if result.get("binding_strength") not in _VALID_BINDING:
        result["binding_strength"] = "VAGUE"
    if not isinstance(result.get("key_dates_durations"), list):
        result["key_dates_durations"] = []

    # Fix prefix alignment: ensure ai_recommendation prefix matches relevant_text
    result["ai_recommendation"] = _fix_recommendation_prefix(
        result.get("relevant_text"),
        result.get("ai_recommendation"),
    )

    return result


def analyze_clause_against_pdf(clause: dict, pdf_text: str) -> dict:
    """
    Analyze clause compliance using a two-step AI pipeline:
      Step 1 — AI extracts the verbatim relevant text for this clause from the document.
      Step 2 — AI analyzes that extracted text against the clause requirement.

    Both steps use the shared multi-provider pool (round-robin, rate-limit aware).

    Returns dict with: result, reason, relevant_text, ai_recommendation,
                       parties_obligated, missing_values, binding_strength, key_dates_durations
    """
    title   = clause["title"]
    content = clause["value"]

    # Step 1: AI extracts the verbatim relevant text directly from the full document
    excerpt = _extract_relevant_text_via_ai(title, pdf_text)
    logger.debug("Clause '%s': ai_excerpt=%d chars", title, len(excerpt))

    user_message = USER_PROMPT_TEMPLATE.format(
        title=title,
        content=content,
        pdf_text=excerpt,
    )

    try:
        response_text = _call_ai(user_message, SYSTEM_PROMPT, max_tokens=1500, temperature=0.1)
        result = _parse_response(response_text)
        result["_provider"] = "pool"
        return result
    except json.JSONDecodeError as e:
        logger.error("Failed to parse AI JSON response for clause '%s': %s", title, e)
        return {
            "result": "NOT_FOUND",
            "reason": "AI response could not be parsed",
            "relevant_text": None,
            "ai_recommendation": f"Clause '{title}' could not be analyzed. Manual review recommended.",
            "parties_obligated": [],
            "missing_values": [],
            "binding_strength": "VAGUE",
            "key_dates_durations": [],
            "_provider": "pool",
        }
    except Exception as e:
        logger.error("AI call failed for clause '%s': %s", title, e)
        return {
            "result": "NOT_FOUND",
            "reason": f"AI analysis failed: {type(e).__name__}",
            "relevant_text": None,
            "ai_recommendation": f"Clause '{title}' could not be analyzed due to API error. Manual review recommended.",
            "parties_obligated": [],
            "missing_values": [],
            "binding_strength": "VAGUE",
            "key_dates_durations": [],
            "_provider": "pool",
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

    try:
        response_text = _call_ai(
            user_message, _JURISDICTION_SYSTEM, max_tokens=1000, temperature=0.1,
        )
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

    try:
        response_text = _call_ai(
            user_message, _CONFLICT_SYSTEM, max_tokens=800, temperature=0.1,
        )
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

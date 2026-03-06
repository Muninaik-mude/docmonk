import json
import logging
import re
import threading
import time

try:
    # In production (gevent workers), use gevent.sleep so the sleep is
    # cooperative and yields to other greenlets instead of blocking the worker.
    from gevent import sleep as _sleep
except ImportError:
    # Dev / non-gevent environments: fall back to time.sleep.
    # Gunicorn gevent monkey-patches time.sleep anyway, so this is equivalent
    # in production if gevent is installed before this module is imported.
    _sleep = time.sleep

from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError
from django.conf import settings

# Matches a leading numbering prefix like "3.1 ", "7.10 ", "2.3.1 ", "6. ", "1) "
_NUM_PREFIX_RE = re.compile(r'^(\d+(?:\.\d+)*[\s.):]+)')

logger = logging.getLogger(__name__)

# Seconds to wait when ALL providers are simultaneously rate-limited
_RATE_LIMIT_RETRY_WAIT = 8.0
# Max full-pool sweeps before giving up (on a single _call_ai invocation)
_MAX_RETRIES = 3


# ── Multi-provider client pool ─────────────────────────────────────────────────

_pool_lock:   threading.Lock = threading.Lock()
_pool:        list           = []
_pool_built:  bool           = False
_idx_lock:    threading.Lock = threading.Lock()
_idx:         int            = 0

# ── Circuit breaker ────────────────────────────────────────────────────────────
# When a provider returns a 5xx error or a connection error, it is cooled down
# for _PROVIDER_COOLDOWN_SECS seconds. All calls during that window skip it and
# try the next provider. The cooldown resets automatically when the timer expires.

_PROVIDER_COOLDOWN_SECS = 60
_cooldown_lock:      threading.Lock = threading.Lock()
_provider_cooldowns: dict           = {}  # {provider_name: float} — expiry unix timestamp


def _mark_provider_down(name: str) -> None:
    """Put provider in cooldown after a 5xx or connection error."""
    with _cooldown_lock:
        _provider_cooldowns[name] = time.time() + _PROVIDER_COOLDOWN_SECS
    logger.warning(
        "Provider %s cooling down for %ds (5xx or connection error)",
        name, _PROVIDER_COOLDOWN_SECS,
    )


def _is_provider_available(name: str) -> bool:
    """Return True if the provider's cooldown has expired (or was never set)."""
    with _cooldown_lock:
        return time.time() >= _provider_cooldowns.get(name, 0)


def _build_provider_pool() -> list:
    """
    Build the ordered list of AI provider clients from Django settings.

    Provider order: groq_1, groq_2, cerebras_1, cerebras_2
    Any provider whose API key is not set is silently skipped.
    """
    pool = []
    groq_model     = getattr(settings, "GROQ_MODEL",     "llama-3.3-70b-versatile")
    cerebras_model = getattr(settings, "CEREBRAS_MODEL", "gpt-oss-120b")

    for slot, attr in enumerate(["GROQ_API_KEY_1", "GROQ_API_KEY_2"], start=1):
        key = getattr(settings, attr, None)
        if key:
            pool.append({
                "name":   f"groq_{slot}",
                "client": OpenAI(
                    api_key=key,
                    base_url="https://api.cerebras.ai/v1",
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


def _validated_pool() -> tuple[list, int, int]:
    """
    Validate the provider pool is non-empty and return (pool, n, start_idx).
    Raises RuntimeError if no providers are configured.
    """
    pool = _get_pool()
    if not pool:
        raise RuntimeError(
            "No AI providers configured. "
            "Set at least one of: GROQ_API_KEY_1, GROQ_API_KEY_2, "
            "CEREBRAS_KEY_1, CEREBRAS_KEY_2."
        )
    return pool, len(pool), _next_start_idx()


# ── Core AI callers ────────────────────────────────────────────────────────────

def _call_ai(
    messages: list,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.1,
) -> str:
    """
    Non-streaming AI call using round-robin provider selection.

    messages: pre-built list of {role, content} dicts (system + user, optionally
              with assistant history turns for multi-turn conversations).

    Strategy:
      • Pick a start offset via global round-robin counter so parallel calls
        spread evenly across providers.
      • On RateLimitError from one provider, immediately rotate to the next.
      • If every provider in the pool is rate-limited during one sweep,
        wait _RATE_LIMIT_RETRY_WAIT seconds then sweep again.
      • Raises RuntimeError after _MAX_RETRIES full sweeps all hit rate limits.
    """
    pool, n, start = _validated_pool()

    for attempt in range(_MAX_RETRIES + 1):
        for offset in range(n):
            provider = pool[(start + offset) % n]

            if not _is_provider_available(provider["name"]):
                logger.debug("Skipping %s — in cooldown", provider["name"])
                continue

            try:
                resp = provider["client"].chat.completions.create(
                    messages=messages,
                    model=provider["model"],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = resp.choices[0].message.content
                return content.strip() if content is not None else ""

            except RateLimitError:
                logger.warning(
                    "Rate limited on %s (sweep %d/%d), trying next provider",
                    provider["name"], attempt + 1, _MAX_RETRIES + 1,
                )
                continue

            except APIStatusError as e:
                if e.status_code >= 500:
                    _mark_provider_down(provider["name"])
                    continue
                raise  # 4xx other than 429 — propagate immediately

            except APIConnectionError:
                _mark_provider_down(provider["name"])
                continue

            except Exception:
                raise  # Unexpected errors propagate immediately

        # All n providers were unavailable this sweep — wait and retry
        if attempt < _MAX_RETRIES:
            wait = _RATE_LIMIT_RETRY_WAIT * (attempt + 1)
            logger.warning(
                "All %d providers unavailable (sweep %d/%d) — waiting %.1fs before retry",
                n, attempt + 1, _MAX_RETRIES + 1, wait,
            )
            _sleep(wait)

    raise RuntimeError(
        f"All {n} AI providers exhausted after {_MAX_RETRIES + 1} full sweeps."
    )


def _call_ai_stream(
    messages: list,
    *,
    max_tokens: int = 1000,
    temperature: float = 0.1,
):
    """
    Streaming AI call using round-robin provider selection.

    messages: pre-built list of {role, content} dicts — same format as _call_ai,
              supports multi-turn history by including prior assistant turns.

    Yields str chunks as they arrive from the provider.
    Yields None every 15 s during provider-wait periods so the SSE layer can
    emit keepalive comments and prevent proxy/browser idle-timeout disconnects.
    """
    pool, n, start = _validated_pool()

    for attempt in range(_MAX_RETRIES + 1):
        for offset in range(n):
            provider = pool[(start + offset) % n]

            if not _is_provider_available(provider["name"]):
                logger.debug("Skipping %s — in cooldown", provider["name"])
                continue

            # ── Pre-stream: open the connection ───────────────────────────────
            try:
                stream = provider["client"].chat.completions.create(
                    messages=messages,
                    model=provider["model"],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
            except RateLimitError:
                logger.warning(
                    "Rate limited on %s (pre-stream, sweep %d/%d), trying next provider",
                    provider["name"], attempt + 1, _MAX_RETRIES + 1,
                )
                continue
            except APIStatusError as e:
                if e.status_code >= 500:
                    _mark_provider_down(provider["name"])
                    continue
                raise
            except APIConnectionError:
                _mark_provider_down(provider["name"])
                continue
            except Exception:
                raise

            # ── Mid-stream: iterate chunks ─────────────────────────────────────
            # Once chunks start flowing we cannot transparently retry on a
            # different provider — partial output was already sent to the client.
            # Any interruption here raises RuntimeError so the service layer
            # saves the partial content and signals event:partial to the client.
            try:
                for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
                return  # stream completed successfully
            except RateLimitError:
                logger.error(
                    "Rate limited mid-stream on %s — partial output already sent",
                    provider["name"],
                )
                raise RuntimeError("Rate limit hit mid-stream.")
            except Exception as e:
                logger.exception(
                    "Stream interrupted on %s after partial output (%s)",
                    provider["name"], type(e).__name__,
                )
                raise RuntimeError(f"Stream interrupted ({type(e).__name__}).")

        # All n providers unavailable this sweep — wait and retry.
        # Yield None every 15 s so the SSE layer can emit keepalive comments
        # and prevent proxy/browser idle-timeout from dropping the connection.
        if attempt < _MAX_RETRIES:
            wait = _RATE_LIMIT_RETRY_WAIT * (attempt + 1)
            logger.warning(
                "All %d providers unavailable (stream, sweep %d/%d) — waiting %.1fs",
                n, attempt + 1, _MAX_RETRIES + 1, wait,
            )
            elapsed = 0.0
            while elapsed < wait:
                interval = min(15.0, wait - elapsed)
                _sleep(interval)
                elapsed += interval
                if elapsed < wait:
                    yield None  # keepalive signal — SSE layer emits ": keepalive\n\n"

    raise RuntimeError(
        f"All {n} AI providers exhausted after {_MAX_RETRIES + 1} full sweeps (streaming)."
    )


# ── Per-clause analysis ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a strict legal contract compliance auditor. You receive a REQUIRED CLAUSE and a FULL CONTRACT DOCUMENT. Your job is to:
1. Scan the full document and locate the section(s) relevant to the clause topic.
2. Compare the required clause against what the document actually says in that section.
3. Determine whether the document satisfies, violates, or is missing that clause.

You are not checking if a topic merely exists. You are verifying whether the document's specific terms — amounts, dates, jurisdictions, named acts, restrictions, percentages, locations — exactly match or conflict with the required clause.

You must respond ONLY with valid JSON, no extra text or markdown formatting."""

USER_PROMPT_TEMPLATE = """REQUIRED CLAUSE (this is what the contract SHOULD contain):
TITLE: {title}
CONTENT: {content}

FULL CONTRACT DOCUMENT:
{pdf_text}

Your task: Analyze the FULL CONTRACT DOCUMENT against the REQUIRED CLAUSE using the following strict methodology:

STEP 1 — Scan the full document and locate the section(s) relevant to "{title}".
  - Copy the verbatim sentence(s) or paragraph(s) you find into the "relevant_text" field.
  - If nothing relevant exists anywhere in the document → result is NOT_FOUND, relevant_text is null.

STEP 2 — If relevant text is found, compare every specific value in the required clause against it:
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
    "relevant_text": "the verbatim sentence(s) or paragraph(s) you located in the document for this clause topic — copied exactly as they appear including any numbering prefix (e.g. '3.1 Fees:') — or null if not found",
    "ai_recommendation": "If NOT_FOUND: write the missing clause as it should appear. If VIOLATION or PARTIALLY_SATISFIED: write a corrective/improved clause — look at relevant_text and find the numbering prefix: (a) if relevant_text starts directly with a number (e.g. '2. Monthly Rent' or '3.1 Fees'), begin ai_recommendation with that exact number prefix; (b) if relevant_text starts with a bullet (•, *, -) followed by a number (e.g. '• 2. Monthly Rent'), skip the bullet and begin ai_recommendation with the number prefix only (e.g. '2. Monthly Rent ...'); (c) if relevant_text has no numbering at all, do NOT add any number prefix. If MATCH: null",
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
    Analyze clause compliance in a single AI call.

    The full document text is passed directly — the AI extracts the relevant
    text and performs compliance analysis in one shot, returning both
    relevant_text and the compliance verdict in a single JSON response.

    Uses the shared multi-provider pool (round-robin, rate-limit aware).

    Returns dict with: result, reason, relevant_text, ai_recommendation,
                       parties_obligated, missing_values, binding_strength, key_dates_durations
    """
    title   = clause["title"]
    content = clause["value"]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": USER_PROMPT_TEMPLATE.format(
            title=title, content=content, pdf_text=pdf_text,
        )},
    ]

    try:
        response_text = _call_ai(messages, max_tokens=1500, temperature=0.1)
        result = _parse_response(response_text)
        result["_provider"] = "pool"
        return result
    except json.JSONDecodeError:
        logger.exception("Failed to parse AI JSON response for clause '%s'", title)
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
    except Exception as e:  # G1 — bind exception so type(e).__name__ resolves correctly
        logger.exception("AI call failed for clause '%s'", title)
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


# ── Document-level analysis ────────────────────────────────────────────────────

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
    messages = [
        {"role": "system", "content": _JURISDICTION_SYSTEM},
        {"role": "user",   "content": _JURISDICTION_USER.format(pdf_excerpt=pdf_text[:3000])},
    ]

    try:
        response_text = _call_ai(messages, max_tokens=1000, temperature=0.1)
        result = _safe_json_parse(response_text)
        if not isinstance(result.get("checklist"), list):
            result["checklist"] = []
        if not isinstance(result.get("applicable_laws"), list):
            result["applicable_laws"] = []
        return result
    except Exception:
        logger.exception("detect_jurisdiction failed")
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

    messages = [
        {"role": "system", "content": _CONFLICT_SYSTEM},
        {"role": "user",   "content": _CONFLICT_USER.format(clause_summary_text=clause_summary_text)},
    ]

    try:
        response_text = _call_ai(messages, max_tokens=800, temperature=0.1)
        result = _safe_json_parse(response_text)
        conflicts = result.get("conflicts", [])
        return conflicts if isinstance(conflicts, list) else []
    except Exception:
        logger.exception("detect_conflicts failed")
        return []


# ── Q&A streaming ──────────────────────────────────────────────────────────────

_QA_STREAM_SYSTEM = """You are a legal document expert answering questions about documents.
Answer the user's question based strictly on the provided document excerpt.
Format your response using clear Markdown:
- Use **bold** for key terms, clause names, dates, amounts, and party names
- Use bullet points or numbered lists for multiple conditions, steps, or items
- Use > blockquote for direct quotes from the document
- Be specific — cite exact clause text, dates, amounts, or party names where relevant
- Keep the answer concise and focused on what was asked

If the answer is not found in the document excerpt, respond with:
> The document does not contain information about this topic."""

_REGEN_STREAM_SYSTEM = """You are a legal document expert. A user was not satisfied with a previous answer and has asked for a better one.
Use the user's feedback to guide an improved response.
Format your response using clear Markdown:
- Use **bold** for key terms, clause names, dates, amounts, and party names
- Use bullet points or numbered lists for multiple conditions, steps, or items
- Use > blockquote for direct quotes from the document
- Directly address the user's reason for dissatisfaction
- Be specific — cite exact clause text, dates, amounts, or parties where relevant

If the document excerpt does not contain the needed information, clearly state that."""


_EXCERPT_SYSTEM = """You are a document excerpt extractor.
Given a document passage and a question, return ONLY the single most relevant sentence or paragraph verbatim from the passage that best answers or relates to the question.
- Do not paraphrase, summarize, or add any explanation.
- Return ONLY the exact text from the passage.
- If nothing is relevant, return an empty string."""


def extract_relevant_excerpt(question: str, context: str) -> str:
    """
    Use AI to extract the single most relevant verbatim passage from context for the question.
    Falls back to empty string on any failure — non-critical path.
    """
    if not context:
        return ""
    messages = [
        {"role": "system", "content": _EXCERPT_SYSTEM},
        {"role": "user", "content": f"Document passage:\n{context}\n\nQuestion: {question}"},
    ]
    try:
        return _call_ai(messages, max_tokens=300, temperature=0.0)
    except Exception:
        logger.warning("extract_relevant_excerpt failed — returning empty string")
        return ""


def answer_question_stream(question: str, context: str, history: list | None = None):
    """
    Generator yielding markdown-formatted answer chunks for a Q&A question.

    history: list of prior {role, content} turns (user + assistant alternating),
             oldest first. Injected between the system prompt and the current
             question so the LLM has multi-turn conversation context.
    """
    messages = [{"role": "system", "content": _QA_STREAM_SYSTEM}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": f"Document excerpt:\n{context}\n\nQuestion: {question}"})
    yield from _call_ai_stream(messages, max_tokens=1000, temperature=0.1)


def regenerate_answer_stream(
    question: str,
    previous_answer: str,
    reason: str,
    context: str,
):
    """
    Generator yielding markdown-formatted chunks for a regenerated answer.
    Used by QARegenerateView for SSE streaming.
    """
    user = (
        f"Document excerpt:\n{context}\n\n"
        f"Original question: {question}\n\n"
        f"Previous answer (the user was NOT satisfied with this):\n{previous_answer}\n\n"
        f"User's reason for requesting regeneration: {reason}\n\n"
        "Please provide an improved answer that directly addresses the user's concern."
    )
    messages = [
        {"role": "system", "content": _REGEN_STREAM_SYSTEM},
        {"role": "user",   "content": user},
    ]
    yield from _call_ai_stream(messages, max_tokens=1000, temperature=0.2)

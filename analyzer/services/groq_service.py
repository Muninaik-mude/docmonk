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


SYSTEM_PROMPT = """You are a legal contract compliance analyzer. Analyze whether a given clause is present, compliant, violated, or missing in the provided document text.

You must respond ONLY with valid JSON, no extra text or markdown formatting."""

USER_PROMPT_TEMPLATE = """CLAUSE TITLE: {title}
CLAUSE CONTENT: {content}

DOCUMENT TEXT:
{pdf_text}

Analyze the clause against the document and respond in this EXACT JSON format:
{{
    "result": "MATCH" or "NOT_FOUND" or "VIOLATION",
    "reason": "brief explanation",
    "relevant_text": "the part of document that relates to this clause, or null",
    "ai_recommendation": "If NOT_FOUND: write the missing clause as it should appear. If VIOLATION: write a corrective clause. If MATCH: null"
}}

Rules:
- MATCH: clause content is present and compliant in the document
- NOT_FOUND: clause topic is completely absent from the document
- VIOLATION: clause topic exists but contradicts or violates the clause requirement
"""


def _parse_response(response_text: str) -> dict:
    """Parse and validate the AI JSON response."""
    if response_text.startswith("```"):
        lines = [l for l in response_text.split("\n") if not l.strip().startswith("```")]
        response_text = "\n".join(lines)

    result = json.loads(response_text)

    if result.get("result") not in ("MATCH", "NOT_FOUND", "VIOLATION"):
        logger.warning("Invalid result value: %s, defaulting to NOT_FOUND", result.get("result"))
        result["result"] = "NOT_FOUND"

    return result


def _call_groq(user_message: str) -> str:
    """Call Groq AI and return the raw response text."""
    client = Groq(api_key=settings.GROQ_API_KEY)
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        model=settings.GROQ_MODEL,
        temperature=0.1,
        max_tokens=512,
    )
    return chat_completion.choices[0].message.content.strip()


def analyze_clause_against_pdf(clause: dict, pdf_text: str) -> dict:
    """
    Analyze clause compliance using Groq AI.
    Returns dict with: result, reason, relevant_text, ai_recommendation
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
            "_provider": "groq",
        }
    except Exception as e:
        logger.error("Groq failed for clause '%s': %s", title, e)
        raise


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

import json
import logging

from groq import Groq
from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)

SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1"

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
        max_tokens=1024,
    )
    return chat_completion.choices[0].message.content.strip()


def _call_sambanova(user_message: str) -> str:
    """Call SambaNova AI and return the raw response text."""
    client = OpenAI(
        api_key=settings.SAMBANOVA_API_KEY,
        base_url=SAMBANOVA_BASE_URL,
    )
    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        model=settings.SAMBANOVA_MODEL,
        temperature=0.1,
        max_tokens=1024,
    )
    return chat_completion.choices[0].message.content.strip()


def analyze_clause_against_pdf(clause: dict, pdf_text: str) -> dict:
    """
    Analyze clause compliance using Groq AI (primary).
    Falls back to SambaNova AI if Groq fails for any reason.
    Returns dict with: result, reason, relevant_text, ai_recommendation
    """
    user_message = USER_PROMPT_TEMPLATE.format(
        title=clause["title"],
        content=clause.get("content") or clause.get("value", ""),
        pdf_text=pdf_text,
    )

    # Primary: Groq
    try:
        response_text = _call_groq(user_message)
        logger.info("Groq responded for clause '%s'", clause["title"])
        return _parse_response(response_text)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Groq JSON response: %s", e)
        return {
            "result": "NOT_FOUND",
            "reason": "AI response could not be parsed",
            "relevant_text": None,
            "ai_recommendation": f"Clause '{clause['title']}' could not be analyzed. Manual review recommended.",
        }
    except Exception as e:
        logger.warning("Groq failed for clause '%s': %s — falling back to SambaNova", clause["title"], e)

    # Fallback: SambaNova
    try:
        response_text = _call_sambanova(user_message)
        logger.info("SambaNova (fallback) responded for clause '%s'", clause["title"])
        return _parse_response(response_text)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse SambaNova JSON response: %s", e)
        return {
            "result": "NOT_FOUND",
            "reason": "AI response could not be parsed",
            "relevant_text": None,
            "ai_recommendation": f"Clause '{clause['title']}' could not be analyzed. Manual review recommended.",
        }
    except Exception as e:
        logger.error("SambaNova also failed for clause '%s': %s", clause["title"], e)
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

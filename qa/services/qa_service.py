"""
Q&A service — finds relevant context windows and answers questions per document.

Called from QAAskView inside ThreadPoolExecutor threads.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import close_old_connections

from analyzer.services import groq_service

logger = logging.getLogger(__name__)

_MAX_PARALLEL = 3

_STOP_WORDS = frozenset([
    "the", "and", "for", "are", "was", "this", "that", "with", "from",
    "have", "has", "had", "not", "but", "what", "all", "were", "they",
    "will", "one", "can", "her", "his", "him", "its", "our", "who",
    "did", "does", "how", "why", "when", "where", "which", "into",
    "any", "been", "than", "then", "also", "more", "some", "such",
    "there", "their", "about", "would", "could", "should",
])


# ── Context window ────────────────────────────────────────────────────────────

def _find_relevant_window(question: str, full_text: str, window_size: int = 5000) -> str:
    """
    Find the most relevant window in full_text for the given question.

    1. Extract keywords from the question (words > 3 chars, not stop words).
    2. Scan full_text in 200-char steps and count keyword hits per window.
    3. Return a window_size-char slice centred on the best position.
    Fallback: first window_size chars if no keywords match.
    """
    if not full_text:
        return ""

    keywords = [
        w.lower().strip(".,?!\"'();:")
        for w in question.split()
        if len(w) > 3 and w.lower() not in _STOP_WORDS
    ]

    if not keywords:
        return full_text[:window_size]

    text_lower = full_text.lower()
    text_len   = len(full_text)
    step       = 200

    best_pos   = 0
    best_score = 0

    pos = 0
    while pos < text_len:
        window = text_lower[pos: pos + window_size]
        score  = sum(1 for kw in keywords if kw in window)
        if score > best_score:
            best_score = score
            best_pos   = pos
        pos += step

    if best_score == 0:
        return full_text[:window_size]

    # Centre the window on the best position
    centre = best_pos + window_size // 2
    start  = max(0, centre - window_size // 2)
    end    = min(text_len, start + window_size)
    return full_text[start:end]


# ── Page hint lookup ──────────────────────────────────────────────────────────

def find_page_for_excerpt(excerpt: str, full_text: str, char_page_map: list) -> int | None:
    """
    Locate the first 80 chars of excerpt in full_text, then return its 1-based
    page number using char_page_map [{page, start, end}].
    Returns None if the map is empty or the excerpt is not found.
    """
    if not excerpt or not char_page_map:
        return None
    needle = excerpt[:80]
    pos    = full_text.find(needle)
    if pos == -1:
        return None
    for entry in char_page_map:
        if entry["start"] <= pos <= entry["end"]:
            return entry["page"]
    return None


# ── Per-document worker ───────────────────────────────────────────────────────

def answer_for_document(question: str, doc_data: dict) -> dict:
    """
    Thread worker: answer one question from one document.

    doc_data keys:
      full_text, document_id, document_filename, position, char_page_map

    Returns:
      {document_id, document_filename, position, answer, relevant_excerpt, page_hint, status}
      status: "success" | "failed"  — used by retry to identify failed answers.
    """
    close_old_connections()

    document_id       = doc_data["document_id"]
    document_filename = doc_data["document_filename"]
    position          = doc_data["position"]
    full_text         = doc_data["full_text"]
    char_page_map     = doc_data.get("char_page_map", [])

    try:
        context   = _find_relevant_window(question, full_text)
        ai_result = groq_service.answer_question_in_document(question, context)

        relevant_excerpt = ai_result.get("relevant_excerpt", "") or ""
        page_hint        = find_page_for_excerpt(relevant_excerpt, full_text, char_page_map)

        return {
            "document_id":       document_id,
            "document_filename": document_filename,
            "position":          position,
            "answer":            ai_result.get("answer", ""),
            "relevant_excerpt":  relevant_excerpt,
            "page_hint":         page_hint,
            "status":            "success",
        }

    except Exception as e:
        logger.error(
            "Q&A failed for doc '%s', question '%s': %s",
            document_id, question[:60], e,
        )
        return {
            "document_id":       document_id,
            "document_filename": document_filename,
            "position":          position,
            "answer":            f"Analysis failed: {type(e).__name__}. Manual review recommended.",
            "relevant_excerpt":  "",
            "page_hint":         None,
            "status":            "failed",
        }


# ── Orchestrator ──────────────────────────────────────────────────────────────

def answer_questions(questions: list[str], doc_data_list: list[dict]) -> list[dict]:
    """
    Answer N questions across M documents in parallel.

    Returns a list (one entry per question) with shape:
      [{question, answers_per_document: [{...}, ...]}, ...]

    All (question × document) pairs run concurrently in ThreadPoolExecutor.
    """
    # Build flat list of (q_idx, d_idx, question, doc_data)
    pairs = [
        (q_idx, d_idx, question, doc_data)
        for q_idx, question in enumerate(questions)
        for d_idx, doc_data in enumerate(doc_data_list)
    ]

    result_map: dict[tuple, dict] = {}

    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as executor:
        future_to_key = {
            executor.submit(answer_for_document, question, doc_data): (q_idx, d_idx)
            for q_idx, d_idx, question, doc_data in pairs
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                result_map[key] = future.result()
            except Exception as e:
                q_idx, d_idx = key
                doc_data = doc_data_list[d_idx]
                logger.error("Unexpected thread error for (q=%d, d=%d): %s", q_idx, d_idx, e)
                result_map[key] = {
                    "document_id":       doc_data["document_id"],
                    "document_filename": doc_data["document_filename"],
                    "position":          doc_data["position"],
                    "answer":            f"Unexpected error: {type(e).__name__}",
                    "relevant_excerpt":  "",
                    "page_hint":         None,
                    "status":            "failed",
                }

    # Reconstruct ordered output
    answers = []
    for q_idx, question in enumerate(questions):
        per_doc = [
            result_map[(q_idx, d_idx)]
            for d_idx in range(len(doc_data_list))
            if (q_idx, d_idx) in result_map
        ]
        per_doc.sort(key=lambda x: x["position"])
        answers.append({"question": question, "answers_per_document": per_doc})

    return answers

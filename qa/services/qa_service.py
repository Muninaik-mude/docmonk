"""
Q&A service — context window selection, page hint lookup, and streaming answer generators.

Stream generators yield raw markdown text chunks.
The view layer wraps chunks in SSE format via _stream_to_sse() in views.py.
DB persistence happens inside each generator after all chunks are yielded.

Three outcomes are possible after a stream:

  success  — all chunks received, full answer saved → view emits event:done
  partial  — stream interrupted mid-way (network, rate-limit, etc.), partial
             content saved → PartialAnswerError raised → view emits event:partial
             The client shows the partial answer with a "regenerate" prompt,
             exactly like Claude.ai / ChatGPT when generation is cut short.
  error    — DB save failed after streaming → exception propagates → view emits
             event:error (message was NOT saved, client should retry)
"""
import logging

from django.db import transaction

from analyzer.services import groq_service

from ..models import QAMessage, QASession


class PartialAnswerError(Exception):
    """
    Raised when an AI stream was interrupted mid-response but the partial
    content was successfully saved to the database.

    The view layer catches this and emits 'event: partial' (not 'event: done'),
    allowing the frontend to display the partial answer with a regenerate prompt —
    the same pattern used by Claude.ai and ChatGPT when generation is cut short.
    """
    pass

logger = logging.getLogger(__name__)


# ── Gevent CPU offload ─────────────────────────────────────────────────────────
# find_relevant_window is a CPU-bound scan over potentially large documents.
# In gevent workers it blocks the event loop for its entire duration, starving
# all other concurrent greenlets. Offloading to gevent's OS threadpool lets
# other greenlets run while the scan executes in a real OS thread.
try:
    from gevent import get_hub as _gevent_get_hub
    def _offload_cpu(fn, *args):
        return _gevent_get_hub().threadpool.spawn(fn, *args).get()
except ImportError:
    # Dev / test environment without gevent — run synchronously
    def _offload_cpu(fn, *args):
        return fn(*args)


_STOP_WORDS = frozenset([
    "the", "and", "for", "are", "was", "this", "that", "with", "from",
    "have", "has", "had", "not", "but", "what", "all", "were", "they",
    "will", "one", "can", "her", "his", "him", "its", "our", "who",
    "did", "does", "how", "why", "when", "where", "which", "into",
    "any", "been", "than", "then", "also", "more", "some", "such",
    "there", "their", "about", "would", "could", "should",
])


# ── Context window ─────────────────────────────────────────────────────────────

def find_relevant_window(question: str, full_text: str, window_size: int = 5000) -> str:
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

    centre = best_pos + window_size // 2
    start  = max(0, centre - window_size // 2)
    end    = min(text_len, start + window_size)
    return full_text[start:end]


# ── Page hint lookup ───────────────────────────────────────────────────────────

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


# ── Stream generators ──────────────────────────────────────────────────────────

def ask_stream(session, question: str, doc_data: dict):
    """
    Generator yielding raw markdown text chunks for a Q&A ask.

    Yields None during provider wait periods — the SSE layer converts these
    to keepalive comments so proxies don't drop the idle connection.

    Outcome after the generator is exhausted:
      - success  → full answer saved, caller emits event:done
      - partial  → stream interrupted, partial answer saved, PartialAnswerError raised
      - (DB fail)→ raises, caller emits event:error (message not saved)
    """
    context       = _offload_cpu(find_relevant_window, question, doc_data["full_text"])
    chunks:  list = []
    partial: bool = False

    # G3 — fetch last 10 exchanges as conversation history (oldest first).
    # Gives the LLM context for follow-up questions that reference prior turns.
    history_rows = list(
        QAMessage.objects.filter(session=session).order_by("-created_at")[:10]
    )
    history_rows.reverse()
    history = []
    for msg in history_rows:
        history.append({"role": "user", "content": msg.question})
        first_ans = msg.answers_per_document[0] if msg.answers_per_document else {}
        ans_text  = first_ans.get("answer", "")
        if ans_text:
            history.append({"role": "assistant", "content": ans_text})

    try:
        for chunk in groq_service.answer_question_stream(question, context, history=history):
            if chunk is None:
                yield None  # keepalive — pass through to SSE layer
                continue
            chunks.append(chunk)
            yield chunk
    except RuntimeError:
        # Stream interrupted — _call_ai_stream raises RuntimeError for all
        # recoverable stream failures. Fatal errors (MemoryError, etc.) are
        # NOT caught here so they can propagate and crash the worker cleanly.
        logger.exception("Stream interrupted for question '%s'", question[:60])
        partial = True

    answer_status    = "partial" if partial else "success"
    # G4 — resolve the page the answer came from using the context window position
    relevant_excerpt = context[:200] if context else ""
    page_hint        = find_page_for_excerpt(context, doc_data["full_text"], doc_data["char_page_map"])

    # DB save — raises on failure so the view layer emits event:error instead of event:done/partial
    with transaction.atomic():
        message = QAMessage.objects.create(
            session              = session,
            question             = question,
            answers_per_document = [{
                "document_id":       doc_data["document_id"],
                "document_filename": doc_data["document_filename"],
                "position":          doc_data["position"],
                "answer":            "".join(chunks),
                "relevant_excerpt":  relevant_excerpt,
                "page_hint":         page_hint,
                "status":            answer_status,
            }],
        )

        # Auto-name session from the first question asked.
        # filter+update is a single atomic SQL UPDATE — avoids the read-check-write
        # race that would occur with session.save(update_fields=[...]).
        QASession.objects.filter(id=session.id, name="").update(name=question[:100])

    if partial:
        raise PartialAnswerError()


def retry_stream(message, existing_answers: list, doc_data_list: list):
    """
    Generator yielding raw markdown text chunks for a retry.

    Re-runs answers with status in ("failed", "partial"). Merges new answers
    with existing successful ones and saves on completion.
    Raises PartialAnswerError if any answer was cut short.
    Raises on DB failure.
    """
    new_by_position: dict = {}
    any_partial = False

    for doc_data in doc_data_list:
        context       = _offload_cpu(find_relevant_window, message.question, doc_data["full_text"])
        chunks:  list = []
        partial: bool = False

        try:
            for chunk in groq_service.answer_question_stream(message.question, context):
                if chunk is None:
                    yield None  # keepalive — pass through to SSE layer
                    continue
                chunks.append(chunk)
                yield chunk
        except RuntimeError:
            logger.exception("Retry stream interrupted for message %s", message.id)
            partial     = True
            any_partial = True

        # G4 — resolve page hint from context window position
        relevant_excerpt = context[:200] if context else ""
        page_hint        = find_page_for_excerpt(context, doc_data["full_text"], doc_data["char_page_map"])

        new_by_position[doc_data["position"]] = {
            "document_id":       doc_data["document_id"],
            "document_filename": doc_data["document_filename"],
            "position":          doc_data["position"],
            "answer":            "".join(chunks),
            "relevant_excerpt":  relevant_excerpt,
            "page_hint":         page_hint,
            "status":            "partial" if partial else "success",
        }

    # Merge: replace retried answers with new results, keep untouched successful ones
    updated_answers = [
        new_by_position.get(ans["position"], ans)
        if ans.get("status") in ("failed", "partial")
        else ans
        for ans in existing_answers
    ]

    # DB save — raises on failure
    message.answers_per_document = updated_answers
    message.save(update_fields=["answers_per_document", "updated_at"])

    if any_partial:
        raise PartialAnswerError()


def regenerate_stream(message, existing_answers: list, reason: str, doc, previous_answer: str):
    """
    Generator yielding raw markdown text chunks for a regenerated answer.

    Replaces the first answer in-place and saves on completion.
    Raises PartialAnswerError if the stream was cut short.
    Raises on DB failure.
    """
    context       = _offload_cpu(find_relevant_window, message.question, doc.full_text)
    chunks: list  = []
    partial: bool = False

    try:
        for chunk in groq_service.regenerate_answer_stream(
            message.question, previous_answer, reason, context
        ):
            if chunk is None:
                yield None  # keepalive — pass through to SSE layer
                continue
            chunks.append(chunk)
            yield chunk
    except RuntimeError:
        logger.exception("Regenerate stream interrupted for message %s", message.id)
        partial = True

    existing_position = existing_answers[0].get("position", 0) if existing_answers else 0
    # G4 — resolve page hint from context window position
    relevant_excerpt  = context[:200] if context else ""
    page_hint         = find_page_for_excerpt(context, doc.full_text, doc.char_page_map)
    regenerated = {
        "document_id":         doc.document_id,
        "document_filename":   doc.document_filename,
        "position":            existing_position,
        "answer":              "".join(chunks),
        "relevant_excerpt":    relevant_excerpt,
        "page_hint":           page_hint,
        "status":              "partial" if partial else "success",
        "regenerated":         True,
        "regeneration_reason": reason,
    }

    # DB save — raises on failure
    message.answers_per_document = [regenerated]
    message.save(update_fields=["answers_per_document", "updated_at"])

    if partial:
        raise PartialAnswerError()

"""
Q&A API views.

POST   /v1/qa/sessions               — create session, extract/cache document text
GET    /v1/qa/sessions?user_id=xxx   — list sessions for a user (paginated)
GET    /v1/qa/sessions/{id}          — session metadata + paginated message history
PATCH  /v1/qa/sessions/{id}          — rename session
DELETE /v1/qa/sessions/{id}          — delete session and all messages
POST   /v1/qa/sessions/{id}/ask      — ask a question, stream SSE answer
POST   /v1/qa/messages/{id}/retry    — retry failed answer (SSE)
POST   /v1/qa/messages/{id}/regenerate — regenerate answer with user reason (SSE)
"""
import json
import logging
import math
from typing import Any

from django.db import transaction, IntegrityError
from django.db.models import Count, Prefetch
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from analyzer.services import pdf_service, r2_service
from analyzer.serializers import MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB

from .models import QADocument, QASession, QASessionDocument, QAMessage
from .serializers import (
    QASessionCreateSerializer,
    QAAskSerializer,
    QARenameSessionSerializer,
    QARegenerateSerializer,
    MAX_QUESTIONS,
)
from .services import qa_service

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 10


# ── Response helpers ───────────────────────────────────────────────────────────

def _ok(message: str, data: Any, http_status: int = status.HTTP_200_OK) -> Response:
    return Response(
        {"status": http_status, "success": True, "message": message, "data": data},
        status=http_status,
    )


def _err(message: str, http_status: int, data: Any = None) -> Response:
    return Response(
        {"status": http_status, "success": False, "message": message, "data": data},
        status=http_status,
    )


def _pagination_info(total_records: int, page: int, page_size: int) -> dict:
    total_pages = math.ceil(total_records / page_size) if total_records > 0 else 1
    return {
        "total_records": total_records,
        "total_pages":   total_pages,
        "page_size":     page_size,
        "current_page":  page,
        "next_page":     page + 1 if page < total_pages else None,
        "prev_page":     page - 1 if page > 1 else None,
    }


def _parse_page_params(request) -> tuple[int, int]:
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int(request.query_params.get("page_size", _DEFAULT_PAGE_SIZE))))
    except (TypeError, ValueError):
        page_size = _DEFAULT_PAGE_SIZE
    return page, page_size


# ── SSE helpers ────────────────────────────────────────────────────────────────

def _sse_chunk(text: str) -> str:
    """Encode a text chunk as an SSE data event."""
    return f"data: {json.dumps(text, ensure_ascii=False)}\n\n"


_SSE_ERROR = "event: error\ndata: {\"success\":false,\"message\":\"Answer could not be saved. Please try again.\"}\n\n"


def _sse_response(generator) -> StreamingHttpResponse:
    """Wrap a generator in a StreamingHttpResponse with SSE headers."""
    response = StreamingHttpResponse(generator, content_type="text/event-stream")
    response["Cache-Control"]     = "no-cache"
    response["X-Accel-Buffering"] = "no"
    response["Connection"]        = "keep-alive"
    return response


def _stream_to_sse(service_gen):
    """
    Wrap a qa_service stream generator in SSE format.

    - None chunks from the service layer → ": keepalive\\n\\n" SSE comment.
      Emitted every 15 s during provider waits to prevent proxy idle-timeout.
    - Text chunks → data: "<json>\\n\\n"
    - On clean completion → event: done
    - On PartialAnswerError → event: partial
    - On any other exception → event: error (message not saved)

    message_id is intentionally excluded from all terminal events.
    The client uses GET /sessions/{id} to retrieve the saved message and its id.
    """
    try:
        for chunk in service_gen:
            if chunk is None:
                yield ": keepalive\n\n"
            else:
                yield _sse_chunk(chunk)
        yield "event: done\ndata: {\"success\":true}\n\n"
    except qa_service.PartialAnswerError:
        yield "event: partial\ndata: {\"success\":true,\"status\":\"partial\",\"message\":\"The answer was cut short due to a network or provider issue. You can regenerate for a complete response.\"}\n\n"
    except Exception:
        logger.exception("Stream failed after chunks were sent to client")
        yield _SSE_ERROR


# ── Session views ──────────────────────────────────────────────────────────────

class QASessionView(APIView):
    """
    GET  /v1/qa/sessions?user_id=xxx  — list all sessions for a user (newest first, paginated)
    POST /v1/qa/sessions              — create a new session
    """

    def get(self, request):
        user_id = request.query_params.get("user_id", "").strip()
        if not user_id:
            return _err("'user_id' query parameter is required.", status.HTTP_400_BAD_REQUEST)
        if len(user_id) > 500:
            return _err("'user_id' must not exceed 500 characters.", status.HTTP_400_BAD_REQUEST)

        page, page_size = _parse_page_params(request)
        offset          = (page - 1) * page_size

        total_records = QASession.objects.filter(user_id=user_id).count()

        # Defer full_text and char_page_map — not needed for the list view.
        _doc_qs = QASessionDocument.objects.select_related("document").defer(
            "document__full_text",
            "document__char_page_map",
        )
        sessions_qs = list(
            QASession.objects
            .filter(user_id=user_id)
            .prefetch_related(Prefetch("session_documents", queryset=_doc_qs))
            .order_by("-created_at")
            [offset: offset + page_size]
        )

        records = []
        for s in sessions_qs:
            docs = [
                {
                    "document_id":       sd.document.document_id,
                    "document_filename": sd.document.document_filename,
                    "file_type":         sd.document.file_type,
                }
                for sd in s.session_documents.all()
            ]
            records.append({
                "session_id":  str(s.id),
                "name":        s.name,
                "user_id":     s.user_id,
                "documents":   docs,
                "created_at":  s.created_at.isoformat(),
                "updated_at":  s.updated_at.isoformat(),
            })

        return _ok(
            "Sessions fetched successfully",
            {
                "user_id":         user_id,
                "pagination_info": _pagination_info(total_records, page, page_size),
                "records":         records,
            },
        )

    def post(self, request):
        serializer = QASessionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _err("Validation failed.", status.HTTP_400_BAD_REQUEST, data=serializer.errors)

        validated  = serializer.validated_data
        user_id    = validated["user_id"]
        doc_inputs = validated["documents"]

        # ── Download + extract outside atomic block (network I/O) ─────────────
        resolved_docs: list[dict[str, Any]] = []

        for doc_input in doc_inputs:
            document_id       = doc_input["document_id"]
            s3_url            = doc_input["s3_download_url"]
            document_filename = doc_input.get("document_filename", "document")

            # Fast cache check — skip download if already extracted
            try:
                cached = QADocument.objects.get(document_id=document_id)
                logger.info("Cache hit for document_id=%s", document_id)
                resolved_docs.append({"cached": cached, "document_id": document_id})
                continue
            except QADocument.DoesNotExist:
                pass

            # Download
            try:
                doc_bytes = r2_service.download_document_from_presigned_url(s3_url)
            except Exception as e:
                logger.exception("Failed to download document_id=%s", document_id)
                return _err(
                    f"Failed to download document '{document_filename}': {e}",
                    status.HTTP_400_BAD_REQUEST,
                )

            if len(doc_bytes) > MAX_FILE_SIZE_BYTES:
                file_size_mb = len(doc_bytes) / (1024 * 1024)
                return _err(
                    f"Document '{document_filename}' is {file_size_mb:.1f} MB, "
                    f"exceeds {MAX_FILE_SIZE_MB} MB limit.",
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

            type_hint = s3_url or document_filename
            try:
                file_type   = pdf_service.detect_file_type(type_hint, doc_bytes)
                text_blocks = pdf_service.extract_text_blocks(type_hint, doc_bytes)
                full_text   = pdf_service.get_full_text(text_blocks)
            except Exception as e:
                logger.exception(
                    "Text extraction failed for document_id=%s filename=%s",
                    document_id, document_filename,
                )
                return _err(
                    f"Failed to extract text from '{document_filename}': {type(e).__name__}. "
                    "The file may be corrupt, password-protected, or an unsupported format.",
                    status.HTTP_400_BAD_REQUEST,
                )

            if not full_text.strip():
                return _err(
                    f"Document '{document_filename}' contains no extractable text.",
                    status.HTTP_400_BAD_REQUEST,
                )

            resolved_docs.append({
                "cached":            None,
                "document_id":       document_id,
                "s3_download_url":   s3_url,
                "document_filename": document_filename,
                "file_type":         file_type,
                "full_text":         full_text,
                "char_count":        len(full_text),
                "char_page_map":     pdf_service.build_char_page_map(text_blocks),
            })

        # ── All DB writes in one atomic block — auto-rollback on any failure ──
        session_docs: list[dict[str, Any]] = []

        with transaction.atomic():
            session = QASession.objects.create(user_id=user_id)

            for position, doc_meta in enumerate(resolved_docs):
                if doc_meta.get("cached"):
                    qa_doc = doc_meta["cached"]
                else:
                    # get_or_create handles race condition: two concurrent requests
                    # for the same document_id will not cause IntegrityError.
                    try:
                        qa_doc, created = QADocument.objects.get_or_create(
                            document_id = doc_meta["document_id"],
                            defaults    = {
                                "s3_download_url":   doc_meta["s3_download_url"],
                                "document_filename": doc_meta["document_filename"],
                                "file_type":         doc_meta["file_type"],
                                "full_text":         doc_meta["full_text"],
                                "char_count":        doc_meta["char_count"],
                                "char_page_map":     doc_meta["char_page_map"],
                            },
                        )
                    except IntegrityError:
                        logger.warning(
                            "IntegrityError on get_or_create for document_id=%s — fetching winner row",
                            doc_meta["document_id"],
                        )
                        qa_doc  = QADocument.objects.get(document_id=doc_meta["document_id"])
                        created = False
                    if created:
                        logger.info(
                            "Extracted and cached document_id=%s (%s, %d chars)",
                            qa_doc.document_id, qa_doc.file_type, qa_doc.char_count,
                        )

                QASessionDocument.objects.create(
                    session  = session,
                    document = qa_doc,
                    position = position,
                )

                session_docs.append({
                    "document_id":       qa_doc.document_id,
                    "document_filename": qa_doc.document_filename,
                    "file_type":         qa_doc.file_type,
                    "char_count":        qa_doc.char_count,
                })

        return _ok(
            "Session created successfully",
            {
                "session_id": str(session.id),
                "name":       session.name,
                "user_id":    user_id,
                "documents":  session_docs,
                "created_at": session.created_at.isoformat(),
            },
            http_status=status.HTTP_201_CREATED,
        )


class QASessionDetailView(APIView):
    """
    GET    /v1/qa/sessions/{session_id}  — session detail + paginated message history
    PATCH  /v1/qa/sessions/{session_id}  — rename session
    DELETE /v1/qa/sessions/{session_id}  — delete session + all messages
    """

    def _get_session_or_404(self, session_id, prefetch=None):
        qs = QASession.objects
        if prefetch:
            qs = qs.prefetch_related(*prefetch)
        try:
            return qs.get(id=session_id), None
        except QASession.DoesNotExist:
            return None, _err(f"Session '{session_id}' not found.", status.HTTP_404_NOT_FOUND)

    def get(self, request, session_id):
        page, page_size = _parse_page_params(request)

        # Defer full_text/char_page_map — detail view response never includes them.
        _doc_qs = QASessionDocument.objects.select_related("document").defer(
            "document__full_text",
            "document__char_page_map",
        )
        session, err = self._get_session_or_404(
            session_id,
            prefetch=[Prefetch("session_documents", queryset=_doc_qs)],
        )
        if err:
            return err

        documents = [
            {
                "document_id":       sd.document.document_id,
                "document_filename": sd.document.document_filename,
                "file_type":         sd.document.file_type,
                "char_count":        sd.document.char_count,
            }
            for sd in session.session_documents.all()
        ]

        # Each QAMessage produces 2 records (user + assistant).
        # page_size is in individual records, not Q&A pairs.
        #
        # record_offset — first record index for this page (0-based)
        # qa_offset     — which QAMessage that record falls in (record_offset // 2)
        # inner_offset  — 0 = page starts on the user record of that message
        #                 1 = page starts on the assistant record (odd record_offset)
        # qa_fetch_size — QAMessages needed to cover page_size records + inner_offset
        #
        # Example: page_size=3, page=2
        #   record_offset=3, qa_offset=1, inner_offset=1, qa_fetch_size=2
        #   Fetch messages[1:3] → expand to [Q1,A1,Q2,A2] → skip 1 → [A1,Q2,A2] ✓
        total_qa_messages = QAMessage.objects.filter(session=session).count()
        total_records     = total_qa_messages * 2
        record_offset     = (page - 1) * page_size
        qa_offset         = record_offset // 2
        inner_offset      = record_offset % 2
        qa_fetch_size     = math.ceil((page_size + inner_offset) / 2)

        messages_qs = (
            QAMessage.objects
            .filter(session=session)
            .order_by("created_at")
            [qa_offset: qa_offset + qa_fetch_size]
        )

        all_records = []
        for msg in messages_qs:
            first_answer = msg.answers_per_document[0] if msg.answers_per_document else {}

            all_records.append({
                "message_id": str(msg.question_message_id),
                "role":       "user",
                "content":    msg.question,
                "created_at": str(msg.created_at),
            })
            all_records.append({
                "message_id":       str(msg.id),
                "role":             "assistant",
                "content":          first_answer.get("answer", ""),
                "relevant_excerpt": first_answer.get("relevant_excerpt", ""),
                "page_hint":        first_answer.get("page_hint"),
                "created_at":       str(msg.created_at),
                "updated_at":       str(msg.updated_at),
            })

        records = all_records[inner_offset: inner_offset + page_size]

        return _ok(
            "Session fetched successfully",
            {
                "session_id":      str(session.id),
                "name":            session.name,
                "user_id":         session.user_id,
                "documents":       documents,
                "created_at":      session.created_at.isoformat(),
                "updated_at":      session.updated_at.isoformat(),
                "pagination_info": _pagination_info(total_records, page, page_size),
                "records":         records,
            },
        )

    def patch(self, request, session_id):
        """Rename session. Body: {"name": "New session name"}"""
        session, err = self._get_session_or_404(session_id)
        if err:
            return err

        serializer = QARenameSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return _err("Validation failed.", status.HTTP_400_BAD_REQUEST, data=serializer.errors)

        session.name = serializer.validated_data["name"]
        session.save(update_fields=["name", "updated_at"])

        return _ok(
            "Session renamed successfully",
            {
                "session_id": str(session.id),
                "name":       session.name,
                "updated_at": session.updated_at.isoformat(),
            },
        )

    def delete(self, request, session_id):
        """
        Delete session, all its messages, and any documents no longer
        referenced by any other session.

        Wrapped in transaction.atomic() so a crash between session.delete()
        and orphaned.delete() cannot leave unreferenced QADocument rows.
        """
        session, err = self._get_session_or_404(session_id)
        if err:
            return err

        with transaction.atomic():
            # Capture document IDs before the cascade removes QASessionDocument rows
            doc_ids = list(
                session.session_documents.values_list("document_id", flat=True)
            )

            # Cascade-deletes QAMessage and QASessionDocument rows
            session.delete()

            # Clean up extracted text for documents that are now unreferenced.
            if doc_ids:
                orphaned = QADocument.objects.filter(
                    id__in=doc_ids,
                    session_links__isnull=True,
                )
                deleted_count, _ = orphaned.delete()
                if deleted_count:
                    logger.info(
                        "Deleted %d orphaned QADocument(s) after session %s was deleted",
                        deleted_count, session_id,
                    )

        return _ok("Session deleted successfully", {"session_id": str(session_id)})


# ── Ask view ───────────────────────────────────────────────────────────────────

class QAAskView(APIView):
    """
    POST /v1/qa/sessions/{session_id}/ask

    Ask a single question. Streams the markdown answer via SSE.
    Saves the message to DB after streaming completes.
    """

    def post(self, request, session_id):
        try:
            session = (
                QASession.objects
                .prefetch_related("session_documents__document")
                .annotate(message_count=Count("messages"))
                .get(id=session_id)
            )
        except QASession.DoesNotExist:
            return _err(f"Session '{session_id}' not found.", status.HTTP_404_NOT_FOUND)

        serializer = QAAskSerializer(data=request.data)
        if not serializer.is_valid():
            return _err("Validation failed.", status.HTTP_400_BAD_REQUEST, data=serializer.errors)

        # Use the prefetch cache — do not re-query with select_related
        session_doc = next(iter(session.session_documents.all()), None)
        if not session_doc:
            return _err("Session has no documents.", status.HTTP_400_BAD_REQUEST)

        if session.message_count >= MAX_QUESTIONS:
            return _err(
                f"Session is limited to {MAX_QUESTIONS} questions.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )

        question = serializer.validated_data["question"]
        doc_data = {
            "document_id":       session_doc.document.document_id,
            "document_filename": session_doc.document.document_filename,
            "position":          session_doc.position,
            "full_text":         session_doc.document.full_text,
            "char_page_map":     session_doc.document.char_page_map,
        }

        return _sse_response(
            _stream_to_sse(
                qa_service.ask_stream(session, question, doc_data),
            )
        )


# ── Retry view ─────────────────────────────────────────────────────────────────

class QAMessageRetryView(APIView):
    """
    POST /v1/qa/messages/{message_id}/retry

    Re-runs AI for failed answers in a message (SSE streaming).
    Only failed answers are retried; successful ones are preserved.
    """

    def post(self, request, message_id):
        try:
            message = QAMessage.objects.select_related("session").get(id=message_id)
        except QAMessage.DoesNotExist:
            return _err(f"Message '{message_id}' not found.", status.HTTP_404_NOT_FOUND)

        existing_answers    = message.answers_per_document
        retryable_positions = [
            ans["position"]
            for ans in existing_answers
            if ans.get("status") in ("failed", "partial")
        ]

        if not retryable_positions:
            return _ok(
                "No failed or partial answers found. Nothing to retry.",
                {"status": "no_failures"},
            )

        failed_session_docs = (
            message.session.session_documents
            .filter(position__in=retryable_positions)
            .select_related("document")
        )

        doc_data_list = [
            {
                "document_id":       sd.document.document_id,
                "document_filename": sd.document.document_filename,
                "position":          sd.position,
                "full_text":         sd.document.full_text,
                "char_page_map":     sd.document.char_page_map,
            }
            for sd in failed_session_docs
        ]

        if not doc_data_list:
            return _err("Could not find documents for failed answers.", status.HTTP_400_BAD_REQUEST)

        return _sse_response(
            _stream_to_sse(
                qa_service.retry_stream(message, existing_answers, doc_data_list),
            )
        )


# ── Regenerate view ────────────────────────────────────────────────────────────

class QARegenerateView(APIView):
    """
    POST /v1/qa/messages/{message_id}/regenerate

    Regenerate the answer using the user's feedback reason (SSE).
    Body: {"reason": "Answer was too vague."}
    Always regenerates the first (only) document's answer.
    """

    def post(self, request, message_id):
        try:
            message = QAMessage.objects.select_related("session").get(id=message_id)
        except QAMessage.DoesNotExist:
            return _err(f"Message '{message_id}' not found.", status.HTTP_404_NOT_FOUND)

        serializer = QARegenerateSerializer(data=request.data)
        if not serializer.is_valid():
            return _err("Validation failed.", status.HTTP_400_BAD_REQUEST, data=serializer.errors)

        reason           = serializer.validated_data["reason"]
        existing_answers = message.answers_per_document
        target_answer    = existing_answers[0] if existing_answers else None

        if target_answer is None:
            return _err("No answer found to regenerate.", status.HTTP_400_BAD_REQUEST)

        try:
            session_doc = (
                message.session.session_documents
                .select_related("document")
                .get(position=0)
            )
        except QASessionDocument.DoesNotExist:
            return _err("Could not fetch document for regeneration.", status.HTTP_404_NOT_FOUND)

        previous_answer = target_answer.get("answer", "")

        return _sse_response(
            _stream_to_sse(
                qa_service.regenerate_stream(
                    message, existing_answers, reason, session_doc.document, previous_answer
                ),
            )
        )

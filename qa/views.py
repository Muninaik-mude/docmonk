"""
Q&A API views.

POST /v1/qa/sessions               — create session, extract/cache document text
POST /v1/qa/sessions/{id}/ask      — ask questions, answered per document
GET  /v1/qa/sessions/{id}          — session metadata + full interaction history
"""
import logging
from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from analyzer.serializers import MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from analyzer.services import pdf_service, r2_service

from .models import QADocument, QASession, QASessionDocument, QAInteraction
from .serializers import QASessionCreateSerializer, QAAskSerializer, QARenameSessionSerializer
from .services import qa_service

logger = logging.getLogger(__name__)


def _build_char_page_map(text_blocks: list) -> list:
    """
    Build a [{page, start, end}] char-position map from pdf_service text_blocks.
    Used later to resolve page_hint from a relevant_excerpt string.
    """
    result: list[dict] = []
    pos = 0
    for block in text_blocks:
        length = len(block["text"])
        result.append({
            "page":  block.get("page_num", 1),
            "start": pos,
            "end":   pos + length,
        })
        pos += length + 1  # +1 matches the \n separator in get_full_text
    return result


class QASessionView(APIView):
    """
    GET  /v1/qa/sessions?user_id=xxx  — list all sessions for a user (newest first)
    POST /v1/qa/sessions              — create a new session
    """

    def get(self, request):
        user_id = request.query_params.get("user_id", "").strip()
        if not user_id:
            return Response(
                {"status": "error", "message": "'user_id' query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sessions = (
            QASession.objects
            .filter(user_id=user_id)
            .prefetch_related("session_documents__document")
            .order_by("-created_at")
        )

        data = []
        for s in sessions:
            docs = [
                {
                    "document_id":       sd.document.document_id,
                    "document_filename": sd.document.document_filename,
                    "file_type":         sd.document.file_type,
                }
                for sd in s.session_documents.all()
            ]
            data.append({
                "session_id":  str(s.id),
                "name":        s.name,
                "user_id":     s.user_id,
                "documents":   docs,
                "created_at":  s.created_at.isoformat(),
                "updated_at":  s.updated_at.isoformat(),
            })

        return Response({"user_id": user_id, "sessions": data}, status=status.HTTP_200_OK)

    # ── POST — create session ──────────────────────────────────────────────
    # (docstring kept short; full description in original)

    def post(self, request):
        serializer = QASessionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated  = serializer.validated_data
        user_id    = validated["user_id"]
        doc_inputs = validated["documents"]

        # ── Download + extract outside atomic block (network I/O) ─────────────
        # We resolve all doc_bytes and metadata before touching the DB,
        # so the atomic block below is purely DB writes with no network calls.
        resolved_docs: list[dict[str, Any]] = []

        for doc_input in doc_inputs:
            document_id       = doc_input["document_id"]
            s3_url            = doc_input["s3_download_url"]
            document_filename = doc_input.get("document_filename", "document")

            # Cache check — if already extracted, reuse it
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
                logger.error("Failed to download document_id=%s: %s", document_id, e)
                return Response(
                    {
                        "status": "error",
                        "message": f"Failed to download document '{document_filename}': {e}",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            file_size_mb = len(doc_bytes) / (1024 * 1024)
            if len(doc_bytes) > MAX_FILE_SIZE_BYTES:
                return Response(
                    {
                        "status": "error",
                        "message": (
                            f"Document '{document_filename}' is {file_size_mb:.1f} MB, "
                            f"exceeds {MAX_FILE_SIZE_MB} MB limit."
                        ),
                    },
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

            type_hint   = s3_url or document_filename
            file_type   = pdf_service.detect_file_type(type_hint, doc_bytes)
            text_blocks = pdf_service.extract_text_blocks(type_hint, doc_bytes)
            full_text   = pdf_service.get_full_text(text_blocks)

            if not full_text.strip():
                return Response(
                    {
                        "status": "error",
                        "message": f"Document '{document_filename}' contains no extractable text.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            resolved_docs.append({
                "cached":            None,
                "document_id":       document_id,
                "s3_download_url":   s3_url,
                "document_filename": document_filename,
                "file_type":         file_type,
                "full_text":         full_text,
                "char_count":        len(full_text),
                "char_page_map":     _build_char_page_map(text_blocks),
            })

        # ── All DB writes in one atomic block — auto-rollback on any failure ──
        session_docs: list[dict[str, Any]] = []

        with transaction.atomic():
            session = QASession.objects.create(user_id=user_id)

            for position, doc_meta in enumerate(resolved_docs):
                if doc_meta.get("cached"):
                    qa_doc = doc_meta["cached"]
                else:
                    qa_doc = QADocument.objects.create(
                        document_id       = doc_meta["document_id"],
                        s3_download_url   = doc_meta["s3_download_url"],
                        document_filename = doc_meta["document_filename"],
                        file_type         = doc_meta["file_type"],
                        full_text         = doc_meta["full_text"],
                        char_count        = doc_meta["char_count"],
                        char_page_map     = doc_meta["char_page_map"],
                    )
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
                    "position":          position,
                })

        return Response(
            {
                "session_id": str(session.id),
                "name":       session.name,
                "user_id":    user_id,
                "documents":  session_docs,
                "created_at": session.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class QASessionDetailView(APIView):
    """
    GET    /v1/qa/sessions/{session_id}  — session detail + full interaction history
    PATCH  /v1/qa/sessions/{session_id}  — rename session  {"name": "New Name"}
    DELETE /v1/qa/sessions/{session_id}  — delete session + all interactions
    """

    def _get_session_or_404(self, session_id, prefetch=None):
        qs = QASession.objects
        if prefetch:
            qs = qs.prefetch_related(*prefetch)
        try:
            return qs.get(id=session_id), None
        except QASession.DoesNotExist:
            return None, Response(
                {"status": "error", "message": f"Session '{session_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

    def get(self, request, session_id):
        session, err = self._get_session_or_404(
            session_id, prefetch=["session_documents__document", "interactions"]
        )
        if err:
            return err

        documents = [
            {
                "document_id":       sd.document.document_id,
                "document_filename": sd.document.document_filename,
                "file_type":         sd.document.file_type,
                "char_count":        sd.document.char_count,
                "position":          sd.position,
            }
            for sd in session.session_documents.all()
        ]

        interactions = [
            {
                "id":                   str(ia.id),
                "question":             ia.question,
                "answers_per_document": ia.answers_per_document,
                "asked_at":             ia.asked_at.isoformat(),
            }
            for ia in session.interactions.all()
        ]

        return Response(
            {
                "session_id":   str(session.id),
                "name":         session.name,
                "user_id":      session.user_id,
                "documents":    documents,
                "interactions": interactions,
                "created_at":   session.created_at.isoformat(),
                "updated_at":   session.updated_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, session_id):
        """Rename session. Body: {"name": "New session name"}"""
        session, err = self._get_session_or_404(session_id)
        if err:
            return err

        serializer = QARenameSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session.name = serializer.validated_data["name"]
        session.save(update_fields=["name", "updated_at"])

        return Response(
            {
                "session_id": str(session.id),
                "name":       session.name,
                "updated_at": session.updated_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, session_id):
        """Delete session and all its interactions (cascade)."""
        session, err = self._get_session_or_404(session_id)
        if err:
            return err

        session.delete()
        return Response(
            {"status": "deleted", "session_id": str(session_id)},
            status=status.HTTP_200_OK,
        )


class QAAskView(APIView):
    """
    POST /v1/qa/sessions/{session_id}/ask

    Ask one or more questions. Each question is answered per document in the session.
    All (question × document) pairs are processed in parallel.
    Results are persisted as QAInteraction records.

    Request body:
        {"questions": ["What is the notice period?", "..."]}
    """

    def post(self, request, session_id):
        # ── Fetch session ──────────────────────────────────────────────────
        try:
            session = QASession.objects.prefetch_related(
                "session_documents__document"
            ).get(id=session_id)
        except QASession.DoesNotExist:
            return Response(
                {"status": "error", "message": f"Session '{session_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── Validate questions ─────────────────────────────────────────────
        serializer = QAAskSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        questions = serializer.validated_data["questions"]

        # ── Build doc_data list for qa_service ────────────────────────────
        doc_data_list = [
            {
                "document_id":       sd.document.document_id,
                "document_filename": sd.document.document_filename,
                "position":          sd.position,
                "full_text":         sd.document.full_text,
                "char_page_map":     sd.document.char_page_map,
            }
            for sd in session.session_documents.all()
        ]

        if not doc_data_list:
            return Response(
                {"status": "error", "message": "Session has no documents."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Run parallel Q&A ───────────────────────────────────────────────
        answers = qa_service.answer_questions(questions, doc_data_list)

        # ── Persist interactions ───────────────────────────────────────────
        QAInteraction.objects.bulk_create([
            QAInteraction(
                session              = session,
                question             = item["question"],
                answers_per_document = item["answers_per_document"],
            )
            for item in answers
        ])

        # ── Auto-name session from first question (only if unnamed) ────────
        if not session.name:
            session.name = questions[0][:100]
            session.save(update_fields=["name", "updated_at"])

        return Response(
            {
                "session_id":   str(session.id),
                "session_name": session.name,
                "answers":      answers,
            },
            status=status.HTTP_200_OK,
        )


class QAInteractionRetryView(APIView):
    """
    POST /v1/qa/interactions/{interaction_id}/retry

    Re-runs AI for all failed answers in a single interaction.
    An answer is considered failed when its "status" == "failed".

    Only failed answers are retried — successful ones are preserved as-is.
    The interaction record is updated in-place.

    Response includes the full updated answers_per_document list.
    """

    def post(self, request, interaction_id):
        # ── Fetch interaction ──────────────────────────────────────────────
        try:
            interaction = QAInteraction.objects.select_related("session").get(
                id=interaction_id
            )
        except QAInteraction.DoesNotExist:
            return Response(
                {"status": "error", "message": f"Interaction '{interaction_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        session = interaction.session

        # ── Identify failed answers ────────────────────────────────────────
        existing_answers = interaction.answers_per_document
        failed_positions = [
            ans["position"]
            for ans in existing_answers
            if ans.get("status") == "failed"
        ]

        if not failed_positions:
            return Response(
                {
                    "status":            "no_failures",
                    "message":           "No failed answers found. Nothing to retry.",
                    "interaction_id":    str(interaction.id),
                    "answers_per_document": existing_answers,
                },
                status=status.HTTP_200_OK,
            )

        # ── Fetch only the documents whose answers failed ──────────────────
        failed_session_docs = (
            session.session_documents
            .filter(position__in=failed_positions)
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
            return Response(
                {"status": "error", "message": "Could not find documents for failed answers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Re-run AI for failed answers only ─────────────────────────────
        retry_results = qa_service.answer_questions([interaction.question], doc_data_list)
        new_answers_by_position = {
            ans["position"]: ans
            for ans in retry_results[0]["answers_per_document"]
        }

        # ── Merge: keep successful answers, replace failed ones ───────────
        updated_answers = []
        for ans in existing_answers:
            if ans.get("status") == "failed" and ans["position"] in new_answers_by_position:
                updated_answers.append(new_answers_by_position[ans["position"]])
            else:
                updated_answers.append(ans)

        interaction.answers_per_document = updated_answers
        interaction.save(update_fields=["answers_per_document"])

        still_failed = sum(1 for a in updated_answers if a.get("status") == "failed")

        return Response(
            {
                "interaction_id":       str(interaction.id),
                "question":             interaction.question,
                "retried":              len(failed_positions),
                "still_failed":         still_failed,
                "answers_per_document": updated_answers,
            },
            status=status.HTTP_200_OK,
        )

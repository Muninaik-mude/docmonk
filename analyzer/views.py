import base64
import binascii
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, cast

from django.db import close_old_connections, transaction
from django.db.models import F as models_F
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ClauseAnalyzerSerializer, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from .services import r2_service, pdf_service, groq_service, report_service
from .models import (
    AnalysisJob, JobClause, ClauseResult, JobJurisdiction,
    JobStatus, ClauseStatus,
)

logger = logging.getLogger(__name__)

_MAX_PARALLEL_CLAUSES = 4  # number of parallel batch workers (= number of AI providers)
_CLAUSE_BATCH_SIZE    = groq_service.CLAUSE_BATCH_SIZE  # max clauses per single AI call

# Maps compliance result → color string stored in DB and returned in response
_STATUS_COLOR = {
    "MATCH": "",
    "NOT_FOUND": "blue",
    "PARTIALLY_SATISFIED": "orange",
    "VIOLATION": "red",
}


def _decode_base64_document(base64_string: str) -> bytes:
    """Decode a base64-encoded document string to raw bytes."""
    if "," in base64_string and base64_string.index(",") < 200:
        base64_string = base64_string.split(",", 1)[1]
    return base64.b64decode(base64_string.strip())


def _fallback_ai_result(clause: dict, error: Exception) -> dict:
    return {
        "result": "NOT_FOUND",
        "reason": f"Analysis failed: {type(error).__name__}",
        "relevant_text": None,
        "ai_recommendation": (
            f"Clause '{clause['title']}' could not be analyzed. Manual review recommended."
        ),
        "parties_obligated": [],
        "missing_values": [],
        "binding_strength": "VAGUE",
        "key_dates_durations": [],
        "_provider": "",
    }


def _detect_jurisdiction_with_db(job_id: str, full_text: str) -> dict:
    """
    Run jurisdiction detection and persist result to DB.
    Called inside ThreadPoolExecutor alongside clause analysis — runs concurrently.
    """
    close_old_connections()
    logger.info("Jurisdiction detection started (concurrent with clause analysis)")
    try:
        jurisdiction_info = groq_service.detect_jurisdiction(full_text)
        JobJurisdiction.objects.create(
            job_id=job_id,
            jurisdiction=jurisdiction_info.get("jurisdiction", "Unknown"),
            agreement_type=jurisdiction_info.get("agreement_type", ""),
            applicable_laws=jurisdiction_info.get("applicable_laws", []),
            checklist=jurisdiction_info.get("checklist", []),
        )
        logger.info("Jurisdiction detected: %s", jurisdiction_info.get("jurisdiction"))
        return jurisdiction_info
    except Exception as e:
        logger.error("Jurisdiction detection failed: %s", e)
        return {"jurisdiction": "Unknown", "agreement_type": "", "applicable_laws": [], "checklist": []}


def _analyze_batch_with_db(
    batch_items: list, full_text: str, context: str = ""
) -> list:
    """
    Analyze a batch of clauses in a single AI call and persist results to DB.

    batch_items: list of (clause_db_id, clause_dict)
    Returns list of {clause, ai_result, success} in the same order as batch_items.
    Called inside ThreadPoolExecutor — uses close_old_connections() for thread safety.
    """
    close_old_connections()
    clauses = [c for _, c in batch_items]
    logger.info(
        "Batch clause analysis started: %s",
        [c.get("id") for c in clauses],
    )

    # Transition all clauses → IN_PROGRESS
    for clause_db_id, _ in batch_items:
        JobClause.objects.filter(id=clause_db_id).update(
            status=ClauseStatus.IN_PROGRESS,
            updated_at=timezone.now(),
        )

    # Single AI call for the whole batch
    try:
        ai_results = groq_service.analyze_clauses_against_pdf(clauses, full_text, context=context)
    except Exception as e:
        logger.error("Batch AI call failed: %s", e)
        results = []
        for clause_db_id, clause in batch_items:
            JobClause.objects.filter(id=clause_db_id).update(
                status=ClauseStatus.FAILED,
                error_message=str(e)[:2000],
                retry_count=models_F("retry_count") + 1,
                failed_at=timezone.now(),
                updated_at=timezone.now(),
            )
            results.append({"clause": clause, "ai_result": _fallback_ai_result(clause, e), "success": False})
        return results

    # Persist each result individually (same DB writes as before)
    results = []
    for (clause_db_id, clause), ai_result in zip(batch_items, ai_results):
        try:
            result_status = ai_result.get("result", "NOT_FOUND")
            color = _STATUS_COLOR.get(result_status, "")
            clause_obj = JobClause.objects.get(id=clause_db_id)

            ClauseResult.objects.update_or_create(
                clause=clause_obj,
                defaults={
                    "result": result_status,
                    "color": color,
                    "reason": ai_result.get("reason", "") or "",
                    "relevant_text": ai_result.get("relevant_text", "") or "",
                    "ai_added_text": ai_result.get("ai_recommendation", "") or "",
                    "parties_obligated": ai_result.get("parties_obligated", []),
                    "missing_values": ai_result.get("missing_values", []),
                    "key_dates_durations": ai_result.get("key_dates_durations", []),
                    "binding_strength": ai_result.get("binding_strength", "VAGUE"),
                    "ai_provider_used": ai_result.get("_provider", ""),
                    "analysis_attempt": clause_obj.retry_count + 1,
                },
            )
            JobClause.objects.filter(id=clause_db_id).update(
                status=ClauseStatus.COMPLETED,
                updated_at=timezone.now(),
            )
            results.append({"clause": clause, "ai_result": ai_result, "success": True})

        except Exception as e:
            logger.error("DB save failed for clause '%s': %s", clause.get("id"), e)
            JobClause.objects.filter(id=clause_db_id).update(
                status=ClauseStatus.FAILED,
                error_message=str(e)[:2000],
                retry_count=models_F("retry_count") + 1,
                failed_at=timezone.now(),
                updated_at=timezone.now(),
            )
            results.append({"clause": clause, "ai_result": _fallback_ai_result(clause, e), "success": False})

    return results


class ClauseAnalyzerView(APIView):
    """
    POST /api/v1/analyze

    Synchronous pipeline — blocks until analysis is complete and reports are generated.
    Supports two modes based on the request payload:

    Clause mode  (default): clauses[] provided — analyze each clause against the document.
    Policy mode  (new):     policy_text provided — analyze the document against a policy document.
                            In policy mode, clause analysis is skipped entirely.

    Persists full state to DB as analysis progresses, enabling resume on partial failures
    (clause mode only — policy mode never partially fails).
    """

    def post(self, request):
        serializer = ClauseAnalyzerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # cast() so Pylance doesn't see the DRF `empty` sentinel type on validated_data
        validated_data = cast(dict, serializer.validated_data)

        doc_url: str | None = validated_data.get("document_presigned_url")
        doc_b64: str | None = validated_data.get("document_base64")
        doc_filename = validated_data.get("document_filename", "document.pdf")
        clauses      = validated_data.get("clauses") or []
        context      = validated_data.get("context") or ""

        agreement_meta = {
            "agreement_type":    validated_data.get("agreement_type", ""),
            "agreement_details": validated_data.get("agreement_details") or {},
            "parties":           validated_data.get("parties") or {},
            "property":          validated_data.get("property") or {},
        }

        # ── Create Job + Clauses in DB ─────────────────────────────────────────
        # Declared before the atomic block so Pylance sees a definite type outside it.
        clause_db_map: dict[str, JobClause] = {}

        with transaction.atomic():
            job = AnalysisJob.objects.create(
                status=JobStatus.PENDING,
                document_url=doc_url or "",
                document_filename=doc_filename,
                agreement_type=agreement_meta["agreement_type"],
                agreement_details=agreement_meta["agreement_details"],
                parties=agreement_meta["parties"],
                property_details=agreement_meta["property"],
                total_clauses=len(clauses),
                context=context,
            )
            for i, clause in enumerate(clauses):
                jc = JobClause.objects.create(
                    job=job,
                    clause_ref_id=clause["id"],
                    title=clause["title"],
                    value=clause["value"],
                    category=clause.get("category", ""),
                    position=i,
                )
                clause_db_map[clause["id"]] = jc

        # ── Step 1: Get document bytes ─────────────────────────────────────────
        if doc_b64:
            try:
                doc_bytes = _decode_base64_document(doc_b64)
                logger.info("Document decoded from base64 (%d bytes)", len(doc_bytes))
            except (binascii.Error, ValueError) as e:
                logger.error("Base64 decode failed: %s", e)
                job.status = JobStatus.FAILED
                job.error_message = f"Invalid base64 document: {e}"
                job.save(update_fields=["status", "error_message", "updated_at"])
                return Response(
                    {"status": "error", "job_id": str(job.id),
                     "message": f"Invalid base64 document: {e}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            try:
                doc_bytes = r2_service.download_document_from_presigned_url(doc_url or "")
                logger.info("Document downloaded successfully (%d bytes)", len(doc_bytes))
            except Exception as e:
                logger.error("Document download failed: %s", e)
                job.status = JobStatus.FAILED
                job.error_message = f"Failed to download document: {e}"
                job.save(update_fields=["status", "error_message", "updated_at"])
                return Response(
                    {"status": "error", "job_id": str(job.id),
                     "message": f"Failed to download document: {e}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Validate file size (max 100 MB)
        file_size_mb = len(doc_bytes) / (1024 * 1024)
        if len(doc_bytes) > MAX_FILE_SIZE_BYTES:
            job.status = JobStatus.FAILED
            job.error_message = f"File size {file_size_mb:.2f} MB exceeds {MAX_FILE_SIZE_MB} MB limit"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {
                    "status": "error",
                    "job_id": str(job.id),
                    "message": job.error_message,
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        # ── Step 2: Extract text ───────────────────────────────────────────────
        job.status = JobStatus.EXTRACTING
        job.save(update_fields=["status", "updated_at"])

        type_hint   = doc_url or doc_filename
        file_type   = pdf_service.detect_file_type(type_hint, doc_bytes)
        text_blocks = pdf_service.extract_text_blocks(type_hint, doc_bytes)
        full_text   = pdf_service.get_full_text(text_blocks)
        logger.info("Extracted %d text blocks from %s", len(text_blocks), file_type)

        if not full_text.strip():
            job.status = JobStatus.FAILED
            job.error_message = "Document contains no extractable text"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {"status": "error", "job_id": str(job.id),
                 "message": "Document contains no extractable text"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist extracted text — critical for resume (no re-download needed)
        job.full_text = full_text
        job.file_type = file_type
        job.file_size_bytes = len(doc_bytes)
        job.status = JobStatus.ANALYZING
        job.save(update_fields=["full_text", "file_type", "file_size_bytes", "status", "updated_at"])

        # ── Steps 3+4: Detect jurisdiction and analyze clauses concurrently ──────
        # Jurisdiction detection no longer blocks the clause loop — both run in
        # parallel inside the same executor. Jurisdiction result is collected after
        # all clause futures complete (it is only needed for report generation).
        job.status = JobStatus.ANALYZING
        job.save(update_fields=["status", "updated_at"])

        response_data: dict[str, Any] = {
            "status": "success",
            "job_id": str(job.id),
        }

        _result_map: dict[int, dict[str, Any]] = {}

        # Use only as many providers as needed — no point burning extra API tokens.
        # <10 clauses → 2 providers, <20 → 3 providers, ≥20 → all 4 providers.
        n_clauses = len(clauses)
        n_workers  = 2 if n_clauses < 10 else (3 if n_clauses < 20 else _MAX_PARALLEL_CLAUSES)
        batch_size = max(1, min(math.ceil(n_clauses / n_workers), _CLAUSE_BATCH_SIZE))
        batches = [
            (i, clauses[i:i + batch_size])
            for i in range(0, n_clauses, batch_size)
        ]

        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLAUSES + 1) as executor:
            # Submit jurisdiction detection alongside clause batches (does not block them)
            jurisdiction_future = executor.submit(
                _detect_jurisdiction_with_db, str(job.id), full_text
            )

            future_to_batch: dict = {}
            for start_idx, batch in batches:
                batch_items = [(str(clause_db_map[c["id"]].id), c) for c in batch]
                f = executor.submit(_analyze_batch_with_db, batch_items, full_text, context)
                future_to_batch[f] = (start_idx, batch_items)

            for future in as_completed(future_to_batch):
                start_idx, batch_items = future_to_batch[future]
                try:
                    batch_results = future.result()
                    for offset, item in enumerate(batch_results):
                        _result_map[start_idx + offset] = item
                except Exception as e:
                    logger.error("Unexpected batch thread error at index %d: %s", start_idx, e)
                    for offset, (_, clause) in enumerate(batch_items):
                        _result_map[start_idx + offset] = {
                            "clause": clause,
                            "ai_result": _fallback_ai_result(clause, e),
                            "success": False,
                        }

            # Collect jurisdiction result — will already be done in most cases
            jurisdiction_info = jurisdiction_future.result()

        # Rebuild as an ordered list (preserves original clause order)
        clause_results: list[dict[str, Any]] = [_result_map[i] for i in range(len(clauses))]

        # Update job progress counters from DB (source of truth)
        completed_count = job.clauses.filter(status=ClauseStatus.COMPLETED).count()
        failed_count    = job.clauses.filter(status=ClauseStatus.FAILED).count()
        job.completed_clauses = completed_count
        job.failed_clauses    = failed_count
        job.save(update_fields=["completed_clauses", "failed_clauses", "updated_at"])

        # ── Build analysis_summary ─────────────────────────────────────────────
        analysis_summary = []

        for item in clause_results:
            clause    = item["clause"]
            ai_result = item["ai_result"]
            ai_result.pop("_provider", None)

            result_status = ai_result["result"]
            ai_text       = ai_result.get("ai_recommendation")
            jc            = clause_db_map[clause["id"]]

            summary_entry = {
                "clause_id":           clause["id"],
                "clause_title":        clause["title"],
                "clause_value":        clause["value"],
                "clause_category":     clause.get("category", ""),
                "result":              result_status,
                "reason":              ai_result.get("reason"),
                "relevant_text":       ai_result.get("relevant_text"),
                "color":               _STATUS_COLOR.get(result_status) or None,
                "ai_added_text":       ai_text if result_status != "MATCH" else None,
                "parties_obligated":   ai_result.get("parties_obligated", []),
                "missing_values":      ai_result.get("missing_values", []),
                "binding_strength":    ai_result.get("binding_strength", "VAGUE"),
                "key_dates_durations": ai_result.get("key_dates_durations", []),
                "analysis_status":     jc.status,
                "retry_count":         jc.retry_count,
            }
            analysis_summary.append(summary_entry)

        conflicts = []

        # ── Generate markdown reports ──────────────────────────────────────────
        job.status = JobStatus.GENERATING_REPORTS
        job.save(update_fields=["status", "updated_at"])

        report_kwargs: dict[str, Any] = dict(
            conflicts=conflicts,
            jurisdiction_info=jurisdiction_info,
            full_text=full_text,
            agreement_meta=agreement_meta,
        )

        try:
            md_report  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
            md_summary = report_service.generate_markdown_summary(analysis_summary, **report_kwargs)
            response_data["report_md_base64"]  = base64.b64encode(md_report.encode()).decode()
            response_data["summary_md_base64"] = base64.b64encode(md_summary.encode()).decode()
            logger.info("Markdown report + summary generated for job %s", job.id)
        except Exception as e:
            logger.error("Markdown report generation failed: %s", e)
            response_data["report_error"] = f"Report could not be generated: {e}"

        # ── Finalize job ───────────────────────────────────────────────────────
        if failed_count > 0:
            job.status = JobStatus.PARTIAL_FAILURE
        else:
            job.status       = JobStatus.COMPLETED
            job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])

        response_data["status"]     = job.status.lower()
        response_data["can_resume"] = job.status == JobStatus.PARTIAL_FAILURE
        response_data["progress"]   = {
            "total":     job.total_clauses,
            "completed": job.completed_clauses,
            "failed":    job.failed_clauses,
        }

        return Response(response_data, status=status.HTTP_200_OK)

import base64
import binascii
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from typing import Any, cast

from django.db import close_old_connections, transaction
from django.db.models import F as models_F
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ClauseAnalyzerSerializer, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from .services import r2_service, pdf_service, groq_service, report_service
from .utils.color_constants import STATUS_HIGHLIGHT_COLOR, STATUS_INSERTION_COLOR
from .models import (
    AnalysisJob, JobClause, ClauseResult, JobJurisdiction, JobReport,
    JobStatus, ClauseStatus, ReportType,
)

logger = logging.getLogger(__name__)

_MAX_PARALLEL_CLAUSES = 3

# Maps compliance result → color string stored in DB and returned in response
_STATUS_COLOR = {
    "MATCH": "",
    "NOT_FOUND": "blue",
    "PARTIALLY_SATISFIED": "orange",
    "VIOLATION": "red",
}


def _upload_or_save(file_bytes: bytes, filename: str, prefix: str, content_type: str) -> dict:
    """Upload file to R2 or save locally. Returns {url, expires_in}."""
    if r2_service.is_r2_configured():
        object_key = r2_service.upload_file_to_r2(file_bytes, filename, prefix, content_type)
        url = r2_service.generate_presigned_download_url(object_key)
        return {"url": url, "expires_in": "24 hour", "file_key": object_key, "backend": "r2"}
    else:
        r2_service.save_file_locally(file_bytes, filename)
        return {
            "url": f"/annotated_pdfs/{filename}",
            "expires_in": "local file (no expiry)",
            "file_key": filename,
            "backend": "local",
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


def _analyze_single_clause_with_db(clause_db_id: str, clause: dict, full_text: str) -> dict:
    """
    Analyze one clause and persist state transitions to DB.
    Called inside ThreadPoolExecutor — uses close_old_connections() for thread safety.
    """
    close_old_connections()

    # Transition → IN_PROGRESS
    JobClause.objects.filter(id=clause_db_id).update(
        status=ClauseStatus.IN_PROGRESS,
        updated_at=timezone.now(),
    )

    try:
        ai_result = groq_service.analyze_clause_against_pdf(clause, full_text)
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

        # Transition → COMPLETED
        JobClause.objects.filter(id=clause_db_id).update(
            status=ClauseStatus.COMPLETED,
            updated_at=timezone.now(),
        )

        return {"clause": clause, "ai_result": ai_result, "success": True}

    except Exception as e:
        logger.error("Clause analysis failed for '%s': %s", clause.get("id"), e)

        # Transition → FAILED, increment retry_count
        JobClause.objects.filter(id=clause_db_id).update(
            status=ClauseStatus.FAILED,
            error_message=str(e)[:2000],
            retry_count=models_F("retry_count") + 1,
            failed_at=timezone.now(),
            updated_at=timezone.now(),
        )

        return {"clause": clause, "ai_result": _fallback_ai_result(clause, e), "success": False}


def _save_report(job: AnalysisJob, report_type: str, upload_result: dict, file_size: int) -> None:
    """Create or update a JobReport record after a successful upload/save."""
    expires_at = None
    if upload_result["backend"] == "r2":
        expires_at = timezone.now() + timedelta(hours=24)

    JobReport.objects.update_or_create(
        job=job,
        report_type=report_type,
        defaults={
            "storage_backend": upload_result["backend"],
            "file_key": upload_result["file_key"],
            "presigned_url": upload_result["url"],
            "expires_at": expires_at,
            "file_size_bytes": file_size,
        },
    )


class ClauseAnalyzerView(APIView):
    """
    POST /api/v1/analyze

    Synchronous pipeline — blocks until all clauses are analyzed and reports are generated.
    Persists full state to DB as analysis progresses, enabling resume on partial failures.

    New response fields vs old:
      job_id        — UUID to track/resume this analysis
      status        — final job state (completed | partial_failure | failed)
      can_resume    — true when some clauses failed and can be retried
      progress      — {total, completed, failed} clause counts
      analysis_summary[].analysis_status  — per-clause COMPLETED | FAILED
      analysis_summary[].retry_count      — how many times this clause was retried
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
        clauses      = validated_data["clauses"]
        report_format = validated_data.get("report_format", "markdown")

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
                report_format=report_format,
                total_clauses=len(clauses),
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

        type_hint  = doc_url or doc_filename
        file_type  = pdf_service.detect_file_type(type_hint, doc_bytes)
        text_blocks = pdf_service.extract_text_blocks(type_hint, doc_bytes)
        full_text  = pdf_service.get_full_text(text_blocks)
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
        job.status = JobStatus.DETECTING_JURISDICTION
        job.save(update_fields=["full_text", "file_type", "file_size_bytes", "status", "updated_at"])

        # ── Step 3: Detect jurisdiction ────────────────────────────────────────
        jurisdiction_info = groq_service.detect_jurisdiction(full_text)
        logger.info("Jurisdiction detected: %s", jurisdiction_info.get("jurisdiction"))

        JobJurisdiction.objects.create(
            job=job,
            jurisdiction=jurisdiction_info.get("jurisdiction", "Unknown"),
            agreement_type=jurisdiction_info.get("agreement_type", ""),
            applicable_laws=jurisdiction_info.get("applicable_laws", []),
            checklist=jurisdiction_info.get("checklist", []),
        )

        # ── Step 4: Analyze clauses in parallel ───────────────────────────────
        job.status = JobStatus.ANALYZING
        job.save(update_fields=["status", "updated_at"])

        # Use a dict keyed by index to avoid the list[None] invariance issue that
        # causes Pylance to report "__getitem__ not defined on None" when iterating.
        _result_map: dict[int, dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLAUSES) as executor:
            future_to_idx = {
                executor.submit(
                    _analyze_single_clause_with_db,
                    str(clause_db_map[clause["id"]].id),
                    clause,
                    full_text,
                ): idx
                for idx, clause in enumerate(clauses)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    _result_map[idx] = future.result()
                except Exception as e:
                    # Outer catch — _analyze_single_clause_with_db already handles internally
                    logger.error("Unexpected thread error for index %d: %s", idx, e)
                    _result_map[idx] = {
                        "clause": clauses[idx],
                        "ai_result": _fallback_ai_result(clauses[idx], e),
                        "success": False,
                    }

        # Rebuild as an ordered list (preserves original clause order)
        clause_results: list[dict[str, Any]] = [_result_map[i] for i in range(len(clauses))]

        # Update job progress counters from DB (source of truth)
        completed_count = job.clauses.filter(status=ClauseStatus.COMPLETED).count()
        failed_count    = job.clauses.filter(status=ClauseStatus.FAILED).count()
        job.completed_clauses = completed_count
        job.failed_clauses    = failed_count
        job.save(update_fields=["completed_clauses", "failed_clauses", "updated_at"])

        # ── Build analysis_summary + PDF annotations ───────────────────────────
        analysis_summary = []
        annotations      = []

        for item in clause_results:
            clause    = item["clause"]
            ai_result = item["ai_result"]
            ai_result.pop("_provider", None)

            result_status   = ai_result["result"]
            highlight_color = STATUS_HIGHLIGHT_COLOR.get(result_status)
            insertion_color = STATUS_INSERTION_COLOR.get(result_status)
            ai_text         = ai_result.get("ai_recommendation")
            jc              = clause_db_map[clause["id"]]

            summary_entry = {
                "clause_id":          clause["id"],
                "clause_title":       clause["title"],
                "clause_value":       clause["value"],
                "result":             result_status,
                "reason":             ai_result.get("reason"),
                "relevant_text":      ai_result.get("relevant_text"),
                "color":              _STATUS_COLOR.get(result_status) or None,
                "ai_added_text":      None,
                "parties_obligated":  ai_result.get("parties_obligated", []),
                "missing_values":     ai_result.get("missing_values", []),
                "binding_strength":   ai_result.get("binding_strength", "VAGUE"),
                "key_dates_durations": ai_result.get("key_dates_durations", []),
                # New state fields
                "analysis_status": jc.status,
                "retry_count":     jc.retry_count,
            }

            if result_status == "NOT_FOUND":
                summary_entry["ai_added_text"] = ai_text
                if text_blocks:
                    last_block = text_blocks[-1]
                    annotations.append({
                        "page_num":            last_block["page_num"],
                        "bbox":                last_block["bbox"],
                        "highlight_color":     highlight_color,
                        "inserted_text":       f"[MISSING - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            elif result_status == "PARTIALLY_SATISFIED":
                summary_entry["ai_added_text"] = ai_text
                relevant_text = ai_result.get("relevant_text")
                location = groq_service.find_text_location_in_pdf(text_blocks, relevant_text)
                if not location:
                    location = groq_service.find_text_location_in_pdf(text_blocks, clause["title"])
                if not location and text_blocks:
                    last_block = text_blocks[-1]
                    location = {"page_num": last_block["page_num"], "bbox": last_block["bbox"]}
                if location:
                    annotations.append({
                        "page_num":            location["page_num"],
                        "bbox":                location["bbox"],
                        "highlight_color":     highlight_color,
                        "inserted_text":       f"[PARTIAL - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            elif result_status == "VIOLATION":
                summary_entry["ai_added_text"] = ai_text
                relevant_text = ai_result.get("relevant_text")
                location = groq_service.find_text_location_in_pdf(text_blocks, relevant_text)
                if not location:
                    location = groq_service.find_text_location_in_pdf(text_blocks, clause["title"])
                if not location and text_blocks:
                    last_block = text_blocks[-1]
                    location = {"page_num": last_block["page_num"], "bbox": last_block["bbox"]}
                if location:
                    annotations.append({
                        "page_num":            location["page_num"],
                        "bbox":                location["bbox"],
                        "highlight_color":     highlight_color,
                        "inserted_text":       f"[CORRECTION - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            analysis_summary.append(summary_entry)

        conflicts = []

        # ── Step 5: Generate reports ───────────────────────────────────────────
        job.status = JobStatus.GENERATING_REPORTS
        job.save(update_fields=["status", "updated_at"])

        run_id = uuid.uuid4()
        response_data = {
            "status":      "success",   # overwritten below after job finalization
            "job_id":      str(job.id),
            "analysis_summary": analysis_summary,
            "conflicts":   conflicts,
            "jurisdiction": jurisdiction_info,
        }

        report_kwargs: dict[str, Any] = dict(
            conflicts=conflicts,
            jurisdiction_info=jurisdiction_info,
            full_text=full_text,
            agreement_meta=agreement_meta,
        )

        try:
            # Markdown is always generated and returned inline as base64
            _md_report  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
            _md_summary = report_service.generate_markdown_summary(analysis_summary, **report_kwargs)
            response_data["report_md_base64"]  = base64.b64encode(_md_report.encode()).decode()
            response_data["summary_md_base64"] = base64.b64encode(_md_summary.encode()).decode()

            if report_format in ("pdf", "both"):
                pdf_report_bytes    = report_service.generate_pdf_report(analysis_summary, **report_kwargs)
                pdf_report_filename = f"report_{run_id}.pdf"
                result = _upload_or_save(pdf_report_bytes, pdf_report_filename, "reports", "application/pdf")
                response_data["report_pdf_url"]        = result["url"]
                response_data["report_pdf_expires_in"] = result["expires_in"]
                _save_report(job, ReportType.PDF_REPORT, result, len(pdf_report_bytes))
                logger.info("PDF report generated: %s", pdf_report_filename)

                pdf_summary_bytes    = report_service.generate_pdf_summary(analysis_summary, **report_kwargs)
                pdf_summary_filename = f"summary_{run_id}.pdf"
                result = _upload_or_save(pdf_summary_bytes, pdf_summary_filename, "reports", "application/pdf")
                response_data["summary_pdf_url"]        = result["url"]
                response_data["summary_pdf_expires_in"] = result["expires_in"]
                _save_report(job, ReportType.PDF_SUMMARY, result, len(pdf_summary_bytes))
                logger.info("PDF summary generated: %s", pdf_summary_filename)

            if report_format in ("markdown", "both"):
                md_content  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
                md_bytes    = md_content.encode("utf-8")
                md_filename = f"report_{run_id}.md"
                result = _upload_or_save(md_bytes, md_filename, "reports", "text/markdown")
                response_data["report_markdown_url"]        = result["url"]
                response_data["report_markdown_expires_in"] = result["expires_in"]
                _save_report(job, ReportType.MARKDOWN_REPORT, result, len(md_bytes))
                logger.info("Markdown report generated: %s", md_filename)

                md_summary       = report_service.generate_markdown_summary(analysis_summary, **report_kwargs)
                md_summary_bytes = md_summary.encode("utf-8")
                md_summary_filename = f"summary_{run_id}.md"
                result = _upload_or_save(md_summary_bytes, md_summary_filename, "reports", "text/markdown")
                response_data["summary_markdown_url"]        = result["url"]
                response_data["summary_markdown_expires_in"] = result["expires_in"]
                _save_report(job, ReportType.MARKDOWN_SUMMARY, result, len(md_summary_bytes))
                logger.info("Markdown summary generated: %s", md_summary_filename)

            if report_format == "docx":
                docx_bytes    = report_service.generate_docx_report(analysis_summary, **report_kwargs)
                docx_filename = f"report_{run_id}.docx"
                _docx_ct      = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                result = _upload_or_save(docx_bytes, docx_filename, "reports", _docx_ct)
                response_data["report_docx_url"]        = result["url"]
                response_data["report_docx_expires_in"] = result["expires_in"]
                _save_report(job, ReportType.DOCX_REPORT, result, len(docx_bytes))
                logger.info("DOCX report generated: %s", docx_filename)

                docx_summary_bytes    = report_service.generate_docx_summary(analysis_summary, **report_kwargs)
                docx_summary_filename = f"summary_{run_id}.docx"
                result = _upload_or_save(docx_summary_bytes, docx_summary_filename, "reports", _docx_ct)
                response_data["summary_docx_url"]        = result["url"]
                response_data["summary_docx_expires_in"] = result["expires_in"]
                _save_report(job, ReportType.DOCX_SUMMARY, result, len(docx_summary_bytes))
                logger.info("DOCX summary generated: %s", docx_summary_filename)

        except Exception as e:
            logger.error("Report generation/upload failed: %s", e)
            response_data["report_error"] = f"Report could not be generated/uploaded: {e}"

        # ── Finalize job ───────────────────────────────────────────────────────
        if failed_count > 0:
            job.status = JobStatus.PARTIAL_FAILURE
        else:
            job.status       = JobStatus.COMPLETED
            job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])

        # Enrich response with job tracking fields
        response_data["status"]     = job.status.lower()
        response_data["can_resume"] = job.status == JobStatus.PARTIAL_FAILURE
        response_data["progress"]   = {
            "total":     job.total_clauses,
            "completed": job.completed_clauses,
            "failed":    job.failed_clauses,
        }

        return Response(response_data, status=status.HTTP_200_OK)

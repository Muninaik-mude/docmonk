"""
Job tracking and resume endpoints.

GET  /api/v1/jobs/{job_id}        — Poll job status and retrieve full results
POST /api/v1/jobs/{job_id}/resume — Re-run only FAILED/PENDING clauses (no re-download needed)
"""
import base64
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from django.db import close_old_connections
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import groq_service, report_service
from .models import (
    AnalysisJob, JobClause, ClauseResult, JobJurisdiction,
    JobStatus, ClauseStatus, ReportType,
)
from .views import (
    _upload_or_save, _save_report, _fallback_ai_result,
    _STATUS_COLOR, _MAX_PARALLEL_CLAUSES,
)

logger = logging.getLogger(__name__)


def _reconstruct_analysis_summary(job: AnalysisJob) -> list:
    """
    Rebuild analysis_summary list from DB records — same shape as the original response.
    Used in both GET (full results) and POST resume (merged old + new results).
    """
    clauses = job.clauses.select_related("result").order_by("position")
    summary = []

    for jc in clauses:
        entry = {
            "clause_id":           jc.clause_ref_id,
            "clause_title":        jc.title,
            "clause_value":        jc.value,
            "analysis_status":     jc.status,
            "retry_count":         jc.retry_count,
            # Defaults for failed/pending clauses (overwritten below if result exists)
            "result":              None,
            "reason":              None,
            "relevant_text":       None,
            "color":               None,
            "ai_added_text":       None,
            "parties_obligated":   [],
            "missing_values":      [],
            "binding_strength":    "VAGUE",
            "key_dates_durations": [],
        }

        try:
            r = jc.result   # raises ClauseResult.DoesNotExist if not yet analyzed
            entry.update({
                "result":              r.result,
                "reason":              r.reason or None,
                "relevant_text":       r.relevant_text or None,
                "color":               r.color or None,
                "ai_added_text":       r.ai_added_text or None,
                "parties_obligated":   r.parties_obligated,
                "missing_values":      r.missing_values,
                "binding_strength":    r.binding_strength,
                "key_dates_durations": r.key_dates_durations,
            })
        except ClauseResult.DoesNotExist:
            pass

        summary.append(entry)

    return summary


def _reconstruct_jurisdiction(job: AnalysisJob) -> dict:
    try:
        ji = job.jurisdiction_info
        return {
            "jurisdiction":    ji.jurisdiction,
            "agreement_type":  ji.agreement_type,
            "applicable_laws": ji.applicable_laws,
            "checklist":       ji.checklist,
        }
    except JobJurisdiction.DoesNotExist:
        return {}


def _reconstruct_conflicts(job: AnalysisJob) -> list:
    return [
        {
            "clause_a": c.clause_a_title,
            "clause_b": c.clause_b_title,
            "conflict": c.conflict_description,
        }
        for c in job.conflict_records.all()
    ]


def _reconstruct_report_urls(job: AnalysisJob) -> dict:
    """Build report URL fields from stored JobReport records."""
    urls = {}
    for report in job.reports.all():
        rt = report.report_type
        if rt == ReportType.PDF_REPORT:
            urls["report_pdf_url"]        = report.presigned_url
            urls["report_pdf_expires_in"] = "24 hour"
        elif rt == ReportType.PDF_SUMMARY:
            urls["summary_pdf_url"]        = report.presigned_url
            urls["summary_pdf_expires_in"] = "24 hour"
        elif rt == ReportType.MARKDOWN_REPORT:
            urls["report_markdown_url"]        = report.presigned_url
            urls["report_markdown_expires_in"] = "24 hour"
        elif rt == ReportType.MARKDOWN_SUMMARY:
            urls["summary_markdown_url"]        = report.presigned_url
            urls["summary_markdown_expires_in"] = "24 hour"
        elif rt == ReportType.DOCX_REPORT:
            urls["report_docx_url"]        = report.presigned_url
            urls["report_docx_expires_in"] = "24 hour"
        elif rt == ReportType.DOCX_SUMMARY:
            urls["summary_docx_url"]        = report.presigned_url
            urls["summary_docx_expires_in"] = "24 hour"
    return urls


def _analyze_clause_for_resume(clause_db_id: str, clause_dict: dict, full_text: str) -> dict:
    """
    Resume variant of the thread worker. Identical to the main one but imported
    here to keep job_views self-contained (avoids import cycles).
    """
    close_old_connections()

    JobClause.objects.filter(id=clause_db_id).update(
        status=ClauseStatus.IN_PROGRESS,
        updated_at=timezone.now(),
    )

    try:
        ai_result     = groq_service.analyze_clause_against_pdf(clause_dict, full_text)
        result_status = ai_result.get("result", "NOT_FOUND")
        color         = _STATUS_COLOR.get(result_status, "")

        clause_obj = JobClause.objects.get(id=clause_db_id)

        ClauseResult.objects.update_or_create(
            clause=clause_obj,
            defaults={
                "result":              result_status,
                "color":               color,
                "reason":              ai_result.get("reason", "") or "",
                "relevant_text":       ai_result.get("relevant_text", "") or "",
                "ai_added_text":       ai_result.get("ai_recommendation", "") or "",
                "parties_obligated":   ai_result.get("parties_obligated", []),
                "missing_values":      ai_result.get("missing_values", []),
                "key_dates_durations": ai_result.get("key_dates_durations", []),
                "binding_strength":    ai_result.get("binding_strength", "VAGUE"),
                "ai_provider_used":    ai_result.get("_provider", ""),
                "analysis_attempt":    clause_obj.retry_count + 1,
            },
        )

        JobClause.objects.filter(id=clause_db_id).update(
            status=ClauseStatus.COMPLETED,
            updated_at=timezone.now(),
        )
        return {"clause": clause_dict, "ai_result": ai_result, "success": True}

    except Exception as e:
        logger.error("Resume clause analysis failed for '%s': %s", clause_dict.get("id"), e)
        JobClause.objects.filter(id=clause_db_id).update(
            status=ClauseStatus.FAILED,
            error_message=str(e)[:2000],
            retry_count=F("retry_count") + 1,
            failed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return {"clause": clause_dict, "ai_result": _fallback_ai_result(clause_dict, e), "success": False}


# ─────────────────────────────────────────────────────────────────────────────

class JobStatusView(APIView):
    """
    GET /api/v1/jobs/{job_id}

    Returns the current state of a job plus all persisted results.
    Safe to poll — read-only, no DB writes.
    """

    def get(self, request, job_id):
        try:
            job = AnalysisJob.objects.get(id=job_id)
        except (AnalysisJob.DoesNotExist, Exception):
            return Response(
                {"status": "error", "message": f"Job '{job_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        analysis_summary = _reconstruct_analysis_summary(job)
        jurisdiction     = _reconstruct_jurisdiction(job)
        conflicts        = _reconstruct_conflicts(job)
        report_urls      = _reconstruct_report_urls(job)

        response_data = {
            "job_id":           str(job.id),
            "status":           job.status.lower(),
            "can_resume":       job.status == JobStatus.PARTIAL_FAILURE,
            "progress": {
                "total":     job.total_clauses,
                "completed": job.completed_clauses,
                "failed":    job.failed_clauses,
            },
            "analysis_summary": analysis_summary,
            "conflicts":        conflicts,
            "jurisdiction":     jurisdiction,
            "created_at":       job.created_at.isoformat(),
            "completed_at":     job.completed_at.isoformat() if job.completed_at else None,
            **report_urls,
        }

        if job.error_message:
            response_data["error_message"] = job.error_message

        return Response(response_data, status=status.HTTP_200_OK)


class JobResumeView(APIView):
    """
    POST /api/v1/jobs/{job_id}/resume

    Re-runs only FAILED/PENDING clauses for a job in PARTIAL_FAILURE state.
    Uses the full_text already stored in DB — no document re-download needed.
    Returns the same response shape as POST /analyze with updated results.
    """

    def post(self, request, job_id):
        try:
            job = AnalysisJob.objects.get(id=job_id)
        except (AnalysisJob.DoesNotExist, Exception):
            return Response(
                {"status": "error", "message": f"Job '{job_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if job.status != JobStatus.PARTIAL_FAILURE:
            return Response(
                {
                    "status": "error",
                    "message": (
                        f"Job is in '{job.status}' state. "
                        "Only jobs with status 'PARTIAL_FAILURE' can be resumed."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not job.full_text.strip():
            return Response(
                {"status": "error", "message": "Stored document text is empty — cannot resume."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Collect clauses to retry ───────────────────────────────────────────
        failed_clauses_qs = job.clauses.filter(
            status__in=[ClauseStatus.FAILED, ClauseStatus.PENDING]
        ).order_by("position")

        if not failed_clauses_qs.exists():
            return Response(
                {"status": "error", "message": "No failed clauses found to resume."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        full_text = job.full_text
        logger.info(
            "Resuming job %s — retrying %d failed clause(s)",
            job_id, failed_clauses_qs.count(),
        )

        # ── Update job status ──────────────────────────────────────────────────
        job.status = JobStatus.ANALYZING
        job.save(update_fields=["status", "updated_at"])

        # ── Re-run failed clauses in parallel ─────────────────────────────────
        failed_list = list(failed_clauses_qs)

        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLAUSES) as executor:
            futures = {
                executor.submit(
                    _analyze_clause_for_resume,
                    str(jc.id),
                    {"id": jc.clause_ref_id, "title": jc.title, "value": jc.value,
                     "category": jc.category},
                    full_text,
                ): jc
                for jc in failed_list
            }
            for future in as_completed(futures):
                jc = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error("Unexpected resume thread error for clause %s: %s", jc.clause_ref_id, e)

        # ── Refresh counters ───────────────────────────────────────────────────
        completed_count = job.clauses.filter(status=ClauseStatus.COMPLETED).count()
        failed_count    = job.clauses.filter(status=ClauseStatus.FAILED).count()
        job.completed_clauses = completed_count
        job.failed_clauses    = failed_count

        # ── Rebuild full analysis_summary from DB ──────────────────────────────
        analysis_summary = _reconstruct_analysis_summary(job)
        jurisdiction     = _reconstruct_jurisdiction(job)
        conflicts        = _reconstruct_conflicts(job)

        agreement_meta = {
            "agreement_type":    job.agreement_type,
            "agreement_details": job.agreement_details,
            "parties":           job.parties,
            "property":          job.property_details,
        }

        # ── Re-generate reports if all clauses now complete ───────────────────
        response_data = {
            "job_id":           str(job.id),
            "analysis_summary": analysis_summary,
            "conflicts":        conflicts,
            "jurisdiction":     jurisdiction,
        }

        report_kwargs: dict[str, Any] = dict(
            conflicts=conflicts,
            jurisdiction_info=jurisdiction,
            full_text=full_text,
            agreement_meta=agreement_meta,
        )

        if failed_count == 0:
            job.status = JobStatus.GENERATING_REPORTS
            job.save(update_fields=["status", "updated_at"])

            run_id = uuid.uuid4()
            report_format = job.report_format

            try:
                _md_report  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
                _md_summary = report_service.generate_markdown_summary(analysis_summary, **report_kwargs)
                response_data["report_md_base64"]  = base64.b64encode(_md_report.encode()).decode()
                response_data["summary_md_base64"] = base64.b64encode(_md_summary.encode()).decode()

                if report_format in ("pdf", "both"):
                    pdf_bytes    = report_service.generate_pdf_report(analysis_summary, **report_kwargs)
                    pdf_filename = f"report_{run_id}.pdf"
                    result = _upload_or_save(pdf_bytes, pdf_filename, "reports", "application/pdf")
                    response_data["report_pdf_url"]        = result["url"]
                    response_data["report_pdf_expires_in"] = result["expires_in"]
                    _save_report(job, ReportType.PDF_REPORT, result, len(pdf_bytes))

                    pdf_sum_bytes    = report_service.generate_pdf_summary(analysis_summary, **report_kwargs)
                    pdf_sum_filename = f"summary_{run_id}.pdf"
                    result = _upload_or_save(pdf_sum_bytes, pdf_sum_filename, "reports", "application/pdf")
                    response_data["summary_pdf_url"]        = result["url"]
                    response_data["summary_pdf_expires_in"] = result["expires_in"]
                    _save_report(job, ReportType.PDF_SUMMARY, result, len(pdf_sum_bytes))

                if report_format in ("markdown", "both"):
                    md_content  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
                    md_bytes    = md_content.encode("utf-8")
                    md_filename = f"report_{run_id}.md"
                    result = _upload_or_save(md_bytes, md_filename, "reports", "text/markdown")
                    response_data["report_markdown_url"]        = result["url"]
                    response_data["report_markdown_expires_in"] = result["expires_in"]
                    _save_report(job, ReportType.MARKDOWN_REPORT, result, len(md_bytes))

                    md_sum       = report_service.generate_markdown_summary(analysis_summary, **report_kwargs)
                    md_sum_bytes = md_sum.encode("utf-8")
                    md_sum_file  = f"summary_{run_id}.md"
                    result = _upload_or_save(md_sum_bytes, md_sum_file, "reports", "text/markdown")
                    response_data["summary_markdown_url"]        = result["url"]
                    response_data["summary_markdown_expires_in"] = result["expires_in"]
                    _save_report(job, ReportType.MARKDOWN_SUMMARY, result, len(md_sum_bytes))

                if report_format == "docx":
                    _docx_ct      = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    docx_bytes    = report_service.generate_docx_report(analysis_summary, **report_kwargs)
                    docx_filename = f"report_{run_id}.docx"
                    result = _upload_or_save(docx_bytes, docx_filename, "reports", _docx_ct)
                    response_data["report_docx_url"]        = result["url"]
                    response_data["report_docx_expires_in"] = result["expires_in"]
                    _save_report(job, ReportType.DOCX_REPORT, result, len(docx_bytes))

                    docx_sum_bytes = report_service.generate_docx_summary(analysis_summary, **report_kwargs)
                    docx_sum_file  = f"summary_{run_id}.docx"
                    result = _upload_or_save(docx_sum_bytes, docx_sum_file, "reports", _docx_ct)
                    response_data["summary_docx_url"]        = result["url"]
                    response_data["summary_docx_expires_in"] = result["expires_in"]
                    _save_report(job, ReportType.DOCX_SUMMARY, result, len(docx_sum_bytes))

            except Exception as e:
                logger.error("Resume report generation failed: %s", e)
                response_data["report_error"] = f"Report could not be generated: {e}"

            job.status       = JobStatus.COMPLETED
            job.completed_at = timezone.now()

        else:
            # Still have failures — stay resumable, include existing report URLs
            job.status = JobStatus.PARTIAL_FAILURE
            response_data.update(_reconstruct_report_urls(job))

        job.save(update_fields=["status", "completed_clauses", "failed_clauses", "completed_at", "updated_at"])

        response_data["status"]     = job.status.lower()
        response_data["can_resume"] = job.status == JobStatus.PARTIAL_FAILURE
        response_data["progress"]   = {
            "total":     job.total_clauses,
            "completed": job.completed_clauses,
            "failed":    job.failed_clauses,
        }

        return Response(response_data, status=status.HTTP_200_OK)

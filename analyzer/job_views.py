"""
Job tracking and resume endpoints.

GET  /api/v1/jobs/{job_id}        — Poll job status and retrieve full results
POST /api/v1/jobs/{job_id}/resume — Re-run only FAILED/PENDING clauses (no re-download needed)
"""
import base64
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import report_service
from .models import (
    AnalysisJob, JobClause, ClauseResult, JobJurisdiction,
    JobStatus, ClauseStatus,
)
from .views import (
    _MAX_PARALLEL_CLAUSES, _CLAUSE_BATCH_SIZE,
    _analyze_batch_with_db,
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
            "clause_category":     jc.category,
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

        agreement_meta = {
            "agreement_type":    job.agreement_type,
            "agreement_details": job.agreement_details,
            "parties":           job.parties,
            "property":          job.property_details,
        }
        report_kwargs: dict[str, Any] = dict(
            conflicts=conflicts,
            jurisdiction_info=jurisdiction,
            full_text=job.full_text,
            agreement_meta=agreement_meta,
        )

        clauses_analysis = {entry["clause_id"]: {k: v for k, v in entry.items() if k != "clause_id"}
                            for entry in analysis_summary}

        response_data: dict[str, Any] = {
            "job_id":     str(job.id),
            "status":     job.status.lower(),
            "can_resume": job.status == JobStatus.PARTIAL_FAILURE,
            "progress": {
                "total":     job.total_clauses,
                "completed": job.completed_clauses,
                "failed":    job.failed_clauses,
            },
            "analysis_summary": {
                "jurisdiction":    jurisdiction,
                "clauses_analysis": clauses_analysis,
            },
            "created_at":   job.created_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

        if job.status == JobStatus.COMPLETED:
            try:
                md_report  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
                response_data["report_md_base64"]  = base64.b64encode(md_report.encode()).decode()
            except Exception as e:
                logger.error("GET report generation failed for job %s: %s", job.id, e)
                response_data["report_error"] = f"Report could not be generated: {e}"

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
        context   = job.context
        logger.info(
            "Resuming job %s — retrying %d failed clause(s)",
            job_id, failed_clauses_qs.count(),
        )

        # ── Update job status ──────────────────────────────────────────────────
        job.status = JobStatus.ANALYZING
        job.save(update_fields=["status", "updated_at"])

        # ── Re-run failed clauses in parallel batches ─────────────────────────
        failed_list = list(failed_clauses_qs)
        all_items = [
            (str(jc.id), {"id": jc.clause_ref_id, "title": jc.title,
                           "value": jc.value, "category": jc.category})
            for jc in failed_list
        ]
        n_items   = len(all_items)
        n_workers  = 2 if n_items < 10 else (3 if n_items < 20 else _MAX_PARALLEL_CLAUSES)
        batch_size = max(1, min(math.ceil(n_items / n_workers), _CLAUSE_BATCH_SIZE))
        batches = [
            all_items[i:i + batch_size]
            for i in range(0, n_items, batch_size)
        ]

        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLAUSES) as executor:
            futures = {
                executor.submit(_analyze_batch_with_db, batch, full_text, context): batch
                for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    future.result()
                except Exception as e:
                    ids = [item[0] for item in batch]
                    logger.error("Unexpected resume batch error for clauses %s: %s", ids, e)

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

            try:
                md_report  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
                response_data["report_md_base64"]  = base64.b64encode(md_report.encode()).decode()
                logger.info("Resume markdown report generated for job %s", job.id)
            except Exception as e:
                logger.error("Resume report generation failed: %s", e)
                response_data["report_error"] = f"Report could not be generated: {e}"

            job.status       = JobStatus.COMPLETED
            job.completed_at = timezone.now()

        else:
            job.status = JobStatus.PARTIAL_FAILURE

        job.save(update_fields=["status", "completed_clauses", "failed_clauses", "completed_at", "updated_at"])

        response_data["status"]     = job.status.lower()
        response_data["can_resume"] = job.status == JobStatus.PARTIAL_FAILURE
        response_data["progress"]   = {
            "total":     job.total_clauses,
            "completed": job.completed_clauses,
            "failed":    job.failed_clauses,
        }

        return Response(response_data, status=status.HTTP_200_OK)

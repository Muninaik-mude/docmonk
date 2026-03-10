import base64
import binascii
import logging
from typing import cast

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PolicyAnalysisJob
from .serializers import PolicyAnalyzerSerializer
from .services import policy_service, policy_report_service
from analyzer.services import pdf_service, r2_service

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE_MB    = 100
_MAX_FILE_SIZE_BYTES = _MAX_FILE_SIZE_MB * 1024 * 1024


def _decode_base64_document(base64_string: str) -> bytes:
    """Decode a base64-encoded document string to raw bytes."""
    if "," in base64_string and base64_string.index(",") < 200:
        base64_string = base64_string.split(",", 1)[1]
    return base64.b64decode(base64_string.strip())


class PolicyAnalyzerView(APIView):
    """
    POST /v1/policy/analyze

    Analyze a subject document against a policy document.

    Accepts a document (base64 or presigned URL) and a policy_text string.
    Extracts text from the document, runs AI compliance analysis against the
    policy, generates an HTML report and summary, and returns the results.
    """

    def post(self, request):
        serializer = PolicyAnalyzerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_data = cast(dict, serializer.validated_data)

        doc_b64       = validated_data.get("document_base64") or ""
        doc_url       = validated_data.get("document_presigned_url") or ""
        doc_filename  = validated_data.get("document_filename") or "document"
        policy_type   = validated_data.get("policy_type") or ""
        policy_text   = validated_data.get("policy_text") or ""
        agreement_type    = validated_data.get("agreement_type") or ""
        agreement_details = validated_data.get("agreement_details")
        parties           = validated_data.get("parties")

        agreement_meta = {
            "agreement_type":    agreement_type,
            "agreement_details": agreement_details or {},
            "parties":           parties or {},
        }

        # ── Create DB record ───────────────────────────────────────────────────
        job = PolicyAnalysisJob.objects.create(
            status="in_progress",
            document_filename=doc_filename,
            policy_type=policy_type,
            policy_text=policy_text,
            agreement_type=agreement_type,
            agreement_details=agreement_details,
            parties=parties,
        )

        # ── Step 1: Get document bytes ─────────────────────────────────────────
        if doc_b64:
            try:
                doc_bytes = _decode_base64_document(doc_b64)
                logger.info(
                    "Policy job %s: document decoded from base64 (%d bytes)",
                    job.job_id, len(doc_bytes),
                )
            except (binascii.Error, ValueError) as e:
                logger.error("Policy job %s: base64 decode failed: %s", job.job_id, e)
                job.status = "failed"
                job.error_message = f"Invalid base64 document: {e}"
                job.save(update_fields=["status", "error_message", "updated_at"])
                return Response(
                    {"status": "error", "job_id": str(job.job_id),
                     "message": f"Invalid base64 document: {e}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            try:
                doc_bytes = r2_service.download_document_from_presigned_url(doc_url)
                logger.info(
                    "Policy job %s: document downloaded (%d bytes)",
                    job.job_id, len(doc_bytes),
                )
            except Exception as e:
                logger.error("Policy job %s: document download failed: %s", job.job_id, e)
                job.status = "failed"
                job.error_message = f"Failed to download document: {e}"
                job.save(update_fields=["status", "error_message", "updated_at"])
                return Response(
                    {"status": "error", "job_id": str(job.job_id),
                     "message": f"Failed to download document: {e}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Validate file size
        file_size_mb = len(doc_bytes) / (1024 * 1024)
        if len(doc_bytes) > _MAX_FILE_SIZE_BYTES:
            msg = f"File size {file_size_mb:.2f} MB exceeds {_MAX_FILE_SIZE_MB} MB limit"
            job.status = "failed"
            job.error_message = msg
            job.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {"status": "error", "job_id": str(job.job_id), "message": msg},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        # ── Step 2: Extract text ───────────────────────────────────────────────
        type_hint   = doc_url or doc_filename
        file_type   = pdf_service.detect_file_type(type_hint, doc_bytes)
        text_blocks = pdf_service.extract_text_blocks(type_hint, doc_bytes)
        full_text   = pdf_service.get_full_text(text_blocks)
        logger.info(
            "Policy job %s: extracted %d text blocks from %s",
            job.job_id, len(text_blocks), file_type,
        )

        if not full_text.strip():
            job.status = "failed"
            job.error_message = "Document contains no extractable text"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return Response(
                {"status": "error", "job_id": str(job.job_id),
                 "message": "Document contains no extractable text"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist extracted text for auditability
        job.full_text = full_text
        job.save(update_fields=["full_text", "updated_at"])

        # ── Step 3: AI analysis ────────────────────────────────────────────────
        logger.info(
            "Policy job %s: running policy analysis (policy_type=%r)",
            job.job_id, policy_type,
        )
        policy_analysis = policy_service.analyze_document_against_policy(
            full_text, policy_type, policy_text
        )
        logger.info(
            "Policy job %s: analysis complete — verdict=%s, score=%s",
            job.job_id,
            policy_analysis.get("overall_verdict"),
            policy_analysis.get("compliance_score"),
        )

        job.policy_result = policy_analysis
        job.save(update_fields=["policy_result", "updated_at"])

        # ── Step 4: Generate reports ───────────────────────────────────────────
        response_data: dict = {
            "status": "completed",
            "job_id": str(job.job_id),
        }

        try:
            md_report = policy_report_service.generate_policy_report(
                policy_analysis,
                policy_type=policy_type,
                agreement_meta=agreement_meta,
                document_text=full_text,
                doc_bytes=doc_bytes,
                file_type=file_type,
            )
            md_summary = policy_report_service.generate_policy_summary(
                policy_analysis,
                policy_type=policy_type,
                jurisdiction_info={},
                agreement_meta=agreement_meta,
            )
            summary_json = policy_report_service.build_policy_summary_json(
                policy_analysis,
                policy_type=policy_type,
                jurisdiction_info={},
                agreement_meta=agreement_meta,
            )
            # Merge structured summary fields into policy_analysis so the
            # frontend receives a single, unified analysis object.
            policy_analysis.update(summary_json)
            response_data["report_md_base64"]  = base64.b64encode(md_report.encode()).decode()
            response_data["summary_md_base64"] = base64.b64encode(md_summary.encode()).decode()
            response_data["policy_analysis"]   = policy_analysis
            logger.info("Policy job %s: report + summary generated", job.job_id)
        except Exception as e:
            logger.error("Policy job %s: report generation failed: %s", job.job_id, e)
            response_data["report_error"] = f"Report could not be generated: {e}"
            response_data["policy_analysis"] = policy_analysis

        # ── Finalize ───────────────────────────────────────────────────────────
        job.status     = "completed"
        job.updated_at = timezone.now()
        job.save(update_fields=["status", "updated_at"])

        return Response(response_data, status=status.HTTP_200_OK)

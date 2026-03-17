import base64
import binascii
import logging
from typing import cast

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PolicyAnalysisJob, PolicyRuleExtractionJob
from .serializers import PolicyAnalyzerSerializer, PolicyRuleExtractSerializer
from .services import policy_service, policy_report_service, policy_rule_service
from analyzer.services import pdf_service, r2_service

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE_MB    = 100
_MAX_FILE_SIZE_BYTES = _MAX_FILE_SIZE_MB * 1024 * 1024


def _decode_base64_document(base64_string: str) -> bytes:
    """Decode a base64-encoded document string to raw bytes."""
    if "," in base64_string and base64_string.index(",") < 200:
        base64_string = base64_string.split(",", 1)[1]
    return base64.b64decode(base64_string.strip())


def _get_doc_bytes(validated_data: dict, job_id) -> tuple[bytes | None, Response | None]:
    """
    Resolve document bytes from either base64 or presigned URL.
    Returns (doc_bytes, None) on success, (None, error_response) on failure.
    """
    doc_b64 = validated_data.get("document_base64") or ""
    doc_url = validated_data.get("document_presigned_url") or ""

    if doc_b64:
        try:
            doc_bytes = _decode_base64_document(doc_b64)
            logger.info("Policy job %s: document decoded from base64 (%d bytes)", job_id, len(doc_bytes))
            return doc_bytes, None
        except (binascii.Error, ValueError) as e:
            logger.error("Policy job %s: base64 decode failed: %s", job_id, e)
            return None, Response(
                {"status": "error", "job_id": str(job_id), "message": f"Invalid base64 document: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        try:
            doc_bytes = r2_service.download_document_from_presigned_url(doc_url)
            logger.info("Policy job %s: document downloaded (%d bytes)", job_id, len(doc_bytes))
            return doc_bytes, None
        except Exception as e:
            logger.error("Policy job %s: document download failed: %s", job_id, e)
            return None, Response(
                {"status": "error", "job_id": str(job_id), "message": f"Failed to download document: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


def _check_file_size(doc_bytes: bytes, job_id) -> Response | None:
    """Return a 413 error response if the file exceeds the size limit, else None."""
    if len(doc_bytes) > _MAX_FILE_SIZE_BYTES:
        msg = f"File size {len(doc_bytes) / 1024 / 1024:.2f} MB exceeds {_MAX_FILE_SIZE_MB} MB limit"
        return Response(
            {"status": "error", "job_id": str(job_id), "message": msg},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    return None


def _extract_text(doc_bytes: bytes, type_hint: str, job_id) -> tuple[str, str, Response | None]:
    """
    Extract text from doc_bytes.
    Returns (full_text, file_type, None) on success, ("", "", error_response) on failure.
    """
    file_type   = pdf_service.detect_file_type(type_hint, doc_bytes)
    text_blocks = pdf_service.extract_text_blocks(type_hint, doc_bytes)
    full_text   = pdf_service.get_full_text(text_blocks)
    logger.info("Policy job %s: extracted %d text blocks from %s", job_id, len(text_blocks), file_type)

    if not full_text.strip():
        return "", "", Response(
            {"status": "error", "job_id": str(job_id), "message": "Document contains no extractable text"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return full_text, file_type, None


# ── View 1: Rule Extraction ────────────────────────────────────────────────────

class PolicyRuleExtractView(APIView):
    """
    POST /v1/policy/extract-rules

    Upload a policy/rule document and receive structured atomic rules extracted
    from it, plus an interactive HTML preview of the document with each extracted
    rule highlighted in place.

    Request (JSON):
        document_base64 | document_presigned_url  — the rule/policy document
        document_filename                          — original filename (optional)
        policy_type                                — e.g. "Personal Loan" (optional)

    Response (200):
        job_id                  — UUID for auditability
        status                  — "completed"
        policy_type             — echoed back
        document_filename       — echoed back
        extraction_summary      — { total_rules, categories }
        rules                   — list of atomic rule objects
        rule_document_html_base64 — base64-encoded interactive HTML preview
    """

    def post(self, request):
        serializer = PolicyRuleExtractSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_data = cast(dict, serializer.validated_data)
        doc_filename = validated_data.get("document_filename") or "document"
        policy_type  = validated_data.get("policy_type") or ""
        type_hint    = validated_data.get("document_presigned_url") or doc_filename

        # ── Create DB record ───────────────────────────────────────────────────
        with transaction.atomic():
            job = PolicyRuleExtractionJob.objects.create(
                status="in_progress",
                document_filename=doc_filename,
                policy_type=policy_type,
            )

        # ── Step 1: Resolve document bytes ─────────────────────────────────────
        doc_bytes, err = _get_doc_bytes(validated_data, job.job_id)
        if err:
            job.status = "failed"
            job.error_message = "Document retrieval failed"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return err

        size_err = _check_file_size(doc_bytes, job.job_id)
        if size_err:
            job.status = "failed"
            job.error_message = "File size exceeded"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return size_err

        # ── Step 2: Extract text ───────────────────────────────────────────────
        full_text, file_type, text_err = _extract_text(doc_bytes, type_hint, job.job_id)
        if text_err:
            job.status = "failed"
            job.error_message = "No extractable text"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return text_err

        job.full_text = full_text
        job.save(update_fields=["full_text", "updated_at"])

        # ── Step 3: AI rule extraction ─────────────────────────────────────────
        logger.info(
            "Policy extraction job %s: extracting rules (policy_type=%r, ~%d chars)",
            job.job_id, policy_type, len(full_text),
        )
        extraction_result = policy_rule_service.extract_rules_from_policy(
            full_text, policy_type, doc_filename
        )
        rules = extraction_result.get("rules", [])
        logger.info(
            "Policy extraction job %s: extracted %d rules",
            job.job_id, len(rules),
        )

        job.extracted_rules = rules
        job.save(update_fields=["extracted_rules", "updated_at"])

        # ── Step 4: Generate rule-document HTML preview ───────────────────────
        response_data: dict = {
            "status":             "completed",
            "job_id":             str(job.job_id),
            "policy_type":        policy_type,
            "document_filename":  doc_filename,
            "extraction_summary": extraction_result.get("extraction_summary", {}),
            "rules":              rules,
        }

        try:
            rule_html = policy_report_service.generate_rule_extraction_report(
                rules,
                policy_type=policy_type,
                doc_bytes=doc_bytes,
                document_text=full_text,
                file_type=file_type,
                document_filename=doc_filename,
            )
            response_data["rule_document_html_base64"] = (
                base64.b64encode(rule_html.encode()).decode()
            )
            logger.info("Policy extraction job %s: HTML preview generated", job.job_id)
        except Exception as e:
            logger.error(
                "Policy extraction job %s: HTML preview generation failed: %s",
                job.job_id, e,
            )
            response_data["preview_error"] = "Preview could not be generated."

        # ── Finalize ───────────────────────────────────────────────────────────
        with transaction.atomic():
            job.status = "completed"
            job.save(update_fields=["status", "updated_at"])

        return Response(response_data, status=status.HTTP_200_OK)


# ── View 2: Document Analysis ──────────────────────────────────────────────────

class PolicyAnalyzerView(APIView):
    """
    POST /v1/policy/analyze

    Analyze a loan/subject document against a policy.

    Accepts either:
        rules       — structured rule objects returned by /extract-rules  (preferred)
        policy_text — raw policy document text                            (legacy)

    Request (JSON):
        document_base64 | document_presigned_url
        document_filename
        policy_type
        rules           — list of rule objects from /extract-rules
        policy_text     — raw policy text (used only when rules is absent)
        agreement_type, agreement_details, parties  — optional metadata

    Response (200):
        job_id, status, report_md_base64, policy_analysis
    """

    def post(self, request):
        serializer = PolicyAnalyzerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_data = cast(dict, serializer.validated_data)

        doc_filename      = validated_data.get("document_filename") or "document"
        policy_type       = validated_data.get("policy_type") or ""
        rules             = validated_data.get("rules") or []
        policy_text       = validated_data.get("policy_text") or ""
        agreement_type    = validated_data.get("agreement_type") or ""
        agreement_details = validated_data.get("agreement_details")
        parties           = validated_data.get("parties")
        type_hint         = validated_data.get("document_presigned_url") or doc_filename

        agreement_meta = {
            "agreement_type":    agreement_type,
            "agreement_details": agreement_details or {},
            "parties":           parties or {},
        }

        # ── Create DB record ───────────────────────────────────────────────────
        with transaction.atomic():
            job = PolicyAnalysisJob.objects.create(
                status="in_progress",
                document_filename=doc_filename,
                policy_type=policy_type,
                policy_text=policy_text,
                agreement_type=agreement_type,
                agreement_details=agreement_details,
                parties=parties,
            )

        # ── Step 1: Resolve document bytes ─────────────────────────────────────
        doc_bytes, err = _get_doc_bytes(validated_data, job.job_id)
        if err:
            job.status = "failed"
            job.error_message = "Document retrieval failed"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return err

        size_err = _check_file_size(doc_bytes, job.job_id)
        if size_err:
            job.status = "failed"
            job.error_message = "File size exceeded"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return size_err

        # ── Step 2: Extract text ───────────────────────────────────────────────
        full_text, file_type, text_err = _extract_text(doc_bytes, type_hint, job.job_id)
        if text_err:
            job.status = "failed"
            job.error_message = "No extractable text"
            job.save(update_fields=["status", "error_message", "updated_at"])
            return text_err

        job.full_text = full_text
        job.save(update_fields=["full_text", "updated_at"])

        # ── Step 3: AI analysis — rules path (preferred) or policy_text (legacy) ─
        if rules:
            logger.info(
                "Policy job %s: running rules-based analysis (%d rules, policy_type=%r)",
                job.job_id, len(rules), policy_type,
            )
            policy_analysis = policy_service.analyze_document_against_rules(
                full_text, policy_type, rules
            )
        else:
            logger.info(
                "Policy job %s: running policy-text analysis (policy_type=%r)",
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
            summary_json = policy_report_service.build_policy_summary_json(
                policy_analysis,
                policy_type=policy_type,
                jurisdiction_info={},
                agreement_meta=agreement_meta,
            )
            policy_analysis.update(summary_json)
            response_data["report_md_base64"] = base64.b64encode(md_report.encode()).decode()
            response_data["policy_analysis"]  = policy_analysis
            logger.info("Policy job %s: report generated", job.job_id)
        except Exception as e:
            logger.error("Policy job %s: report generation failed: %s", job.job_id, e)
            response_data["report_error"]    = "Report could not be generated."
            response_data["policy_analysis"] = policy_analysis

        # ── Finalize ───────────────────────────────────────────────────────────
        with transaction.atomic():
            job.status = "completed"
            job.save(update_fields=["status", "updated_at"])

        return Response(response_data, status=status.HTTP_200_OK)

import logging
import time
import uuid

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ClauseAnalyzerSerializer, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from .services import r2_service, pdf_service, groq_service, report_service
from .utils.color_constants import STATUS_HIGHLIGHT_COLOR, STATUS_INSERTION_COLOR

logger = logging.getLogger(__name__)


def _upload_or_save(file_bytes: bytes, filename: str, prefix: str, content_type: str) -> dict:
    """Upload file to R2 or save locally. Returns {url, expires_in}."""
    if r2_service.is_r2_configured():
        object_key = r2_service.upload_file_to_r2(file_bytes, filename, prefix, content_type)
        url = r2_service.generate_presigned_download_url(object_key)
        return {"url": url, "expires_in": "24 hour"}
    else:
        r2_service.save_file_locally(file_bytes, filename)
        return {"url": f"/annotated_pdfs/{filename}", "expires_in": "local file (no expiry)"}


class ClauseAnalyzerView(APIView):
    """
    POST /api/analyze/

    Orchestrates the full clause compliance analysis pipeline:
    1. Validate request
    2. Download PDF from presigned URL
    3. Extract text from PDF
    4. Detect jurisdiction (one AI call)
    5. Analyze each clause via Groq AI + annotate original PDF
    6. Detect cross-clause conflicts (one AI call)
    7. Generate report (redline) + summary (analytics)
    8. Upload report(s) to S3/R2 or save locally
    9. Return analysis_summary + report/summary download URL(s) + conflicts + jurisdiction
    """

    def post(self, request):
        serializer = ClauseAnalyzerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        doc_url = serializer.validated_data["document_presigned_url"]
        clauses = serializer.validated_data["clauses"]
        report_format = serializer.validated_data.get("report_format", "pdf")

        # Agreement metadata (all optional)
        agreement_meta = {
            "agreement_type":    serializer.validated_data.get("agreement_type", ""),
            "agreement_details": serializer.validated_data.get("agreement_details") or {},
            "parties":           serializer.validated_data.get("parties") or {},
            "property":          serializer.validated_data.get("property") or {},
        }

        # Step 1: Download document (PDF, DOCX, Markdown, TXT)
        try:
            doc_bytes = r2_service.download_document_from_presigned_url(doc_url)
            logger.info("Document downloaded successfully (%d bytes)", len(doc_bytes))
        except Exception as e:
            logger.error("Document download failed: %s", e)
            return Response(
                {"status": "error", "message": f"Failed to download document: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate file size (max 100 MB)
        file_size_mb = len(doc_bytes) / (1024 * 1024)
        if len(doc_bytes) > MAX_FILE_SIZE_BYTES:
            return Response(
                {
                    "status": "error",
                    "message": (
                        f"File size {file_size_mb:.2f} MB exceeds "
                        f"the maximum allowed size of {MAX_FILE_SIZE_MB} MB."
                    ),
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        # Step 2: Detect file type and extract text
        file_type = pdf_service.detect_file_type(doc_url, doc_bytes)
        text_blocks = pdf_service.extract_text_blocks(doc_url, doc_bytes)
        full_text = pdf_service.get_full_text(text_blocks)
        logger.info("Extracted %d text blocks from %s", len(text_blocks), file_type)

        if not full_text.strip():
            return Response(
                {"status": "error", "message": "PDF contains no extractable text"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Step 3: Detect jurisdiction (one AI call using first 3000 chars)
        jurisdiction_info = groq_service.detect_jurisdiction(full_text)
        logger.info("Jurisdiction detected: %s", jurisdiction_info.get("jurisdiction"))

        # Step 4: Analyze each clause via Groq + build annotation list
        analysis_summary = []
        annotations = []

        for clause in clauses:
            try:
                ai_result = groq_service.analyze_clause_against_pdf(clause, full_text)
            except Exception as e:
                logger.error("Groq API failed for clause %s: %s", clause["id"], e)
                return Response(
                    {"status": "error", "message": f"AI analysis failed: {str(e)}"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            ai_result.pop("_provider", None)

            result_status = ai_result["result"]
            highlight_color = STATUS_HIGHLIGHT_COLOR.get(result_status)
            insertion_color = STATUS_INSERTION_COLOR.get(result_status)
            ai_text = ai_result.get("ai_recommendation")

            summary_entry = {
                "clause_id": clause["id"],
                "clause_title": clause["title"],
                "clause_content": clause.get("content") or clause.get("value", ""),
                "result": result_status,
                "reason": ai_result.get("reason"),
                "relevant_text": ai_result.get("relevant_text"),
                "color": None,
                "ai_added_text": None,
                "parties_obligated": ai_result.get("parties_obligated", []),
                "missing_values": ai_result.get("missing_values", []),
                "binding_strength": ai_result.get("binding_strength", "VAGUE"),
                "key_dates_durations": ai_result.get("key_dates_durations", []),
            }

            if result_status == "NOT_FOUND":
                summary_entry["color"] = "blue"
                summary_entry["ai_added_text"] = ai_text
                if text_blocks:
                    last_block = text_blocks[-1]
                    annotations.append({
                        "page_num": last_block["page_num"],
                        "bbox": last_block["bbox"],
                        "highlight_color": highlight_color,
                        "inserted_text": f"[MISSING - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            elif result_status == "PARTIALLY_SATISFIED":
                summary_entry["color"] = "orange"
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
                        "page_num": location["page_num"],
                        "bbox": location["bbox"],
                        "highlight_color": highlight_color,
                        "inserted_text": f"[PARTIAL - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            elif result_status == "VIOLATION":
                summary_entry["color"] = "red"
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
                        "page_num": location["page_num"],
                        "bbox": location["bbox"],
                        "highlight_color": highlight_color,
                        "inserted_text": f"[CORRECTION - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            analysis_summary.append(summary_entry)
            time.sleep(0.5)

        # Step 5: Detect cross-clause conflicts (one AI call)
        conflicts = groq_service.detect_conflicts(analysis_summary)
        logger.info("Detected %d clause conflict(s)", len(conflicts))

        # Step 6: Generate report(s) + summary from analysis_summary
        run_id = uuid.uuid4()
        response_data = {
            "status": "success",
            "analysis_summary": analysis_summary,
            "conflicts": conflicts,
            "jurisdiction": jurisdiction_info,
        }

        report_kwargs = dict(
            conflicts=conflicts,
            jurisdiction_info=jurisdiction_info,
            full_text=full_text,
            agreement_meta=agreement_meta,
        )

        try:
            if report_format in ("pdf", "both"):
                # Redline report
                pdf_report_bytes = report_service.generate_pdf_report(
                    analysis_summary, **report_kwargs,
                )
                pdf_report_filename = f"report_{run_id}.pdf"
                result = _upload_or_save(
                    pdf_report_bytes, pdf_report_filename,
                    prefix="reports", content_type="application/pdf",
                )
                response_data["report_pdf_url"] = result["url"]
                response_data["report_pdf_expires_in"] = result["expires_in"]
                logger.info("PDF report generated: %s", pdf_report_filename)

                # Analytics summary
                pdf_summary_bytes = report_service.generate_pdf_summary(
                    analysis_summary, **report_kwargs,
                )
                pdf_summary_filename = f"summary_{run_id}.pdf"
                result = _upload_or_save(
                    pdf_summary_bytes, pdf_summary_filename,
                    prefix="reports", content_type="application/pdf",
                )
                response_data["summary_pdf_url"] = result["url"]
                response_data["summary_pdf_expires_in"] = result["expires_in"]
                logger.info("PDF summary generated: %s", pdf_summary_filename)

            if report_format in ("markdown", "both"):
                # Redline report
                md_content = report_service.generate_markdown_report(
                    analysis_summary, **report_kwargs,
                )
                md_bytes = md_content.encode("utf-8")
                md_filename = f"report_{run_id}.md"
                result = _upload_or_save(
                    md_bytes, md_filename,
                    prefix="reports", content_type="text/markdown",
                )
                response_data["report_markdown_url"] = result["url"]
                response_data["report_markdown_expires_in"] = result["expires_in"]
                logger.info("Markdown report generated: %s", md_filename)

                # Analytics summary
                md_summary = report_service.generate_markdown_summary(
                    analysis_summary, **report_kwargs,
                )
                md_summary_bytes = md_summary.encode("utf-8")
                md_summary_filename = f"summary_{run_id}.md"
                result = _upload_or_save(
                    md_summary_bytes, md_summary_filename,
                    prefix="reports", content_type="text/markdown",
                )
                response_data["summary_markdown_url"] = result["url"]
                response_data["summary_markdown_expires_in"] = result["expires_in"]
                logger.info("Markdown summary generated: %s", md_summary_filename)

            if report_format == "docx":
                # Redline report
                docx_bytes = report_service.generate_docx_report(
                    analysis_summary, **report_kwargs,
                )
                docx_filename = f"report_{run_id}.docx"
                result = _upload_or_save(
                    docx_bytes, docx_filename,
                    prefix="reports",
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                response_data["report_docx_url"] = result["url"]
                response_data["report_docx_expires_in"] = result["expires_in"]
                logger.info("DOCX report generated: %s", docx_filename)

                # Analytics summary
                docx_summary_bytes = report_service.generate_docx_summary(
                    analysis_summary, **report_kwargs,
                )
                docx_summary_filename = f"summary_{run_id}.docx"
                result = _upload_or_save(
                    docx_summary_bytes, docx_summary_filename,
                    prefix="reports",
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                response_data["summary_docx_url"] = result["url"]
                response_data["summary_docx_expires_in"] = result["expires_in"]
                logger.info("DOCX summary generated: %s", docx_summary_filename)

        except Exception as e:
            logger.error("Report generation/upload failed: %s", e)
            response_data["report_error"] = f"Report could not be generated/uploaded: {str(e)}"

        return Response(response_data, status=status.HTTP_200_OK)

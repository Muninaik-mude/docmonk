import base64
import binascii
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ClauseAnalyzerSerializer, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_MB
from .services import r2_service, pdf_service, groq_service, report_service
from .utils.color_constants import STATUS_HIGHLIGHT_COLOR, STATUS_INSERTION_COLOR

logger = logging.getLogger(__name__)

# Max parallel clause analyses — each clause makes 2 sequential AI calls (extract + analyze).
# With 4 pooled providers, 3 workers = up to 6 concurrent calls spread evenly across keys.
_MAX_PARALLEL_CLAUSES = 3


def _upload_or_save(file_bytes: bytes, filename: str, prefix: str, content_type: str) -> dict:
    """Upload file to R2 or save locally. Returns {url, expires_in}."""
    if r2_service.is_r2_configured():
        object_key = r2_service.upload_file_to_r2(file_bytes, filename, prefix, content_type)
        url = r2_service.generate_presigned_download_url(object_key)
        return {"url": url, "expires_in": "24 hour"}
    else:
        r2_service.save_file_locally(file_bytes, filename)
        return {"url": f"/annotated_pdfs/{filename}", "expires_in": "local file (no expiry)"}


def _decode_base64_document(base64_string: str) -> bytes:
    """Decode a base64-encoded document string to raw bytes."""
    # Strip optional data URI prefix: "data:application/pdf;base64,..."
    if "," in base64_string and base64_string.index(",") < 200:
        base64_string = base64_string.split(",", 1)[1]
    # Strip whitespace/newlines
    base64_string = base64_string.strip()
    return base64.b64decode(base64_string)


def _analyze_single_clause(clause: dict, full_text: str) -> dict:
    """Analyze one clause — called by ThreadPoolExecutor."""
    ai_result = groq_service.analyze_clause_against_pdf(clause, full_text)
    return {"clause": clause, "ai_result": ai_result}


class ClauseAnalyzerView(APIView):
    """
    POST /api/analyze/

    Orchestrates the full clause compliance analysis pipeline:
    1. Validate request
    2. Get document bytes (from presigned URL or base64)
    3. Extract text from PDF/DOCX/MD/TXT
    4. Detect jurisdiction (one AI call)
    5. Analyze clauses in parallel via Groq AI + annotate original PDF
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

        doc_url = serializer.validated_data.get("document_presigned_url")
        doc_b64 = serializer.validated_data.get("document_base64")
        doc_filename = serializer.validated_data.get("document_filename", "document.pdf")
        clauses = serializer.validated_data["clauses"]
        report_format = serializer.validated_data.get("report_format", "markdown")

        # Agreement metadata (all optional)
        agreement_meta = {
            "agreement_type":    serializer.validated_data.get("agreement_type", ""),
            "agreement_details": serializer.validated_data.get("agreement_details") or {},
            "parties":           serializer.validated_data.get("parties") or {},
            "property":          serializer.validated_data.get("property") or {},
        }

        # Step 1: Get document bytes — from URL or base64
        if doc_b64:
            try:
                doc_bytes = _decode_base64_document(doc_b64)
                logger.info("Document decoded from base64 (%d bytes)", len(doc_bytes))
            except (binascii.Error, ValueError) as e:
                logger.error("Base64 decode failed: %s", e)
                return Response(
                    {"status": "error", "message": f"Invalid base64 document: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
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
        # For base64 input use filename for type detection; for URL use the URL
        type_hint = doc_url or doc_filename
        file_type = pdf_service.detect_file_type(type_hint, doc_bytes)
        text_blocks = pdf_service.extract_text_blocks(type_hint, doc_bytes)
        full_text = pdf_service.get_full_text(text_blocks)
        logger.info("Extracted %d text blocks from %s", len(text_blocks), file_type)

        if not full_text.strip():
            return Response(
                {"status": "error", "message": "Document contains no extractable text"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Step 3: Detect jurisdiction (one AI call using first 3000 chars)
        jurisdiction_info = groq_service.detect_jurisdiction(full_text)
        logger.info("Jurisdiction detected: %s", jurisdiction_info.get("jurisdiction"))

        # Step 4: Analyze clauses in PARALLEL via Groq
        clause_results = [None] * len(clauses)

        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CLAUSES) as executor:
            future_to_idx = {
                executor.submit(_analyze_single_clause, clause, full_text): idx
                for idx, clause in enumerate(clauses)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    clause_results[idx] = future.result()
                except Exception as e:
                    logger.error("Clause analysis thread failed for index %d: %s", idx, e)
                    clause_results[idx] = {
                        "clause": clauses[idx],
                        "ai_result": {
                            "result": "NOT_FOUND",
                            "reason": f"Analysis failed: {type(e).__name__}",
                            "relevant_text": None,
                            "ai_recommendation": f"Clause '{clauses[idx]['title']}' could not be analyzed. Manual review recommended.",
                            "parties_obligated": [],
                            "missing_values": [],
                            "binding_strength": "VAGUE",
                            "key_dates_durations": [],
                            "_provider": "groq",
                        },
                    }

        # Build analysis_summary + annotations from parallel results
        analysis_summary = []
        annotations = []

        for item in clause_results:
            clause = item["clause"]
            ai_result = item["ai_result"]
            ai_result.pop("_provider", None)

            result_status = ai_result["result"]
            highlight_color = STATUS_HIGHLIGHT_COLOR.get(result_status)
            insertion_color = STATUS_INSERTION_COLOR.get(result_status)
            ai_text = ai_result.get("ai_recommendation")

            summary_entry = {
                "clause_id": clause["id"],
                "clause_title": clause["title"],
                "clause_value": clause["value"],
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

        # Step 5: Conflict detection skipped — not currently used in reports
        conflicts = []

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
            # Always generate markdown report + summary and return as base64 in payload
            _md_report = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
            _md_summary = report_service.generate_markdown_summary(analysis_summary, **report_kwargs)
            response_data["report_md_base64"] = base64.b64encode(_md_report.encode("utf-8")).decode("utf-8")
            response_data["summary_md_base64"] = base64.b64encode(_md_summary.encode("utf-8")).decode("utf-8")

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

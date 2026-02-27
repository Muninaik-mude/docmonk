import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ClauseAnalyzerSerializer
from .services import pdf_service, groq_service, report_service
from .utils.color_constants import STATUS_HIGHLIGHT_COLOR, STATUS_INSERTION_COLOR

logger = logging.getLogger(__name__)

# 3 concurrent clause workers — safe for Groq's 8 000 TPM free-tier limit.
# Each clause call uses ~1 250 input tokens; 3 × 1 250 = 3 750 tokens/burst,
# well below the limit even after the upfront context call (~1 200 tokens).
_CLAUSE_WORKERS = 3


def _fallback_clause_result(clause: dict, error: Exception) -> dict:
    """Safe default returned when a clause analysis thread raises an exception."""
    return {
        "result":            "NOT_FOUND",
        "reason":            f"Analysis failed: {error}",
        "relevant_text":     None,
        "ai_recommendation": (
            f"Clause '{clause['title']}' could not be analyzed. "
            "Manual review recommended."
        ),
        "parties_obligated":   [],
        "missing_values":      [],
        "binding_strength":    "VAGUE",
        "key_dates_durations": [],
    }


class ClauseAnalyzerView(APIView):
    """
    POST /api/analyze/

    Payload:
        document_base64  – base64-encoded document (md / pdf / docx / txt)
        document_type    – "md" | "pdf" | "docx" | "txt"  (default "md")
        clauses          – list of clause objects  (required, max 100)
        report_format    – "markdown" | "pdf" | "docx" | "both"  (default "markdown")

    Pipeline:
        1. Decode base64 → raw bytes
        2. Extract text blocks (routed by document_type)
        3. ONE Groq call — extract metadata + jurisdiction together
        4. [PARALLEL, 3 workers] Per-clause analysis
              → smart retry: waits exactly as long as Groq's rate-limit header says
              → per-clause failures get a safe fallback, never abort the request
        5. Generate report(s) + summary
        6. Return base64-encoded outputs in response JSON
    """

    def post(self, request):
        serializer = ClauseAnalyzerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        doc_b64       = serializer.validated_data["document_base64"]
        document_type = serializer.validated_data.get("document_type", "md")
        clauses       = serializer.validated_data["clauses"]
        report_format = serializer.validated_data.get("report_format", "markdown")

        # ── Step 1: Decode base64 → bytes ────────────────────────────────────────
        try:
            doc_bytes = base64.b64decode(doc_b64)
            logger.info("Document decoded: %d bytes, type=%s", len(doc_bytes), document_type)
        except Exception as e:
            return Response(
                {"status": "error", "message": f"Failed to decode document_base64: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 2: Extract text ──────────────────────────────────────────────────
        text_blocks = pdf_service.extract_text_blocks_by_type(document_type, doc_bytes)
        full_text   = pdf_service.get_full_text(text_blocks)
        logger.info("Extracted %d text blocks", len(text_blocks))

        if not full_text.strip():
            return Response(
                {"status": "error", "message": "Document contains no extractable text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 3: ONE call — metadata + jurisdiction combined ───────────────────
        # Previously 2 parallel calls (~2 000 tokens burst).
        # Now 1 call (~1 200 tokens) before clause analysis starts.
        doc_context = groq_service.extract_document_context(full_text)

        agreement_meta = {
            "agreement_type":    doc_context.get("agreement_type", ""),
            "agreement_details": doc_context.get("agreement_details") or {},
            "parties":           doc_context.get("parties") or {},
            "property":          doc_context.get("property") or {},
        }
        jurisdiction_info = {
            "jurisdiction":    doc_context.get("jurisdiction", "Unknown"),
            "applicable_laws": doc_context.get("applicable_laws", []),
            "checklist":       doc_context.get("checklist", []),
        }
        logger.info(
            "Context extracted — agreement_type=%s  jurisdiction=%s",
            agreement_meta["agreement_type"],
            jurisdiction_info["jurisdiction"],
        )

        # ── Step 4: Per-clause analysis — PARALLEL (3 workers) ───────────────────
        workers = min(len(clauses), _CLAUSE_WORKERS)
        results_by_id: dict[str, dict] = {}

        with ThreadPoolExecutor(max_workers=workers) as clause_pool:
            future_to_clause = {
                clause_pool.submit(
                    groq_service.analyze_clause_against_pdf, clause, full_text
                ): clause
                for clause in clauses
            }

            for future in as_completed(future_to_clause):
                clause = future_to_clause[future]
                try:
                    results_by_id[clause["id"]] = future.result()
                except Exception as e:
                    logger.error(
                        "Clause '%s' (%s) failed after retries: %s",
                        clause["id"], clause["title"], e,
                    )
                    results_by_id[clause["id"]] = _fallback_clause_result(clause, e)

        logger.info("Clause analysis done (%d clauses, %d workers)", len(clauses), workers)

        # ── Build analysis_summary + annotations in original clause order ─────────
        analysis_summary: list[dict] = []
        annotations:      list[dict] = []

        for clause in clauses:
            ai_result = results_by_id[clause["id"]]
            ai_result.pop("_provider", None)

            result_status   = ai_result["result"]
            highlight_color = STATUS_HIGHLIGHT_COLOR.get(result_status)
            insertion_color = STATUS_INSERTION_COLOR.get(result_status)
            ai_text         = ai_result.get("ai_recommendation")

            summary_entry = {
                "clause_id":           clause["id"],
                "clause_title":        clause["title"],
                "clause_content":      clause.get("content") or clause.get("value", ""),
                "result":              result_status,
                "reason":              ai_result.get("reason"),
                "relevant_text":       ai_result.get("relevant_text"),
                "color":               None,
                "ai_added_text":       None,
                "parties_obligated":   ai_result.get("parties_obligated", []),
                "missing_values":      ai_result.get("missing_values", []),
                "binding_strength":    ai_result.get("binding_strength", "VAGUE"),
                "key_dates_durations": ai_result.get("key_dates_durations", []),
            }

            if result_status == "NOT_FOUND":
                summary_entry["color"]         = "blue"
                summary_entry["ai_added_text"] = ai_text
                if text_blocks:
                    last = text_blocks[-1]
                    annotations.append({
                        "page_num":            last["page_num"],
                        "bbox":                last["bbox"],
                        "highlight_color":     highlight_color,
                        "inserted_text":       f"[MISSING - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            elif result_status == "PARTIALLY_SATISFIED":
                summary_entry["color"]         = "orange"
                summary_entry["ai_added_text"] = ai_text
                relevant_text = ai_result.get("relevant_text")
                location = groq_service.find_text_location_in_pdf(text_blocks, relevant_text)
                if not location:
                    location = groq_service.find_text_location_in_pdf(text_blocks, clause["title"])
                if not location and text_blocks:
                    location = {"page_num": text_blocks[-1]["page_num"], "bbox": text_blocks[-1]["bbox"]}
                if location:
                    annotations.append({
                        "page_num":            location["page_num"],
                        "bbox":                location["bbox"],
                        "highlight_color":     highlight_color,
                        "inserted_text":       f"[PARTIAL - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            elif result_status == "VIOLATION":
                summary_entry["color"]         = "red"
                summary_entry["ai_added_text"] = ai_text
                relevant_text = ai_result.get("relevant_text")
                location = groq_service.find_text_location_in_pdf(text_blocks, relevant_text)
                if not location:
                    location = groq_service.find_text_location_in_pdf(text_blocks, clause["title"])
                if not location and text_blocks:
                    location = {"page_num": text_blocks[-1]["page_num"], "bbox": text_blocks[-1]["bbox"]}
                if location:
                    annotations.append({
                        "page_num":            location["page_num"],
                        "bbox":                location["bbox"],
                        "highlight_color":     highlight_color,
                        "inserted_text":       f"[CORRECTION - {clause['title']}]: {ai_text}" if ai_text else None,
                        "inserted_text_color": insertion_color,
                    })

            analysis_summary.append(summary_entry)

        # ── Step 5 & 6: Generate reports, encode as base64 ───────────────────────
        report_kwargs = dict(
            conflicts=[],           # conflict detection removed
            jurisdiction_info=jurisdiction_info,
            full_text=full_text,
            agreement_meta=agreement_meta,
        )

        response_data = {
            "status":            "success",
            "document_metadata": doc_context,
            "analysis_summary":  analysis_summary,
            "jurisdiction":      jurisdiction_info,
        }

        try:
            if report_format in ("markdown", "both"):
                md_report  = report_service.generate_markdown_report(analysis_summary, **report_kwargs)
                md_summary = report_service.generate_markdown_summary(analysis_summary, **report_kwargs)
                response_data["report_markdown_base64"]  = base64.b64encode(md_report.encode("utf-8")).decode("ascii")
                response_data["summary_markdown_base64"] = base64.b64encode(md_summary.encode("utf-8")).decode("ascii")
                logger.info("Markdown report generated and base64-encoded")

            if report_format in ("pdf", "both"):
                pdf_report  = report_service.generate_pdf_report(analysis_summary, **report_kwargs)
                pdf_summary = report_service.generate_pdf_summary(analysis_summary, **report_kwargs)
                response_data["report_pdf_base64"]  = base64.b64encode(pdf_report).decode("ascii")
                response_data["summary_pdf_base64"] = base64.b64encode(pdf_summary).decode("ascii")
                logger.info("PDF report generated and base64-encoded")

            if report_format == "docx":
                docx_report  = report_service.generate_docx_report(analysis_summary, **report_kwargs)
                docx_summary = report_service.generate_docx_summary(analysis_summary, **report_kwargs)
                response_data["report_docx_base64"]  = base64.b64encode(docx_report).decode("ascii")
                response_data["summary_docx_base64"] = base64.b64encode(docx_summary).decode("ascii")
                logger.info("DOCX report generated and base64-encoded")

        except Exception as e:
            logger.error("Report generation failed: %s", e)
            response_data["report_error"] = f"Report could not be generated: {e}"

        return Response(response_data, status=status.HTTP_200_OK)

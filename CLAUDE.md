# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DocMonk is a Django REST API that performs AI-powered legal document analysis. It extracts text from uploaded documents, uses the Groq API to check clauses for compliance, detects conflicts, and generates annotated reports in PDF, DOCX, or Markdown formats.

## Common Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
python manage.py runserver

# Apply migrations
python manage.py migrate

# Check configuration
python manage.py check

# Production server (as per Procfile)
gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 2 --timeout 120
```

No test suite is configured. There are no test files in this repository.

## Environment Variables

Copy `.env.example` to `.env`. Key variables:
- `GROQ_API_KEY` / `GROQ_MODEL` — Groq AI for legal clause analysis
- `STORAGE_BACKEND` — `"r2"` (Cloudflare) or `"local"` (falls back to `annotated_pdfs/`)
- `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` — R2 credentials when using cloud storage

## Architecture

The entire API is a single endpoint: **POST `/api/analyze/`** (see `analyzer/urls.py` and `analyzer/views.py`).

The view (`ClauseAnalyzerView`) orchestrates a pipeline across four services:

| Service | File | Responsibility |
|---|---|---|
| `pdf_service` | `analyzer/services/pdf_service.py` | Extract text from PDF, DOCX, Markdown, TXT; annotate PDFs with highlights |
| `groq_service` | `analyzer/services/groq_service.py` | AI analysis via Groq API — clause compliance, jurisdiction detection, conflict detection |
| `r2_service` | `analyzer/services/r2_service.py` | Upload/download files from Cloudflare R2 or local filesystem |
| `report_service` | `analyzer/services/report_service.py` | Generate PDF/DOCX/Markdown reports with color-coded compliance results |

### Analysis Pipeline

1. Download document from presigned URL (`r2_service`)
2. Extract full text (`pdf_service`)
3. Detect jurisdiction and applicable compliance requirements (`groq_service.detect_jurisdiction`)
4. For each clause: analyze compliance against document text (`groq_service.analyze_clause_against_pdf`)
5. Detect cross-clause conflicts (`groq_service.detect_conflicts`)
6. Annotate original PDF with color-coded highlights (`pdf_service`)
7. Generate report(s) in requested format(s) (`report_service`)
8. Upload results and return presigned download URLs (`r2_service`)

### Compliance Status Values

Defined in `analyzer/utils/color_constants.py`:
- `MATCH` — Fully satisfied (green)
- `NOT_FOUND` — Absent from document (blue)
- `PARTIALLY_SATISFIED` — Vague or incomplete (orange)
- `VIOLATION` — Contradicts requirement (red)

### AI Response Handling

`groq_service.py` includes JSON repair logic for truncated Groq API responses. It uses intelligent excerpt selection (5,000-char window centered on relevant keywords) to stay within token limits for large documents. All Groq responses are validated and defaulted for missing fields before use.

### Storage Abstraction

`r2_service.py` provides a dual-backend storage layer. Setting `STORAGE_BACKEND=local` stores files in `annotated_pdfs/` instead of R2, which is useful for development without cloud credentials.

## Request Format

```json
{
  "document_presigned_url": "https://...",
  "agreement_type": "Commercial Rental Agreement",
  "agreement_details": { "agreement_date": "...", "city": "...", "state": "..." },
  "parties": { "landlord": {...}, "tenant": {...} },
  "property": { "type": "...", "area_sqft": 5000, "address": "..." },
  "clauses": [
    { "id": "clause_1", "title": "Rent Payment", "content": "..." }
  ],
  "report_format": "pdf"
}
```

`report_format` accepts `"pdf"`, `"docx"`, `"markdown"`, or `"both"`. The field `pdf_presigned_url` is also accepted as a legacy alias for `document_presigned_url`.

## Deployment

Deployed on Leapcell. The `Procfile` defines the web process. Static files are served via WhiteNoise. No database migrations beyond the default Django tables are required.

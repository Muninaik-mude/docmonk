# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DocMonk is a Django REST API that performs AI-powered legal document services:
- **Clause Analysis** — check uploaded document clauses for compliance, generate color-coded reports
- **Document Q&A** *(planned)* — ask freeform questions on an uploaded document
- **Contract Generation** *(planned)* — generate a contract document from structured party/property details

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

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | `True` / `False` |
| `ALLOWED_HOSTS` | Comma-separated host list |
| `DATABASE_URL` | PostgreSQL connection string (`postgres://...`). Falls back to SQLite if unset. |
| `STORAGE_BACKEND` | `"r2"` (Cloudflare R2) or `"local"` (writes to `annotated_pdfs/`) |
| `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` | R2 credentials |
| `GROQ_API_KEY_1`, `GROQ_API_KEY_2` | Groq API keys (pool of 2) |
| `CEREBRAS_KEY_1`, `CEREBRAS_KEY_2` | Cerebras API keys (pool of 2) |
| `GROQ_MODEL` | Model ID (default: `llama-3.3-70b-versatile`) |

## Architecture

### App Structure

```
analyzer/
├── models.py          — DB models (AnalysisJob, JobClause, ClauseResult, ...)
├── serializers.py     — DRF serializers for request validation
├── views.py           — ClauseAnalyzerView (POST /v1/analyze)
├── job_views.py       — JobStatusView (GET), JobResumeView (POST resume)
├── urls.py            — URL routing
├── services/
│   ├── groq_service.py    — AI calls: jurisdiction detection, clause analysis, conflict detection
│   ├── pdf_service.py     — Text extraction (PDF/DOCX/MD/TXT), PDF annotation
│   ├── r2_service.py      — Cloudflare R2 / local file storage
│   └── report_service.py  — PDF/DOCX/Markdown report + summary generation
└── utils/
    └── color_constants.py — Compliance status → highlight/insertion color maps
```

### Services

| Service | File | Responsibility |
|---|---|---|
| `pdf_service` | `analyzer/services/pdf_service.py` | Extract text from PDF, DOCX, Markdown, TXT; annotate PDFs with highlights |
| `groq_service` | `analyzer/services/groq_service.py` | AI analysis — clause compliance, jurisdiction detection, conflict detection (dormant) |
| `r2_service` | `analyzer/services/r2_service.py` | Upload/download files from Cloudflare R2 or local filesystem |
| `report_service` | `analyzer/services/report_service.py` | Generate PDF/DOCX/Markdown report + summary with color-coded results |

### AI Provider Pool

`groq_service.py` maintains a pool of 4 clients (Groq key 1, Groq key 2, Cerebras key 1, Cerebras key 2). Calls round-robin across available clients. Includes JSON repair logic for truncated responses and intelligent 5,000-char context window excerpt selection.

**Conflict detection** (`groq_service.detect_conflicts`) is implemented but intentionally NOT called — too expensive per request. Will be wired in on demand.

## Database Models

| Model | Table | Purpose |
|---|---|---|
| `AnalysisJob` | `analysis_jobs` | Top-level job: status machine, counters, stored full text |
| `JobClause` | `job_clauses` | One row per clause; state machine (PENDING → IN_PROGRESS → COMPLETED/FAILED) |
| `ClauseResult` | `clause_results` | AI output for a completed clause |
| `JobJurisdiction` | `job_jurisdiction` | Jurisdiction + applicable laws + checklist |
| `ClauseConflict` | `clause_conflicts` | Conflict pairs (populated only if conflict detection is enabled) |
| `JobReport` | `job_reports` | Stored report file keys + presigned URLs |

## API Endpoints

### POST `/v1/analyze`

Full analysis pipeline. Returns only lightweight response — no analysis_summary, no report URLs.

**Request** (JSON):
```json
{
  "document_base64": "<base64-encoded document>",
  "document_filename": "contract.pdf",
  "agreement_type": "Commercial Rental Agreement",
  "agreement_details": { "agreement_date": "...", "city": "...", "state": "..." },
  "parties": { "landlord": {...}, "tenant": {...} },
  "property": { "type": "...", "area_sqft": 5000, "address": "..." },
  "clauses": [
    {
      "id": "confidentiality_survival_clause",
      "category": "confidentiality",
      "title": "Post-Termination Confidentiality",
      "value": "Confidentiality obligations shall survive termination..."
    }
  ],
  "report_format": "markdown"
}
```

`document_presigned_url` / `pdf_presigned_url` are also accepted (legacy aliases). `report_format` accepts `"pdf"`, `"docx"`, `"markdown"`, or `"both"`.

**Response** (slim — for speed):
```json
{
  "job_id": "uuid",
  "status": "completed",
  "can_resume": false,
  "progress": { "total": 5, "completed": 5, "failed": 0 },
  "report_md_base64": "<base64>",
  "summary_md_base64": "<base64>"
}
```

### GET `/v1/jobs/{job_id}`

Poll job state + retrieve full results including analysis_summary (with jurisdiction embedded per entry) and report URLs.

### POST `/v1/jobs/{job_id}/resume`

Re-run only FAILED/PENDING clauses. Uses `full_text` stored in DB — no re-download. Only works when job is in `PARTIAL_FAILURE` state.

## Analysis Pipeline

1. Decode base64 / download document from presigned URL
2. Extract full text (`pdf_service`)
3. Detect jurisdiction + applicable laws (`groq_service.detect_jurisdiction`)
4. Analyze each clause in parallel — 3 workers (`groq_service.analyze_clause_against_pdf`)
5. ~~Detect cross-clause conflicts~~ — dormant, not called
6. Annotate original PDF with color-coded highlights (`pdf_service`)
7. Generate reports in requested format(s) (`report_service`)
8. Store reports, return slim response

## Compliance Status Values

Defined in `analyzer/utils/color_constants.py`:

| Status | Color | Meaning |
|---|---|---|
| `MATCH` | green | Fully satisfied |
| `NOT_FOUND` | blue | Absent from document |
| `PARTIALLY_SATISFIED` | orange | Vague or incomplete |
| `VIOLATION` | red | Contradicts requirement |

## Storage Abstraction

`r2_service.py` provides a dual-backend layer. Set `STORAGE_BACKEND=local` for dev (stores in `annotated_pdfs/`). Set `STORAGE_BACKEND=r2` for production (Cloudflare R2 with 24-hour presigned download URLs).

## Deployment

Deployed on **Railway**. The `Procfile` defines the web process. Static files served via WhiteNoise. PostgreSQL provided via `DATABASE_URL` environment variable using `dj-database-url`.

## Agents & Skills Available

Agents in `.claude/agents/`:
- `explorer` — scan codebase before any feature work (use proactively)
- `django-reviewer` — review all changed files after implementation (use proactively)
- `migration-auditor` — audit new migrations before applying (use proactively)
- `test-runner` — write and run tests (no test suite exists yet)

Skills in `.claude/skills/`: `django-architecture`, `django-models`, `django-performance`, `django-security`, `django-testing`, `drf-api-design`

Commands in `.claude/commands/`: `/feature`, `/debug`, `/migrate`, `/review`, `/pr`, `/scaffold`, `/test`

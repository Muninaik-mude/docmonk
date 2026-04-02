# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DocMonk is a Django REST API that performs AI-powered legal document services:
- **Clause Analysis** — check uploaded document clauses for compliance, generate color-coded reports
- **Document Q&A** *(live)* — sequential chatbot for asking freeform questions on an uploaded document
- **Policy Rule Extraction** *(live)* — extract atomic policy rules from unstructured policy documents, generate interactive HTML preview
- **Policy Document Analysis** *(live)* — analyze loan/subject documents against extracted policy rules or raw policy text, generate Markdown report with compliance score
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
| `CEREBRAS_MODEL` | Cerebras model ID (default: `gpt-oss-120b`) |
| `POLICY_AI_API_KEY` | API key for policy AI provider (required in production) |
| `POLICY_AI_MODEL` | Policy AI model ID (default: `gpt-4o`) |
| `POLICY_AI_BASE_URL` | Policy AI base URL (default: `https://api.openai.com/v1`) |

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
│   ├── groq_service.py    — AI calls: jurisdiction detection, clause analysis, conflict detection, Q&A streaming
│   ├── pdf_service.py     — Text extraction (PDF/DOCX/MD/TXT), PDF annotation
│   ├── r2_service.py      — Cloudflare R2 / local file storage
│   └── report_service.py  — PDF/DOCX/Markdown report + summary generation
└── utils/
    └── color_constants.py — Compliance status → highlight/insertion color maps

qa/
├── models.py          — QADocument, QASession, QASessionDocument, QAMessage
├── serializers.py     — Input validation for session create, ask, rename, regenerate
├── views.py           — All Q&A views + SSE helpers
├── urls.py            — Q&A URL routing
├── migrations/
│   └── 0001_initial.py
└── services/
    └── qa_service.py  — Context window selection, page hint lookup, stream generators

policy/
├── models.py          — PolicyRuleExtractionJob, PolicyAnalysisJob
├── serializers.py     — PolicyRuleExtractSerializer, PolicyAnalyzerSerializer
├── views.py           — PolicyRuleExtractView, PolicyAnalyzerView
├── urls.py            — Policy URL routing
├── migrations/
└── services/
    ├── policy_service.py        — AI analysis: analyze_document_against_rules, analyze_document_against_policy
    ├── policy_rule_service.py   — AI rule extraction: extract_rules_from_policy
    ├── policy_report_service.py — Markdown report + HTML preview + summary JSON generation
    └── policy_ai_service.py     — Single OpenAI-compatible client; raises PolicyRateLimitError on 429
```

### Services

| Service | File | Responsibility |
|---|---|---|
| `pdf_service` | `analyzer/services/pdf_service.py` | Extract text from PDF, DOCX, Markdown, TXT, PPTX; annotate PDFs with highlights |
| `groq_service` | `analyzer/services/groq_service.py` | AI analysis — clause compliance, jurisdiction detection, conflict detection (dormant), Q&A streaming |
| `r2_service` | `analyzer/services/r2_service.py` | Upload/download files from Cloudflare R2 or local filesystem |
| `report_service` | `analyzer/services/report_service.py` | Generate PDF/DOCX/Markdown report + summary with color-coded results |
| `policy_service` | `policy/services/policy_service.py` | Policy document analysis — `analyze_document_against_rules` (preferred) and `analyze_document_against_policy` (legacy) |
| `policy_rule_service` | `policy/services/policy_rule_service.py` | Policy rule extraction — `extract_rules_from_policy` returns list of atomic rule objects + extraction_summary |
| `policy_report_service` | `policy/services/policy_report_service.py` | Generate Markdown analysis report, interactive HTML rule extraction preview, and summary JSON |
| `policy_ai_service` | `policy/services/policy_ai_service.py` | Single OpenAI-compatible AI client for policy. Raises `PolicyRateLimitError` on 429. |

### AI Provider Pool

**Analyzer / Q&A:** `groq_service.py` maintains a pool of 4 clients (Groq key 1, Groq key 2, Cerebras key 1, Cerebras key 2). Calls round-robin across available clients. Includes JSON repair logic for truncated responses, intelligent 5,000-char context window selection, and AI-powered relevant excerpt extraction (`extract_relevant_excerpt`).

**Policy:** `policy_ai_service.py` uses a single OpenAI-compatible client configured via `POLICY_AI_API_KEY`, `POLICY_AI_MODEL`, and `POLICY_AI_BASE_URL`. No round-robin pool. Raises `PolicyRateLimitError` on rate limit; views respond with HTTP 429 + `Retry-After` header.

**Conflict detection** (`groq_service.detect_conflicts`) is implemented but intentionally NOT called — too expensive per request. Will be wired in on demand.

## Database Models

### Clause Analyzer models

| Model | Table | Purpose |
|---|---|---|
| `AnalysisJob` | `analysis_jobs` | Top-level job: status machine, counters, stored full text |
| `JobClause` | `job_clauses` | One row per clause; state machine (PENDING → IN_PROGRESS → COMPLETED/FAILED) |
| `ClauseResult` | `clause_results` | AI output for a completed clause |
| `JobJurisdiction` | `job_jurisdiction` | Jurisdiction + applicable laws + checklist |
| `ClauseConflict` | `clause_conflicts` | Conflict pairs (populated only if conflict detection is enabled) |
| `JobReport` | `job_reports` | Stored report file keys + presigned URLs |

### Q&A models (`qa/`)

| Model | Table | Purpose |
|---|---|---|
| `QADocument` | `qa_documents` | Text extraction cache — one row per unique S3 object key. Multiple sessions can share the same extracted text without re-downloading. |
| `QASession` | `qa_sessions` | One session per user per document. Holds the full chat history for that session. |
| `QASessionDocument` | `qa_session_documents` | Junction between session and document. Always exactly 1 document per session (`MAX_DOCS_PER_SESSION = 1`). |
| `QAMessage` | `qa_messages` | One row per question/answer exchange. `answers_per_document` is always a single-element list since sessions are limited to 1 document. |

### Policy models (`policy/`)

| Model | Table | Purpose |
|---|---|---|
| `PolicyRuleExtractionJob` | `policy_rule_extraction_jobs` | Tracks a rule extraction job. Stores `extracted_rules` (JSONField list of atomic rule objects) and `full_text` cache. Status: `pending → in_progress → completed/failed`. |
| `PolicyAnalysisJob` | `policy_analysis_jobs` | Tracks a policy analysis job. Stores `policy_result` (JSONField AI output), `policy_text` (legacy raw text), `agreement_type`, `parties`, `agreement_details`, and `full_text` cache. Status: `pending → in_progress → completed/failed`. |

**Policy model fields of note:**
- `extracted_rules` on `PolicyRuleExtractionJob` — JSONField list of atomic rule objects returned from `policy_rule_service`
- `policy_result` on `PolicyAnalysisJob` — JSONField containing the full AI analysis: compliance verdict, score, per-rule analysis
- `full_text` on both models — extracted document text cached to avoid re-download on retry
- `policy_text` on `PolicyAnalysisJob` — raw policy document text, used only in the legacy path when `rules` is not provided

**Critical Q&A design constraints — do not second-guess these:**
- `document_id` = the S3 object key. S3 keys are immutable content references. The same key always means the same file. There is no scenario where two different documents share a key, or where a key's content changes. Deduplication by `document_id` is safe and correct.
- `MAX_DOCS_PER_SESSION = 1` is enforced at the serializer level. Multi-document logic does not exist and should not be designed for.
- This is a **sequential chatbot**. The UI sends one question, waits for the SSE stream to complete, then enables the next question. Concurrent `/ask` calls from the same user to the same session are not a realistic scenario.
- `answers_per_document` in `QAMessage` always contains exactly one entry. It is a list for schema flexibility only. Do not redesign it as a separate model unless multi-doc is explicitly requested.

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

### Q&A Endpoints (`/v1/qa/`)

| Method | Endpoint | Purpose | Response |
|--------|----------|---------|----------|
| `POST` | `/sessions` | Create session, download + extract document text | JSON 201 |
| `GET` | `/sessions?user_id=xxx` | List sessions for a user (paginated) | JSON 200 |
| `GET` | `/sessions/{id}` | Session detail + full chat message history | JSON 200 |
| `PATCH` | `/sessions/{id}` | Rename session | JSON 200 |
| `DELETE` | `/sessions/{id}` | Delete session + cascade messages + orphan doc cleanup | JSON 200 |
| `POST` | `/sessions/{id}/ask` | Ask a question — streams SSE answer | SSE stream |
| `POST` | `/messages/{id}/retry` | Re-run failed answer — streams SSE answer | SSE stream |
| `POST` | `/messages/{id}/regenerate` | Regenerate with user feedback reason — streams SSE answer | SSE stream |

### Policy Endpoints (`/v1/policy/`)

| Method | Endpoint | Purpose | Response |
|--------|----------|---------|----------|
| `POST` | `/v1/policy/extract-rules` | Extract atomic rules from a policy document, return rules + interactive HTML preview | JSON 200 |
| `POST` | `/v1/policy/analyze` | Analyze a subject document against policy rules (or raw policy text), return Markdown report + analysis JSON | JSON 200 |

#### POST `/v1/policy/extract-rules`

**Request:**
```json
{
  "document_base64": "<base64>",
  "document_filename": "policy.pdf",
  "policy_type": "Personal Loan"
}
```
`document_presigned_url` accepted as alternative to `document_base64`.

**Response (200):**
```json
{
  "job_id": "uuid",
  "status": "completed",
  "policy_type": "Personal Loan",
  "document_filename": "policy.pdf",
  "extraction_summary": { "total_rules": 12, "categories": ["Eligibility", "Documentation"] },
  "rules": [ { ... } ],
  "rule_document_html_base64": "<base64-encoded interactive HTML>"
}
```

**Errors:** 400 (invalid/missing input), 413 (file > 100 MB), 429 (AI rate limit — includes `Retry-After` header), 500 (AI failure).

#### POST `/v1/policy/analyze`

**Request:**
```json
{
  "document_base64": "<base64>",
  "document_filename": "loan_application.pdf",
  "policy_type": "Personal Loan",
  "rules": [ { ... } ],
  "agreement_type": "Personal Loan Agreement",
  "agreement_details": {},
  "parties": {}
}
```
`policy_text` (raw text string) accepted instead of `rules` for legacy use. Either `rules` or `policy_text` is required.

**Response (200):**
```json
{
  "job_id": "uuid",
  "status": "completed",
  "report_md_base64": "<base64-encoded Markdown report>",
  "policy_analysis": {
    "compliance_verdict": "...",
    "compliance_score": 85,
    "per_rule_analysis": [ { ... } ]
  }
}
```
`status` may be `"partial"` if report generation failed (response also includes `report_error` field). Full analysis JSON still returned.

**Errors:** 400, 413, 429 (+ `Retry-After`), 500.

**SSE event format:**
- Text chunks: `data: "word or phrase"\n\n` (JSON-encoded string — call `JSON.parse(event.data)`)
- End of stream (saved): `event: done\ndata: {"success":true}\n\n`
- Partial (stream cut short, partial saved): `event: partial\ndata: {...}\n\n`
- Save failed: `event: error\ndata: {"success":false,...}\n\n`

## Analysis Pipeline (Clause Analyzer)

1. Decode base64 / download document from presigned URL
2. Extract full text (`pdf_service`)
3. Detect jurisdiction + applicable laws (`groq_service.detect_jurisdiction`) — runs before clause analysis; merged to reduce round-trips
4. Analyze each clause in **parallel** — 3 workers via `ThreadPoolExecutor` (`groq_service.analyze_clause_against_pdf`)
5. ~~Detect cross-clause conflicts~~ — dormant, not called (`groq_service.detect_conflicts` exists but is intentionally skipped)
6. If `report_format` includes PDF: annotate original PDF with color-coded highlights (`pdf_service`)
7. Generate reports in requested format(s) — `"markdown"` (default), `"pdf"`, `"docx"`, or `"both"` (`report_service`)
8. Upload PDF/DOCX to R2 storage (skipped for Markdown — returned inline as base64)
9. Return slim response: `job_id`, `status`, `progress`, `report_md_base64`, `summary_md_base64`

## Q&A Pipeline

### Session creation (`POST /v1/qa/sessions`)
1. Check `QADocument` cache by `document_id` (= S3 object key) — skip download if already extracted
2. Cache miss: download from presigned URL, extract text, build `char_page_map`
3. Create `QASession` + `QASessionDocument` in one atomic block
4. Return `session_id`

### Ask flow (`POST /v1/qa/sessions/{id}/ask`)
1. Load session + document via `prefetch_related`
2. `qa_service.find_relevant_window(question, full_text)` — 5000-char sliding window keyword scan
3. Fetch last 10 Q&A exchanges as conversation history (oldest first) — passed to AI for multi-turn context
4. `groq_service.answer_question_stream(question, context, history)` — yields text chunks
5. View wraps each chunk as SSE `data:` event and flushes immediately
6. After all chunks: `groq_service.extract_relevant_excerpt(question, context)` — AI extracts verbatim relevant passage from context (offloaded to gevent threadpool)
7. `qa_service.find_page_for_excerpt(relevant_excerpt, full_text, char_page_map)` — resolves 1-based page number
8. Save `QAMessage` atomically, auto-name session from first question
9. Emit `event: done` on success, `event: partial` if stream cut short, `event: error` if DB save failed

### Retry / Regenerate
- **Retry**: re-runs AI only for answers with `status in ("failed", "partial")`, merges results back
- **Regenerate**: re-runs AI with `previous_answer + user reason` as additional context, replaces answer in-place

## Policy Pipeline

### Rule Extraction (`POST /v1/policy/extract-rules`)
1. Create `PolicyRuleExtractionJob` (status: `in_progress`)
2. Resolve document bytes (base64 decode or download presigned URL)
3. Validate file size (max 100 MB)
4. Extract text via `pdf_service`
5. Call `policy_rule_service.extract_rules_from_policy(full_text, policy_type, filename)` → list of atomic rule objects + extraction_summary
6. Generate interactive HTML preview via `policy_report_service.generate_rule_extraction_report()`
7. Save `extracted_rules` to DB, update job status → `completed`
8. Return response with rules, extraction_summary, and base64-encoded HTML preview

### Policy Analysis (`POST /v1/policy/analyze`)
1. Create `PolicyAnalysisJob` (status: `in_progress`)
2. Resolve document bytes
3. Validate file size
4. Extract text via `pdf_service`
5. Call AI analysis:
   - If `rules` provided (preferred): `policy_service.analyze_document_against_rules(full_text, policy_type, rules)`
   - Else (legacy): `policy_service.analyze_document_against_policy(full_text, policy_type, policy_text)`
6. Generate Markdown report via `policy_report_service.generate_policy_report()`
7. Build summary JSON via `policy_report_service.build_policy_summary_json()` and merge into analysis result
8. Save `policy_result` to DB, update job status → `completed`
9. Return response with `report_md_base64` and `policy_analysis`
   - If report generation fails: status → `"partial"`, `report_error` field added, analysis JSON still returned

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

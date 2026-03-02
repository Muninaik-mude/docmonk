# DocMonk — Product & API Specification

> Last updated: 2026-03-02

---

## 1. Product Overview

DocMonk is an AI-powered legal document backend. It provides three core services:

| # | Service | Status |
|---|---|---|
| 1 | **Clause Analysis** — check contract clauses for compliance against an uploaded document | ✅ Live |
| 2 | **Document Q&A** — ask freeform questions on an uploaded document | 🔜 Planned |
| 3 **Contract Generation** — generate a full contract document from structured input | 🔜 Planned |

---

## 2. Current API — Clause Analysis

### POST `/v1/analyze`

Accepts a base64-encoded document and a list of clauses. Runs full AI analysis pipeline.

#### Request Body

```json
{
  "document_base64": "<base64-encoded PDF/DOCX/MD/TXT>",
  "document_filename": "contract.pdf",
  "agreement_type": "Commercial Rental Agreement",
  "agreement_details": {
    "agreement_date": "2026-01-01",
    "city": "Mumbai",
    "state": "Maharashtra"
  },
  "parties": {
    "landlord": { "name": "...", "address": "...", "contact": "..." },
    "tenant":   { "name": "...", "company_name": "...", "address": "...", "contact": "..." }
  },
  "property": {
    "type": "Commercial",
    "area_sqft": 5000,
    "address": "..."
  },
  "clauses": [
    {
      "id": "confidentiality_survival_clause",
      "category": "confidentiality",
      "title": "Post-Termination Confidentiality",
      "value": "Confidentiality obligations shall survive termination..."
    }
  ]
}
```

**Notes:**
- `document_base64` preferred. `document_presigned_url` / `pdf_presigned_url` also accepted.
- Report format is always **Markdown**. No PDF or DOCX generation.
- Max 100 clauses per request. Max document size 100 MB.

#### Response (slim — optimized for speed)

```json
{
  "job_id": "uuid",
  "status": "completed",
  "can_resume": false,
  "progress": { "total": 5, "completed": 5, "failed": 0 },
  "report_md_base64": "<base64-encoded markdown report>",
  "summary_md_base64": "<base64-encoded markdown summary>"
}
```

**Design decision:** `analysis_summary` and report URLs are excluded from POST response.
Full results (including `analysis_summary` with `jurisdiction` + `clauses_analysis` dict) are available via `GET /v1/jobs/{job_id}`. This keeps POST fast for the caller.

---

### GET `/v1/jobs/{job_id}`

Poll job status and retrieve full results.

#### Response

```json
{
  "job_id": "uuid",
  "status": "completed",
  "can_resume": false,
  "progress": { "total": 5, "completed": 5, "failed": 0 },
  "analysis_summary": {
    "jurisdiction": {
      "jurisdiction": "Maharashtra, India",
      "agreement_type": "Commercial Rental Agreement",
      "applicable_laws": ["Transfer of Property Act, 1882"],
      "checklist": []
    },
    "clauses_analysis": {
      "confidentiality_survival_clause": {
        "clause_title": "Post-Termination Confidentiality",
        "clause_value": "...",
        "clause_category": "confidentiality",
        "result": "MATCH",
        "reason": "...",
        "relevant_text": "...",
        "color": null,
        "ai_added_text": null,
        "parties_obligated": ["Receiving Party"],
        "missing_values": [],
        "binding_strength": "MUST/SHALL",
        "key_dates_durations": [],
        "analysis_status": "COMPLETED",
        "retry_count": 0
      },
      "rent_payment_clause": {
        "clause_title": "Monthly Rent",
        "clause_value": "...",
        "clause_category": "financial",
        "result": "VIOLATION",
        "reason": "...",
        "relevant_text": "...",
        "color": "red",
        "ai_added_text": "Rent amount should be explicitly stated.",
        "parties_obligated": ["Tenant"],
        "missing_values": ["rent amount"],
        "binding_strength": "MUST/SHALL",
        "key_dates_durations": [],
        "analysis_status": "COMPLETED",
        "retry_count": 0
      }
    }
  },
  "report_pdf_url": "https://...",
  "report_markdown_url": "https://...",
  "created_at": "2026-03-02T10:00:00Z",
  "completed_at": "2026-03-02T10:00:45Z"
}
```

**Shape rationale:**
- `jurisdiction` sits once at `analysis_summary` level — not duplicated per clause
- `clauses_analysis` is a dict keyed by `clause_id` — O(1) client lookup vs O(n) list scan
- `clause_id` is the key, so it is not repeated inside the object (saves payload bytes)

---

### POST `/v1/jobs/{job_id}/resume`

Re-run only FAILED or PENDING clauses. Reuses extracted text from DB — no re-download.
Only callable when `status == "PARTIAL_FAILURE"`.

---

## 3. Document Q&A API

**Session-based.** Upload a document once → get a `session_id` → ask as many questions as needed. Q&A history stored in DB per session.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/qa/sessions` | Create session — upload doc or reuse job_id |
| `GET` | `/v1/qa/sessions/{session_id}` | Get session info + full Q&A history |
| `POST` | `/v1/qa/sessions/{session_id}/ask` | Ask 1 or more questions |

---

### POST `/v1/qa/sessions`

Upload a document and create a Q&A session. Text is extracted once and stored.

#### Option A — Upload new document

```json
{
  "document_base64": "<base64-encoded PDF/DOCX/MD/TXT>",
  "document_filename": "contract.pdf"
}
```

#### Option B — Reuse an existing analysis job (no re-upload)

```json
{
  "job_id": "uuid"
}
```

Copies `full_text` from the `AnalysisJob` record. No extraction cost.

#### Response

```json
{
  "session_id": "uuid",
  "document_filename": "contract.pdf",
  "file_type": "pdf",
  "char_count": 18400,
  "created_at": "2026-03-02T10:00:00Z"
}
```

---

### POST `/v1/qa/sessions/{session_id}/ask`

Ask one or more questions. Questions are processed in **parallel** (same pattern as clause analysis). Each question gets its own AI call with a focused context window.

#### Request Body

```json
{
  "questions": [
    "What is the notice period for termination?",
    "Does this contract have an auto-renewal clause?",
    "What are the penalties for late rent payment?"
  ]
}
```

Single question is also valid — just send a list with one entry.

#### Response

```json
{
  "session_id": "uuid",
  "answers": [
    {
      "question": "What is the notice period for termination?",
      "answer": "The contract specifies a 30-day written notice period for termination by either party.",
      "relevant_excerpt": "Either party may terminate this agreement by providing 30 days written notice to the other party.",
      "page_hint": 4
    },
    {
      "question": "Does this contract have an auto-renewal clause?",
      "answer": "Yes. The agreement auto-renews for successive 12-month terms unless either party provides 60 days written notice before the expiry date.",
      "relevant_excerpt": "This agreement shall automatically renew for successive periods of twelve (12) months...",
      "page_hint": 7
    }
  ]
}
```

**`page_hint`** — 1-based page number where the relevant excerpt was located. `null` if not locatable.

---

### GET `/v1/qa/sessions/{session_id}`

Retrieve session metadata and full Q&A history.

#### Response

```json
{
  "session_id": "uuid",
  "document_filename": "contract.pdf",
  "file_type": "pdf",
  "char_count": 18400,
  "created_at": "2026-03-02T10:00:00Z",
  "interactions": [
    {
      "id": "uuid",
      "question": "What is the notice period for termination?",
      "answer": "The contract specifies a 30-day written notice period...",
      "relevant_excerpt": "...",
      "page_hint": 4,
      "asked_at": "2026-03-02T10:05:00Z"
    }
  ]
}
```

---

### DB Models

#### `QASession`
| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Session identifier |
| `source_job` | FK → `AnalysisJob` nullable | Set when created from job_id |
| `document_filename` | CharField | Original filename |
| `file_type` | CharField | pdf / docx / md / txt |
| `full_text` | TextField | Extracted document text |
| `char_count` | IntegerField | Length of full_text |
| `created_at` | DateTimeField | Auto |

#### `QAInteraction`
| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `session` | FK → `QASession` | CASCADE |
| `question` | TextField | Question as asked |
| `answer` | TextField | AI answer |
| `relevant_excerpt` | TextField | Document excerpt used |
| `page_hint` | IntegerField nullable | 1-based page number |
| `ai_provider_used` | CharField | Which provider answered |
| `asked_at` | DateTimeField | Auto |

---

### AI Pipeline (per question)

1. Keyword extraction from question (nouns, legal terms)
2. Scan `full_text` for most relevant 5,000-char window centered on keywords — same logic as `groq_service.find_text_location_in_pdf`
3. Single AI call: system = legal QA expert, user = question + excerpt
4. Parse AI response → `answer`, `relevant_excerpt`, `page_hint`
5. Persist as `QAInteraction`, return in response

**Parallel execution:** when caller sends N questions, N AI calls run in `ThreadPoolExecutor` (max workers = 3, same pool as clause analysis).

---

### New Service File

`analyzer/services/qa_service.py`

```python
def answer_question(question: str, full_text: str, text_blocks: list) -> dict:
    """
    Single question → AI answer.
    Returns: { answer, relevant_excerpt, page_hint }
    """
```

---

### URL Registration

```python
# analyzer/urls.py additions
path('qa/sessions',                        QASessionView.as_view(),   name='qa-session-create'),
path('qa/sessions/<uuid:session_id>',      QASessionDetailView.as_view(), name='qa-session-detail'),
path('qa/sessions/<uuid:session_id>/ask',  QAAskView.as_view(),       name='qa-ask'),
```

---

## 4. Planned API — Contract Generation

### POST `/v1/contracts/generate`

Generate a complete, legally-structured contract document from structured input. Returns the generated contract as a base64-encoded file.

#### Use Cases
- Generate a Commercial Rental Agreement between two parties
- Generate an NDA based on party names and scope
- Generate an Employment Agreement with specific clauses

#### Request Body

```json
{
  "agreement_type": "Commercial Rental Agreement",
  "language": "en",
  "agreement_details": {
    "agreement_date": "2026-04-01",
    "city": "Mumbai",
    "state": "Maharashtra",
    "duration_months": 24,
    "start_date": "2026-04-01"
  },
  "parties": {
    "landlord": {
      "name": "Ravi Kumar",
      "address": "123 MG Road, Mumbai",
      "contact": "+91-9876543210"
    },
    "tenant": {
      "name": "TechCorp Pvt Ltd",
      "company_name": "TechCorp Pvt Ltd",
      "authorized_signatory": "Priya Shah",
      "address": "456 BKC, Mumbai",
      "contact": "+91-9123456789"
    }
  },
  "property": {
    "type": "Commercial Office",
    "area_sqft": 2500,
    "address": "Unit 12, Tech Park, Andheri East, Mumbai"
  },
  "clauses": [
    {
      "id": "rent_payment",
      "category": "financial",
      "title": "Monthly Rent",
      "value": "Monthly rent of INR 1,50,000 payable by the 5th of each month."
    }
  ],
  "output_format": "docx"
}
```

**Notes:**
- `clauses` are optional — if omitted, AI generates standard clauses for the agreement type
- `output_format`: `"pdf"` | `"docx"` | `"markdown"`
- `language`: `"en"` (default) — future: `"hi"`, `"mr"` etc.

#### Response

```json
{
  "contract_id": "uuid",
  "agreement_type": "Commercial Rental Agreement",
  "output_format": "docx",
  "document_base64": "<base64-encoded DOCX>",
  "clauses_generated": 12,
  "jurisdiction_detected": "Maharashtra, India"
}
```

#### Pipeline

1. Validate request (parties, agreement_type required)
2. Detect jurisdiction from state/city
3. AI call 1: generate standard clause list for this agreement type + jurisdiction (if clauses not provided)
4. AI call 2: expand each clause into full legal text incorporating party names, property details, dates
5. Compose full document (DOCX/PDF/Markdown) via `report_service`
6. Return base64-encoded document

#### Design Decisions (TBD — to finalize before implementation)
- Store generated contracts in DB? (`GeneratedContract` model with parties, clauses, output)
- R2 storage for generated files?
- Template-based generation vs pure AI generation?
- Clause library — predefined standard clauses per agreement type?

---

## 5. Response Time Optimization Strategies

### Current bottlenecks (POST /v1/analyze)
1. Sequential jurisdiction detection before parallel clause analysis
2. Report generation (PDF especially) after AI analysis
3. File upload to R2 after report generation
4. Large response payload (analysis_summary) serialization

### Applied strategies
| Strategy | Status | Impact |
|---|---|---|
| Parallel clause analysis (ThreadPoolExecutor, 3 workers) | ✅ Done | High |
| Multi-provider AI pool (4 clients, round-robin) | ✅ Done | Medium |
| Slim POST response (no analysis_summary, no URLs) | ✅ Done | Medium |
| 5,000-char context window per clause (not full doc) | ✅ Done | High |
| Merged jurisdiction + clause AI calls | ✅ Done | Medium |
| Store full_text in DB (resume without re-download) | ✅ Done | Medium |

### Future strategies to consider
| Strategy | Complexity | Impact |
|---|---|---|
| Increase parallel workers to 5–8 (monitor rate limits) | Low | Medium |
| Skip PDF annotation on first call, generate lazily via GET | Low | Medium |
| Async job processing (Celery/RQ) — POST returns `job_id` immediately, client polls | High | Very High |
| Jurisdiction cache by document hash (same doc → skip detection) | Medium | Low |
| Stream AI responses and process chunks | High | Medium |
| Pre-warm AI clients on server startup | Low | Low |

---

## 6. Data Models Summary

```
AnalysisJob          — top-level job (status, full_text, counters)
  └── JobClause[]    — one per clause (state machine)
        └── ClauseResult  — AI output (result, reason, parties, dates...)
  └── JobJurisdiction      — jurisdiction + laws + checklist
  └── ClauseConflict[]     — conflict pairs (dormant)
  └── JobReport[]          — stored report files
```

---

## 7. Compliance Status Reference

| Status | Color | Meaning |
|---|---|---|
| `MATCH` | green | Clause fully satisfied in document |
| `NOT_FOUND` | blue | Clause absent from document |
| `PARTIALLY_SATISFIED` | orange | Clause present but vague/incomplete |
| `VIOLATION` | red | Document contradicts the clause |

---

## 8. Deployment

- **Platform:** Railway
- **Process:** `gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 2 --timeout 120`
- **DB:** PostgreSQL via `DATABASE_URL` env var
- **Static files:** WhiteNoise
- **Storage:** Cloudflare R2 (`STORAGE_BACKEND=r2`) or local fallback
